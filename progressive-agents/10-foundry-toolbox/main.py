from __future__ import annotations

import atexit
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agent_framework import (
    Agent,
    AgentSession,
    MCPSkillsSource,
    MCPStreamableHTTPTool,
    SkillsProvider,
    SkillsSource,
    SkillsSourceContext,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.ai.agentserver.optimization import load_config, load_skills_from_dir
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from opentelemetry import trace
from opentelemetry.trace import Span
import yaml
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from memory_store import store


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
SERVICE_NAME = "step-10-foundry-toolbox"
DEFAULT_OPTIMIZATION_CONFIG_PATH = Path(__file__).parent / ".agent_configs"
DEFAULT_SKILLS_PATH = DEFAULT_OPTIMIZATION_CONFIG_PATH / "baseline" / "skills"
DEFAULT_SKILL_RESOURCES_PATH = Path(__file__).parent / "skills" / "resources"
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


def load_optimization_configuration():
    config_path = Path(
        os.getenv("OPTIMIZATION_LOCAL_DIR", str(DEFAULT_OPTIMIZATION_CONFIG_PATH))
    ).resolve()
    config = load_config(config_dir=config_path)
    if config is None:
        raise RuntimeError(f"No optimization configuration found in {config_path}.")
    if not config.skills and config.skills_dir:
        config.skills.extend(load_skills_from_dir(Path(config.skills_dir)))
    if not config.instructions.strip():
        raise RuntimeError("Optimization configuration instructions must not be empty.")
    log_event(
        "optimizer.config_loaded",
        source=config.source,
        model=config.model,
        skill_count=len(config.skills),
        tool_definition_count=len(config.tool_definitions),
    )
    return config


def materialize_optimizer_skills(config) -> Path | None:
    if not config.skills:
        return None
    source_path = Path(config.skills_dir or DEFAULT_SKILLS_PATH).resolve()
    if not source_path.exists():
        raise RuntimeError(f"Optimizer skill directory does not exist: {source_path}")

    runtime_root = Path(tempfile.mkdtemp(prefix="step10-toolbox-skills-"))
    runtime_skills = runtime_root / "skills"
    shutil.copytree(source_path, runtime_skills)
    if DEFAULT_SKILL_RESOURCES_PATH.exists():
        shutil.copytree(
            DEFAULT_SKILL_RESOURCES_PATH,
            runtime_skills,
            dirs_exist_ok=True,
        )
    atexit.register(shutil.rmtree, runtime_root, ignore_errors=True)

    for skill in config.skills:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill.name):
            raise RuntimeError(f"Invalid optimizer skill name: {skill.name}")
        skill_file = runtime_skills / skill.name / "SKILL.md"
        frontmatter: dict[str, Any] = {}
        if skill_file.exists():
            original = skill_file.read_text(encoding="utf-8")
            match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", original, re.DOTALL)
            if match:
                loaded = yaml.safe_load(match.group(1))
                if isinstance(loaded, dict):
                    frontmatter = loaded
        frontmatter["name"] = skill.name
        frontmatter["description"] = skill.description
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(
            "---\n"
            f"{yaml.safe_dump(frontmatter, sort_keys=False).strip()}\n"
            "---\n\n"
            f"{skill.body.strip()}\n",
            encoding="utf-8",
        )

    log_event(
        "optimizer.skills_materialized",
        source=config.source,
        skill_count=len(config.skills),
        runtime_path=str(runtime_skills),
    )
    return runtime_skills


def create_skills_provider(skills_dir: str | None) -> SkillsProvider | None:
    if not skills_dir:
        return None
    skills_path = Path(skills_dir).resolve()
    if not skills_path.exists():
        raise RuntimeError(f"Optimizer skill directory does not exist: {skills_path}")
    log_event("skills.local_provider_configured", skills_path=str(skills_path))
    return SkillsProvider.from_paths(
        skill_paths=skills_path,
        resource_filter=lambda _skill_name, path: path.startswith(("references/", "assets/")),
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
    )


