from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from typing import Any

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from opentelemetry import trace
from opentelemetry.trace import Span
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
SERVICE_NAME = "step-06-foundry-observability-evals"
PERSONALITY_INSTRUCTIONS = (
    "You are a professional, calm, friendly, and concise assistant. "
    "Use plain language. Be warm without being overly excited. "
    "Do not sound cold, hype-driven, sarcastic, or theatrical. "
    "When unsure, acknowledge uncertainty briefly and suggest the next practical step."
)
_sessions: dict[str, AgentSession] = {}
_telemetry_configured = False

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(os.getenv("APP_LOG_LEVEL", "INFO"))
tracer = trace.get_tracer(SERVICE_NAME)


def create_credential() -> DefaultAzureCredential:
    running_in_azure = any(
        os.getenv(name)
        for name in ("AGENT_NAME", "AZURE_CLIENT_ID", "MSI_ENDPOINT", "IDENTITY_ENDPOINT")
    )
    return DefaultAzureCredential(exclude_managed_identity_credential=not running_in_azure)


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def log_event(event: str, **fields: Any) -> None:
    logger.info(
        json.dumps(
            {"event": event, "service": SERVICE_NAME, **fields},
            separators=(",", ":"),
            default=str,
        )
    )


def configure_telemetry() -> None:
    global _telemetry_configured
    if _telemetry_configured:
        return

    env_connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    connection_string = None
    try:
        project_client = AIProjectClient(
            endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
            credential=create_credential(),
        )
        connection_string = project_client.telemetry.get_application_insights_connection_string()
    except Exception as exc:
        log_event("telemetry.project_connection_unavailable", error=str(exc))

    if not connection_string:
        connection_string = env_connection_string

    if connection_string:
        os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = connection_string
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            connection_string=connection_string,
            service_name=SERVICE_NAME,
        )
        log_event("telemetry.azure_monitor_configured")
    else:
        log_event("telemetry.local_only")

    _telemetry_configured = True


@contextmanager
def span(name: str, correlation_id: str, **attributes: Any):
    with tracer.start_as_current_span(name) as current_span:
        current_span.set_attribute("service.name", SERVICE_NAME)
        current_span.set_attribute("correlation.id", correlation_id)
        for key, value in attributes.items():
            if value is not None:
                current_span.set_attribute(key, value)
        yield current_span


def set_span_result(current_span: Span, **attributes: Any) -> None:
    for key, value in attributes.items():
        if value is not None:
            current_span.set_attribute(key, value)


