from __future__ import annotations

import asyncio
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
from urllib.parse import urlparse

import httpx
from jwt import PyJWTError
from msal import ConfidentialClientApplication
from agent_framework import (
    Agent,
    AgentSession,
    AggregatingSkillsSource,
    FileSkillsSource,
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
from microsoft_agents.activity import Activity
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication as ActivityAgentApplication,
    AgentAuthConfiguration,
    AuthTypes,
    Authorization,
    JwtTokenValidator,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import CloudAdapter, start_agent_process
from microsoft_agents_a365.observability.core import (
    AgentDetails,
    Agent365ExporterOptions,
    BaggageBuilder,
    CallerDetails,
    Channel,
    InvokeAgentScope,
    InvokeAgentScopeDetails,
    Request as ObservabilityRequest,
    ServiceEndpoint,
    UserDetails,
    configure as configure_agent365_observability,
)
from microsoft_agents_a365.runtime import get_observability_authentication_scope
from opentelemetry import trace
from opentelemetry.trace import Span
from pydantic import AnyUrl
import yaml
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from memory_store import store


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
SERVICE_NAME = "step-11-agent365-teams"
DEFAULT_OPTIMIZATION_CONFIG_PATH = Path(__file__).parent / ".agent_configs"
DEFAULT_SKILLS_PATH = DEFAULT_OPTIMIZATION_CONFIG_PATH / "baseline" / "skills"
DEFAULT_SKILL_RESOURCES_PATH = Path(__file__).parent / "skills" / "resources"
DEFAULT_TOOLBOX_SKILLS_PATH = Path(__file__).parent / "toolbox" / "skills"
_sessions: dict[str, AgentSession] = {}
_telemetry_configured = False
_agent365_tokens: dict[str, str] = {}

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(os.getenv("APP_LOG_LEVEL", "INFO"))
tracer = trace.get_tracer(SERVICE_NAME)


def create_credential(
    managed_identity_client_id: str | None = None,
) -> DefaultAzureCredential:
    running_in_azure = any(
        os.getenv(name)
        for name in ("AGENT_NAME", "AZURE_CLIENT_ID", "MSI_ENDPOINT", "IDENTITY_ENDPOINT")
    )
    return DefaultAzureCredential(
        exclude_managed_identity_credential=not running_in_azure,
        managed_identity_client_id=managed_identity_client_id,
    )


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

    if os.getenv("ENABLE_A365_OBSERVABILITY", "").lower() == "true":
        def token_resolver(agent_id: str, tenant_id: str) -> str | None:
            return _agent365_tokens.get(f"{agent_id}:{tenant_id}")

        configured = configure_agent365_observability(
            service_name=SERVICE_NAME,
            service_namespace="progressive-agents",
            exporter_options=Agent365ExporterOptions(
                token_resolver=token_resolver,
                use_s2s_endpoint=True,
            ),
        )
        if not configured:
            raise RuntimeError("Agent 365 observability configuration failed.")
        log_event("telemetry.agent365_configured")

    _telemetry_configured = True


class ToolboxAzureAuth(httpx.Auth):
    def __init__(self, credential: DefaultAzureCredential) -> None:
        self.credential = credential

    def auth_flow(self, request: httpx.Request):
        token = self.credential.get_token("https://ai.azure.com/.default").token
        request.headers["Authorization"] = f"Bearer {token}"
        identity_headers = {
            "AgentInstanceClientId": os.getenv("FOUNDRY_AGENT_INSTANCE_CLIENT_ID"),
            "AgentBlueprintClientId": os.getenv("FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID"),
            "ApplicationName": os.getenv("AGENT_NAME"),
        }
        for name, value in identity_headers.items():
            if value:
                request.headers[name] = value
        yield request


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

    runtime_root = Path(tempfile.mkdtemp(prefix="step11-toolbox-skills-"))
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


def create_local_skills_source(skills_dir: str | None) -> FileSkillsSource | None:
    if not skills_dir:
        return None
    skills_path = Path(skills_dir).resolve()
    if not skills_path.exists():
        raise RuntimeError(f"Optimizer skill directory does not exist: {skills_path}")
    log_event("skills.local_provider_configured", skills_path=str(skills_path))
    return FileSkillsSource(
        skills_path,
        resource_filter=lambda _skill_name, path: path.startswith(("references/", "assets/")),
    )


class ConnectedToolboxSkillsSource(SkillsSource):
    def __init__(self, toolbox: MCPStreamableHTTPTool) -> None:
        self.toolbox = toolbox
        self.archive_source: FileSkillsSource | None = None
        self.index_signature: str | None = None

    async def get_skills(self, context: SkillsSourceContext):
        await self.toolbox.connect()
        if self.toolbox.session is None:
            raise RuntimeError("Toolbox MCP session was not initialized.")
        resources_result = await self.toolbox.session.list_resources()
        resource_uris = [str(resource.uri) for resource in resources_result.resources]
        index_result = await self.toolbox.session.read_resource(AnyUrl("skill://index.json"))
        index_text = "\n".join(
            content.text
            for content in index_result.contents
            if getattr(content, "text", None)
        )
        index = json.loads(index_text)
        entries = index.get("skills", [])
        if not isinstance(entries, list) or not entries:
            raise RuntimeError("Toolbox skill index contains no skills.")
        if self.archive_source is not None and self.index_signature == index_text:
            return await self.archive_source.get_skills(context)

        if any(entry.get("type") not in {"skill-md", "archive"} for entry in entries):
            raise RuntimeError("Toolbox skill index contains an unsupported skill type.")

        runtime_root = Path(tempfile.mkdtemp(prefix="step11-toolbox-skills-"))
        atexit.register(shutil.rmtree, runtime_root, ignore_errors=True)
        for entry in entries:
            archive_name = entry.get("name")
            remote_description = entry.get("description")
            if not isinstance(archive_name, str) or not isinstance(remote_description, str):
                raise RuntimeError("Toolbox skill index entry is malformed.")
            matches: list[Path] = []
            for version_path in DEFAULT_TOOLBOX_SKILLS_PATH.iterdir():
                skill_file = version_path / "SKILL.md"
                if not skill_file.is_file():
                    continue
                skill_text = skill_file.read_text(encoding="utf-8")
                frontmatter_match = re.match(
                    r"\A---\r?\n(.*?)\r?\n---\r?\n",
                    skill_text,
                    re.DOTALL,
                )
                if not frontmatter_match:
                    continue
                frontmatter = yaml.safe_load(frontmatter_match.group(1))
                if (
                    isinstance(frontmatter, dict)
                    and frontmatter.get("description") == remote_description
                ):
                    matches.append(version_path)
            if len(matches) != 1:
                raise RuntimeError(
                    f"Toolbox skill metadata did not match one local version: {archive_name}"
                )
            selected_skill = (matches[0] / "SKILL.md").read_text(encoding="utf-8")
            selected_frontmatter = re.match(
                r"\A---\r?\n(.*?)\r?\n---\r?\n",
                selected_skill,
                re.DOTALL,
            )
            loaded_frontmatter = (
                yaml.safe_load(selected_frontmatter.group(1))
                if selected_frontmatter
                else None
            )
            local_name = (
                loaded_frontmatter.get("name")
                if isinstance(loaded_frontmatter, dict)
                else None
            )
            if not isinstance(local_name, str) or not local_name:
                raise RuntimeError(f"Toolbox skill has no local name: {archive_name}")
            shutil.copytree(matches[0], runtime_root / local_name)

        expected_canary = os.getenv("TOOLBOX_EXPECTED_SKILL_CANARY")
        if expected_canary:
            skill_files = list(runtime_root.glob("*/SKILL.md"))
            if len(skill_files) != 1 or expected_canary not in skill_files[0].read_text(
                encoding="utf-8"
            ):
                raise RuntimeError(
                    f"Toolbox skill did not contain expected canary: {expected_canary}"
                )
        self.archive_source = FileSkillsSource(
            runtime_root,
            resource_filter=lambda _skill_name, path: path.startswith(
                ("references/", "assets/")
            ),
        )
        self.index_signature = index_text
        log_event(
            "toolbox.skills_resources_validated",
            resource_count=len(resource_uris),
            expected_canary=expected_canary,
            remote_skill_count=len(entries),
        )
        return await self.archive_source.get_skills(context)


def create_toolbox(
    credential: DefaultAzureCredential,
    *,
    name: str,
    load_tools: bool,
) -> MCPStreamableHTTPTool:
    endpoint = required_env("TOOLBOX_ENDPOINT")

    return MCPStreamableHTTPTool(
        name=name,
        url=endpoint,
        http_client=httpx.AsyncClient(
            auth=ToolboxAzureAuth(credential),
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=300.0),
        ),
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
    memory_store_name = os.getenv("MEMORY_STORE_NAME", "step-11-agent365-teams")
    memory_scope = os.getenv("MEMORY_SCOPE", "{{$userId}}")
    memory_update_delay = optional_int_env("MEMORY_UPDATE_DELAY_SECONDS", 1)
    optimization_active = str(optimization_config.source).startswith(("env:", "api:"))
    memory_default_user_id = (
        os.getenv("OPTIMIZATION_MEMORY_USER_ID", "step11-optimizer-isolated")
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
    local_skills_source = create_local_skills_source(
        str(runtime_skills_path) if runtime_skills_path else None
    )
    skills_sources = [
        source
        for source in (
            local_skills_source,
            ConnectedToolboxSkillsSource(toolbox_skills_transport),
        )
        if source is not None
    ]
    skills_provider = SkillsProvider(
        AggregatingSkillsSource(skills_sources),
        disable_caching=True,
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
        source_id="agent-skills",
    )

    return (
        Agent(
            client=client,
            name="FoundryToolboxAgent",
            instructions=optimization_config.compose_instructions(),
            tools=tools,
            context_providers=[skills_provider],
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
    platform_user_id = request.headers.get("x-agent-user-id")
    if platform_user_id and platform_user_id.strip():
        return platform_user_id.strip()
    if os.getenv("AGENT_NAME"):
        raise ValueError("Hosted invocation is missing the platform user identity.")
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


def activity_message_text(activity: Activity) -> str:
    text = activity.text or ""
    recipient_id = activity.recipient.id if activity.recipient else None
    if recipient_id:
        for mention in activity.get_mentions():
            if (
                mention.mentioned
                and mention.mentioned.id == recipient_id
                and mention.text
            ):
                text = text.replace(mention.text, " ")
    return " ".join(text.split())


def activity_user_id(activity: Activity) -> str:
    sender = activity.from_property
    if sender:
        for value in (sender.aad_object_id, sender.id):
            if value and value.strip():
                return value.strip()
    raise ValueError("Activity sender must provide an Entra object ID or channel ID.")


def activity_conversation_id(activity: Activity) -> str:
    if activity.conversation and activity.conversation.id:
        return activity.conversation.id
    raise ValueError("Activity must provide a conversation ID.")


async def cache_agent365_s2s_token(
    connection_manager: MsalConnectionManager,
    tenant_id: str,
    agent_id: str,
) -> None:
    connection = connection_manager.get_connection("SERVICE_CONNECTION")
    agentic_application_token = await connection.get_agentic_application_token(
        tenant_id,
        agent_id,
    )
    if not agentic_application_token:
        raise RuntimeError("Agentic application token acquisition returned no token.")
    delegated_scope = get_observability_authentication_scope()[0]
    resource = delegated_scope.rsplit("/", 1)[0]
    client = ConfidentialClientApplication(
        client_id=agent_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential={"client_assertion": agentic_application_token},
    )
    result = await asyncio.to_thread(
        client.acquire_token_for_client,
        scopes=[f"{resource}/.default"],
    )
    token = result.get("access_token")
    if not token:
        raise RuntimeError(
            f"Agent 365 S2S token exchange failed: {result.get('error_description', result)}"
        )
    _agent365_tokens[f"{agent_id}:{tenant_id}"] = token


def create_activity_application(
    agent: Agent,
    model: str,
) -> tuple[ActivityAgentApplication[TurnState], CloudAdapter, AgentAuthConfiguration]:
    hosted = bool(os.getenv("AGENT_NAME"))
    blueprint_client_id = os.getenv("FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID")
    tenant_id = os.getenv("AZURE_TENANT_ID")
    if hosted and (not blueprint_client_id or not tenant_id):
        raise RuntimeError(
            "Hosted Activity protocol requires FOUNDRY_AGENT_BLUEPRINT_CLIENT_ID "
            "and AZURE_TENANT_ID."
        )

    auth_configuration = AgentAuthConfiguration(
        auth_type=AuthTypes.identity_proxy_manager if hosted else AuthTypes.client_secret,
        client_id=blueprint_client_id,
        tenant_id=tenant_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}" if tenant_id else None,
        scopes=["5a807f24-c9de-44ee-a3a7-329e88a00ffc/.default"],
        connection_name="SERVICE_CONNECTION",
        anonymous_allowed=not hosted,
    )
    connection_manager = MsalConnectionManager(
        connections_configurations={"SERVICE_CONNECTION": auth_configuration},
        connections_map=[{"ServiceUrl": "*", "Connection": "SERVICE_CONNECTION"}],
    )
    storage = MemoryStorage()
    adapter = CloudAdapter(connection_manager=connection_manager)
    authorization = Authorization(storage, connection_manager)
    application = ActivityAgentApplication[TurnState](
        storage=storage,
        adapter=adapter,
        authorization=authorization,
    )

    @application.activity("message")
    async def handle_activity(context: TurnContext, _state: TurnState) -> None:
        activity = context.activity
        user_message = activity_message_text(activity)
        if not user_message:
            await context.send_activity("Mention me with a non-empty support question.")
            return

        user_id = activity_user_id(activity)
        conversation_id = activity_conversation_id(activity)
        correlation_id = activity.id or conversation_id
        channel_id = activity.channel_id or "unknown"
        tenant = activity.get_agentic_tenant_id() or tenant_id or ""
        agent_id = (
            activity.get_agentic_instance_id()
            or os.getenv("FOUNDRY_AGENT_INSTANCE_CLIENT_ID")
            or SERVICE_NAME
        )
        agentic_user_id = activity.get_agentic_user()
        if os.getenv("ENABLE_A365_OBSERVABILITY", "").lower() == "true":
            if not tenant:
                raise RuntimeError("Agent 365 Activity did not provide a tenant ID.")
            await cache_agent365_s2s_token(connection_manager, tenant, agent_id)
        session = _sessions.setdefault(
            conversation_id,
            AgentSession(session_id=conversation_id),
        )
        project_host = urlparse(required_env("FOUNDRY_PROJECT_ENDPOINT")).hostname
        agent_details = AgentDetails(
            agent_id=agent_id,
            agent_name=SERVICE_NAME,
            agent_description="Support-policy agent published through Agent 365.",
            agentic_user_id=agentic_user_id,
            agent_blueprint_id=blueprint_client_id,
            tenant_id=tenant,
            agent_version=os.getenv("FOUNDRY_AGENT_VERSION"),
            provider_name="Microsoft Foundry",
        )
        caller_details = CallerDetails(
            user_details=UserDetails(
                user_id=user_id,
                user_name=activity.from_property.name if activity.from_property else None,
            )
        )
        baggage = (
            BaggageBuilder()
            .agent_id(agent_id)
            .agent_name(SERVICE_NAME)
            .tenant_id(tenant)
            .user_id(user_id)
            .channel_name(channel_id)
            .conversation_id(conversation_id)
            .session_id(conversation_id)
            .build()
        )
        with baggage:
            with InvokeAgentScope.start(
                request=ObservabilityRequest(
                    content=[user_message],
                    session_id=conversation_id,
                    conversation_id=conversation_id,
                    channel=Channel(name=channel_id),
                ),
                scope_details=InvokeAgentScopeDetails(
                    endpoint=ServiceEndpoint(hostname=project_host or "localhost", port=443)
                ),
                agent_details=agent_details,
                caller_details=caller_details,
            ) as invoke_scope:
                with span(
                    "activity.bridge",
                    correlation_id,
                    protocol="activity",
                    channel_id=channel_id,
                    conversation_id=conversation_id,
                    tenant_id=tenant,
                    user_id=user_id,
                ):
                    store.add_message(
                        user_id,
                        conversation_id,
                        "user",
                        user_message,
                        {"correlationId": correlation_id, "channelId": channel_id},
                    )
                    response = await agent.run(
                        [enrich_message_for_memory(user_id, user_message)],
                        session=session,
                        stream=False,
                        options=foundry_memory_options(user_id),
                    )
                    response_text = response.text or ""
                    store.add_message(
                        user_id,
                        conversation_id,
                        "assistant",
                        response_text,
                        {"correlationId": correlation_id, "channelId": channel_id},
                    )
                    invoke_scope.record_output_messages([response_text])
                    await context.send_activity(response_text)

    return application, adapter, auth_configuration


class ResponsesAndInvocationsHost(ResponsesHostServer):
    def __init__(self, agent: Agent, model: str) -> None:
        super().__init__(agent)
        self.model = model
        invocations = InvocationAgentServerHost()
        activity_application, activity_adapter, activity_auth = (
            create_activity_application(agent, model)
        )
        activity_validator = JwtTokenValidator(activity_auth)

        async def handle_activity_request(request: Request) -> Response:
            auth_header = request.headers.get("Authorization", "")
            if activity_auth.ANONYMOUS_ALLOWED:
                request.state.claims_identity = activity_validator.get_anonymous_claims()
            else:
                scheme, separator, token = auth_header.partition(" ")
                if separator != " " or scheme.lower() != "bearer" or not token:
                    return JSONResponse(
                        {"error": "Activity authorization header is required."},
                        status_code=401,
                    )
                try:
                    request.state.claims_identity = (
                        await activity_validator.validate_token(token)
                    )
                except (KeyError, PyJWTError, ValueError) as exc:
                    log_event("activity.authentication_failed", error=str(exc))
                    return JSONResponse(
                        {"error": "Activity authentication failed."},
                        status_code=401,
                    )
            response = await start_agent_process(
                request,
                activity_application,
                activity_adapter,
            )
            return response or Response(status_code=202)

        self.router.routes.append(
            Route(
                "/api/messages",
                handle_activity_request,
                methods=["POST"],
                name="activity_messages",
            )
        )

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
        try:
            user_id = user_id_from(request, data)
        except ValueError as exc:
            log_event("request.identity_invalid", correlation_id=correlation_id, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=401)
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