class ConnectedToolboxSkillsSource(SkillsSource):
    def __init__(self, toolbox: MCPStreamableHTTPTool) -> None:
        self.toolbox = toolbox

    async def get_skills(self, context: SkillsSourceContext):
        await self.toolbox.connect()
        if self.toolbox.session is None:
            raise RuntimeError("Toolbox MCP session was not initialized.")
        return await MCPSkillsSource(self.toolbox.session).get_skills(context)


def create_toolbox(
    credential: DefaultAzureCredential,
    *,
    name: str,
    load_tools: bool,
) -> MCPStreamableHTTPTool:
    endpoint = required_env("TOOLBOX_ENDPOINT")

    def headers(_context: dict[str, Any]) -> dict[str, str]:
        token = credential.get_token("https://ai.azure.com/.default").token
        return {"Authorization": f"Bearer {token}"}

    return MCPStreamableHTTPTool(
        name=name,
        url=endpoint,
        header_provider=headers,
        load_tools=load_tools,
        load_prompts=False,
        approval_mode="never_require",
        use_progressive_disclosure=load_tools,
        description=(
            "Versioned Foundry Toolbox for read-only Contoso support policies "
            "and pinned support skills."
        ),
    )


def create_agent() -> tuple[Agent, str]:
    configure_telemetry()
    optimization_config = load_optimization_configuration()
    model = optimization_config.model or os.getenv(
        "AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL_DEPLOYMENT
    )
    credential = create_credential()
    client = FoundryChatClient(
        project_endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        model=model,
        credential=credential,
    )
    memory_store_name = os.getenv("MEMORY_STORE_NAME", "step-10-foundry-toolbox")
    memory_scope = os.getenv("MEMORY_SCOPE", "{{$userId}}")
    memory_update_delay = optional_int_env("MEMORY_UPDATE_DELAY_SECONDS", 1)
    optimization_active = str(optimization_config.source).startswith(("env:", "api:"))
    memory_default_user_id = (
        os.getenv("OPTIMIZATION_MEMORY_USER_ID", "step10-optimizer-isolated")
        if optimization_active
        else os.getenv("MEMORY_DEFAULT_USER_ID", "playground-user")
    )
    memory_tool = FoundryChatClient.get_memory_search_tool(
        memory_store_name=memory_store_name,
        scope=memory_scope,
        update_delay=memory_update_delay,
    )
    toolbox = create_toolbox(
        credential,
        name="contoso_support_toolbox",
        load_tools=True,
    )
    toolbox_skills_transport = create_toolbox(
        credential,
        name="contoso_support_toolbox_skills",
        load_tools=False,
    )
    tools = [toolbox, toolbox_skills_transport, memory_tool.as_dict()]
    log_event(
        "memory.foundry_tool_configured",
        memory_store_name=memory_store_name,
        memory_scope=memory_scope,
        memory_update_delay=memory_update_delay,
        memory_default_user_id=memory_default_user_id,
        optimization_isolation=optimization_active,
    )
    runtime_skills_path = materialize_optimizer_skills(optimization_config)
    skills_provider = create_skills_provider(
        str(runtime_skills_path) if runtime_skills_path else None
    )
    toolbox_skills_provider = SkillsProvider(
        ConnectedToolboxSkillsSource(toolbox_skills_transport),
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
        source_id="foundry-toolbox",
    )

    return (
        Agent(
            client=client,
            name="FoundryToolboxAgent",
            instructions=optimization_config.compose_instructions(),
            tools=tools,
            context_providers=[
                provider
                for provider in (skills_provider, toolbox_skills_provider)
                if provider is not None
            ],
            default_options={
                "store": False,
                "extra_headers": {"x-memory-user-id": memory_default_user_id},
            },
        ),
        model,
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
    def __init__(self, agent: Agent, model: str) -> None:
        super().__init__(agent)
        self.model = model
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
                with span("model.call", correlation_id, model=self.model) as model_span:
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

            return StreamingResponse(
                stream_response(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        with span("model.call", correlation_id, model=self.model) as model_span:
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
        return JSONResponse({"response": response.text, "correlationId": correlation_id})


if __name__ == "__main__":
    configured_agent, configured_model = create_agent()
    ResponsesAndInvocationsHost(configured_agent, configured_model).run()
