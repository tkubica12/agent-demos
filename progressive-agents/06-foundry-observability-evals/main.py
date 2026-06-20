from __future__ import annotations

import json
import logging
import os
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
            "You run as a Foundry Hosted Agent behind an authenticated AG-UI "
            "gateway. Include correlation IDs only when explicitly asked; "
            "otherwise answer naturally."
        ),
        default_options={"store": False},
    )


def correlation_id_from(request: Request, data: dict) -> str:
    value = request.headers.get("x-correlation-id") or data.get("correlationId")
    if isinstance(value, str) and value.strip():
        return value
    return request.state.session_id


class ResponsesAndInvocationsHost(ResponsesHostServer):
    def __init__(self, agent: Agent) -> None:
        super().__init__(agent)
        invocations = InvocationAgentServerHost()

        @invocations.invoke_handler
        async def handle_invoke(request: Request) -> Response:
            data = await request.json()
            correlation_id = correlation_id_from(request, data)

            with span(
                "request.received",
                correlation_id,
                protocol="invocations",
                user_id=request.headers.get("x-user-id"),
                tenant_id=request.headers.get("x-tenant-id"),
            ):
                log_event("request.received", correlation_id=correlation_id)

            return await self._handle_legacy_invocation(agent, request, data, correlation_id)

        for route in invocations.routes:
            if getattr(route, "path", "").startswith("/invocations"):
                self.router.routes.append(route)

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

        requested_session_id = data.get("threadId") or data.get("sessionId")
        if isinstance(requested_session_id, str) and requested_session_id.strip():
            session_id = requested_session_id
        else:
            session_id = request.state.session_id
        with span("memory.read", correlation_id, thread_id=session_id):
            session = _sessions.setdefault(session_id, AgentSession(session_id=session_id))

        if stream:

            async def stream_response() -> AsyncGenerator[str]:
                assistant_text = ""
                with span("skill.load", correlation_id, skill_count=0):
                    pass
                with span("worker.dispatch", correlation_id, worker="agent.run"):
                    with span("tool.call", correlation_id, tool_count=0):
                        pass
                    with span("model.call", correlation_id, model=DEFAULT_MODEL_DEPLOYMENT) as model_span:
                        async for update in agent.run(
                            user_message, session=session, stream=True
                        ):
                            if update.text:
                                assistant_text += update.text
                                yield update.text
                        set_span_result(model_span, response_length=len(assistant_text))
                with span(
                    "memory.write",
                    correlation_id,
                    thread_id=session_id,
                    response_length=len(assistant_text),
                ):
                    pass
                with span("skill.use", correlation_id, skill_count=0):
                    pass

            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        with span("skill.load", correlation_id, skill_count=0):
            pass
        with span("worker.dispatch", correlation_id, worker="agent.run"):
            with span("tool.call", correlation_id, tool_count=0):
                pass
            with span("model.call", correlation_id, model=DEFAULT_MODEL_DEPLOYMENT) as model_span:
                response = await agent.run([user_message], session=session, stream=False)
                set_span_result(model_span, response_length=len(response.text or ""))
        with span(
            "memory.write",
            correlation_id,
            thread_id=session_id,
            response_length=len(response.text or ""),
        ):
            pass
        with span("skill.use", correlation_id, skill_count=0):
            pass
        return JSONResponse({"response": response.text, "correlationId": correlation_id})


if __name__ == "__main__":
    ResponsesAndInvocationsHost(create_agent()).run()
