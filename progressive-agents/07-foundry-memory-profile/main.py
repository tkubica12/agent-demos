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

from memory_store import store


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
SERVICE_NAME = "step-07-foundry-memory-profile"
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


def optional_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc


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
    memory_store_name = os.getenv("MEMORY_STORE_NAME", "step-07-memory-profile")
    memory_scope = os.getenv("MEMORY_SCOPE", "{{$userId}}")
    memory_update_delay = optional_int_env("MEMORY_UPDATE_DELAY_SECONDS", 1)
    memory_default_user_id = os.getenv("MEMORY_DEFAULT_USER_ID", "playground-user")
    memory_tool = FoundryChatClient.get_memory_search_tool(
        memory_store_name=memory_store_name,
        scope=memory_scope,
        update_delay=memory_update_delay,
    )
    # Agent Framework currently serializes plain dict tools for Foundry preview tools.
    tools = [memory_tool.as_dict()]
    log_event(
        "memory.foundry_tool_configured",
        memory_store_name=memory_store_name,
        memory_scope=memory_scope,
        memory_update_delay=memory_update_delay,
        memory_default_user_id=memory_default_user_id,
    )

    return Agent(
        client=client,
        name="FoundryMemoryProfileAgent",
        instructions=(
            f"{PERSONALITY_INSTRUCTIONS} "
            "You run as a Foundry Hosted Agent behind an authenticated AG-UI "
            "gateway. You receive compact persistent user context before the "
            "current user message. Use it quietly and naturally. "
            "For profile mutations, prefer explicit profile patch operations "
            "provided by the host API. Include correlation IDs only when "
            "explicitly asked; otherwise answer naturally."
        ),
        tools=tools,
        default_options={
            "store": False,
            "extra_headers": {"x-memory-user-id": memory_default_user_id},
        },
    )


def correlation_id_from(request: Request, data: dict) -> str:
    value = request.headers.get("x-correlation-id") or data.get("correlationId")
    if isinstance(value, str) and value.strip():
        return value
    return request.state.session_id


def user_id_from(request: Request, data: dict) -> str:
    user = data.get("user")
    if isinstance(user, dict):
        value = user.get("id")
        if isinstance(value, str) and value.strip():
            return value
    value = request.headers.get("x-memory-user-id") or request.headers.get("x-user-id")
    if value and value.strip():
        return value
    return "local-user"


def profile_patch_from(data: dict) -> dict[str, Any]:
    patch = data.get("patch")
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object.")
    return patch


def handle_memory_action(user_id: str, session_id: str, data: dict) -> JSONResponse | None:
    action = data.get("action")
    if not isinstance(action, str):
        return None
    source = data.get("source")
    source = source if isinstance(source, str) and source.strip() else "invocations"

    if action == "get_profile":
        return JSONResponse({"profile": store.get_profile(user_id)})
    if action == "propose_profile_patch":
        proposal = store.propose_profile_patch(user_id, profile_patch_from(data), source)
        return JSONResponse({"proposal": proposal})
    if action == "apply_profile_patch":
        profile = store.apply_profile_patch(user_id, profile_patch_from(data), source)
        return JSONResponse({"profile": profile})
    if action == "delete_profile_item":
        path = data.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string.")
        deleted = store.delete_profile_item(user_id, path, source)
        return JSONResponse({"deleted": deleted})
    if action == "remember":
        content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string.")
        item = store.create_memory(user_id, content.strip(), "direct", {"source": source})
        return JSONResponse({"memory": item})
    if action == "forget":
        query = data.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string.")
        removed = store.forget_memory(user_id, query.strip())
        return JSONResponse({"removed": removed})
    if action == "search_memories":
        query = data.get("query")
        limit = data.get("limit", 5)
        if query is not None and not isinstance(query, str):
            raise ValueError("query must be a string.")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer.")
        items = store.search_memories(user_id, query, limit=limit)
        return JSONResponse({"memories": items})
    if action == "list_conversations":
        return JSONResponse({"conversations": store.list_conversations(user_id)})
    if action == "get_conversation":
        conversation_id = data.get("conversationId") or session_id
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversationId must be a non-empty string.")
        conversation = store.get_conversation(user_id, conversation_id)
        if conversation is None:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        return JSONResponse({"conversation": conversation})
    if action == "summarize_conversation":
        conversation_id = data.get("conversationId") or session_id
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("conversationId must be a non-empty string.")
        return JSONResponse(store.summarize_conversation(user_id, conversation_id))
    if action == "list_audit":
        return JSONResponse({"audit": store.list_audit(user_id)})
    raise ValueError(f"Unsupported action: {action}")


def enrich_message_for_memory(user_id: str, user_message: str) -> str:
    context = store.get_memory_context(user_id, user_message)
    return f"{context.to_prompt()}\n\nCurrent user message:\n{user_message}"


def foundry_memory_options(user_id: str) -> dict[str, Any]:
    return {"extra_headers": {"x-memory-user-id": user_id}}


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
        requested_session_id = data.get("threadId") or data.get("sessionId")
        if isinstance(requested_session_id, str) and requested_session_id.strip():
            session_id = requested_session_id
        else:
            session_id = request.state.session_id
        stream = data.get("stream", False)
        user_id = user_id_from(request, data)
        try:
            action_response = handle_memory_action(user_id, session_id, data)
        except (KeyError, ValueError) as exc:
            log_event("memory.action_invalid", correlation_id=correlation_id, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
        if action_response is not None:
            return action_response

        user_message = data.get("message")
        if not isinstance(user_message, str) or not user_message.strip():
            log_event("request.invalid", correlation_id=correlation_id, reason="missing_message")
            return Response("Missing non-empty 'message' in request", status_code=400)

        with span("memory.read", correlation_id, thread_id=session_id):
            session = _sessions.setdefault(session_id, AgentSession(session_id=session_id))
            log_event("memory.scope_resolved", correlation_id=correlation_id, user_id=user_id, thread_id=session_id)
            store.add_message(
                user_id,
                session_id,
                "user",
                user_message,
                {"correlationId": correlation_id},
            )
            enriched_message = enrich_message_for_memory(user_id, user_message)

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
                            enriched_message,
                            session=session,
                            stream=True,
                            options=foundry_memory_options(user_id),
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
                    store.add_message(
                        user_id,
                        session_id,
                        "assistant",
                        assistant_text,
                        {"correlationId": correlation_id},
                    )
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
                response = await agent.run(
                    [enriched_message],
                    session=session,
                    stream=False,
                    options=foundry_memory_options(user_id),
                )
                set_span_result(model_span, response_length=len(response.text or ""))
        with span(
            "memory.write",
            correlation_id,
            thread_id=session_id,
            response_length=len(response.text or ""),
        ):
            store.add_message(
                user_id,
                session_id,
                "assistant",
                response.text or "",
                {"correlationId": correlation_id},
            )
        with span("skill.use", correlation_id, skill_count=0):
            pass
        return JSONResponse({"response": response.text, "correlationId": correlation_id})


if __name__ == "__main__":
    ResponsesAndInvocationsHost(create_agent()).run()