def create_agent() -> Agent:
    configure_telemetry()
    client = FoundryChatClient(
        project_endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        model=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL_DEPLOYMENT),
        credential=create_credential(),
    )

    return Agent(
        client=client,
        name="FoundryObservabilityEvalsAgent",
        instructions=(
            f"{PERSONALITY_INSTRUCTIONS} "
            "You are exposed through Foundry Hosted Agent Invocations using "
            "AG-UI-shaped Server-Sent Events. Include correlation IDs only when "
            "explicitly asked; otherwise answer naturally."
        ),
        default_options={"store": False},
    )


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def latest_user_text(messages: list[dict]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
    return None


def correlation_id_from(request: Request, data: dict) -> str:
    value = request.headers.get("x-correlation-id") or data.get("correlationId")
    if isinstance(value, str) and value.strip():
        return value
    return str(uuid.uuid4())


class ResponsesAndInvocationsHost(ResponsesHostServer):
    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        invocations = InvocationAgentServerHost()

        @invocations.invoke_handler
        async def handle_invoke(request: Request) -> Response:
            data = await request.json()
            correlation_id = correlation_id_from(request, data)
            messages = data.get("messages")

            with span(
                "request.received",
                correlation_id,
                protocol="invocations",
                has_agui_messages=isinstance(messages, list),
            ):
                log_event("request.received", correlation_id=correlation_id)

            if isinstance(messages, list):
                return await self._handle_agui_invocation(
                    agent, request, data, messages, correlation_id
                )

            return await self._handle_legacy_invocation(agent, request, data, correlation_id)

        for route in invocations.routes:
            if getattr(route, "path", "").startswith("/invocations"):
                self.router.routes.append(route)

    async def _handle_agui_invocation(
        self,
        agent: Agent,
        request: Request,
        data: dict,
        messages: list[dict],
        correlation_id: str,
    ) -> Response:
        user_message = latest_user_text(messages)
        if user_message is None:
            log_event("request.invalid", correlation_id=correlation_id, reason="missing_user")
            return Response("Missing user message in AG-UI messages", status_code=400)

        thread_id = data.get("threadId")
        if not isinstance(thread_id, str) or not thread_id.strip():
            thread_id = getattr(request.state, "session_id", str(uuid.uuid4()))
        run_id = data.get("runId")
        if not isinstance(run_id, str) or not run_id.strip():
            run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())

        with span("memory.read", correlation_id, thread_id=thread_id):
            session = _sessions.setdefault(thread_id, AgentSession(session_id=thread_id))

        async def stream_agui() -> AsyncGenerator[str]:
            log_event(
                "agui.run_started",
                correlation_id=correlation_id,
                thread_id=thread_id,
                run_id=run_id,
            )
            yield sse(
                {
                    "type": "RUN_STARTED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "correlationId": correlation_id,
                }
            )
            yield sse(
                {
                    "type": "TEXT_MESSAGE_START",
                    "messageId": message_id,
                    "role": "assistant",
                    "correlationId": correlation_id,
                }
            )

            assistant_text = ""
            try:
                with span("skill.load", correlation_id, skill_count=0):
                    pass
                with span("worker.dispatch", correlation_id, worker="agent.run"):
                    with span("tool.call", correlation_id, tool_count=0):
                        pass
                    with span(
                        "model.call",
                        correlation_id,
                        model=os.getenv(
                            "AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL_DEPLOYMENT
                        ),
                    ) as model_span:
                        async for update in agent.run(
                            user_message, session=session, stream=True
                        ):
                            if update.text:
                                assistant_text += update.text
                                yield sse(
                                    {
                                        "type": "TEXT_MESSAGE_CONTENT",
                                        "messageId": message_id,
                                        "delta": update.text,
                                        "correlationId": correlation_id,
                                    }
                                )
                        set_span_result(model_span, response_length=len(assistant_text))
            except Exception as exc:
                log_event("agui.run_error", correlation_id=correlation_id, error=str(exc))
                yield sse(
                    {
                        "type": "RUN_ERROR",
                        "message": str(exc),
                        "correlationId": correlation_id,
                    }
                )
                return

            with span(
                "memory.write",
                correlation_id,
                thread_id=thread_id,
                response_length=len(assistant_text),
            ):
                pass
            with span("skill.use", correlation_id, skill_count=0):
                pass

            yield sse(
                {
                    "type": "TEXT_MESSAGE_END",
                    "messageId": message_id,
                    "correlationId": correlation_id,
                }
            )
            yield sse(
                {
                    "type": "RUN_FINISHED",
                    "threadId": thread_id,
                    "runId": run_id,
                    "correlationId": correlation_id,
                }
            )
            log_event(
                "agui.run_finished",
                correlation_id=correlation_id,
                thread_id=thread_id,
                run_id=run_id,
                response_length=len(assistant_text),
            )

        return StreamingResponse(
            stream_agui(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "x-correlation-id": correlation_id,
            },
        )

    async def _handle_legacy_invocation(
        self,
        agent: Agent,
        request: Request,
        data: dict,
        correlation_id: str,
    ) -> Response:
        stream = data.get("stream", False)
        user_message = data.get("message")
        if not isinstance(user_message, str) or not user_message.strip():
            log_event("request.invalid", correlation_id=correlation_id, reason="missing_message")
            return Response("Missing non-empty 'message' in request", status_code=400)

        session_id = request.state.session_id
        with span("memory.read", correlation_id, thread_id=session_id):
            session = _sessions.setdefault(session_id, AgentSession(session_id=session_id))

        if stream:

            async def stream_response() -> AsyncGenerator[str]:
                with span("model.call", correlation_id, model=DEFAULT_MODEL_DEPLOYMENT):
                    async for update in agent.run(
                        user_message, session=session, stream=True
                    ):
                        if update.text:
                            yield update.text

            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        with span("model.call", correlation_id, model=DEFAULT_MODEL_DEPLOYMENT):
            response = await agent.run([user_message], session=session, stream=False)
        with span("memory.write", correlation_id, thread_id=session_id):
            pass
        return JSONResponse({"response": response.text, "correlationId": correlation_id})


if __name__ == "__main__":
    ResponsesAndInvocationsHost(create_agent()).run()
