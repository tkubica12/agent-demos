from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import AsyncGenerator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_framework import (
    Agent,
    AgentSession,
    MCPSkillsSource,
    Message,
    Skill,
    SkillsProvider,
    SkillsSource,
    SkillsSourceContext,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import FoundryToolbox, ResponsesHostServer
from azure.ai.agentserver.invocations import InvocationAgentServerHost
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from opentelemetry import trace
from opentelemetry.trace import Span
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from case_workflow import (
    CaseResolutionApprovalResponse,
    CaseResolutionRequest,
    CaseResolutionWorkflowService,
    MCPCaseTools,
)
from memory_store import store


DEFAULT_MODEL_DEPLOYMENT = "gpt-5.4-mini"
SERVICE_NAME = "foundry-showcase-main"
PERSONALITY_INSTRUCTIONS = (
    "You are a support-operations assistant for the Microsoft Foundry showcase. "
    "You are professional, calm, friendly, and concise. "
    "Lead with the direct answer and normally stay within six sentences or five bullets. "
    "Use plain language. Do not produce hype, promotional narration, or theatrical claims, "
    "even when the user asks for them; offer a factual neutral version instead. "
    "Do not imply that you can see live infrastructure, traces, memory, tenant data, "
    "or deployment state unless those facts were supplied in the current request. "
    "Refuse requests for unauthorized or sensitive data briefly and give the safest "
    "practical verification path. "
    "Distinguish verified case or policy facts from assumptions. "
    "Never claim that a case was changed unless a tool confirms the change. "
    "When unsure, acknowledge uncertainty briefly and suggest the next practical step."
)
_sessions: dict[str, AgentSession] = {}
_telemetry_configured = False

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(os.getenv("APP_LOG_LEVEL", "INFO"))
tracer = trace.get_tracer(SERVICE_NAME)


class TrustedToolboxSkillsSource(SkillsSource):
    def __init__(self, toolbox: FoundryToolbox) -> None:
        self._toolbox = toolbox

    async def get_skills(self, context: SkillsSourceContext) -> list[Skill]:
        session = self._toolbox.session
        if session is None:
            raise RuntimeError("Foundry Toolbox must be connected before skill discovery.")
        return await MCPSkillsSource(client=session).get_skills(context)


class PolicyA2AService:
    def __init__(self, toolbox: FoundryToolbox) -> None:
        self.toolbox = toolbox

    async def assess(self, policy_input: dict[str, str]) -> dict[str, Any]:
        await self.toolbox.connect()
        candidates = [
            function
            for function in self.toolbox.functions
            if function.name == "SendMessage"
            or function.name.endswith("___SendMessage")
            or (function.additional_properties or {}).get("_mcp_remote_name") == "SendMessage"
        ]
        if len(candidates) != 1:
            names = sorted(function.name for function in self.toolbox.functions)
            raise RuntimeError(
                f"Expected one A2A SendMessage function, found {len(candidates)} in {names}."
            )
        contents = await candidates[0].invoke(
            arguments={
                "message": {
                    "parts": [
                        {
                            "kind": "text",
                            "text": json.dumps(policy_input, sort_keys=True),
                        }
                    ]
                }
            }
        )
        for content in reversed(contents):
            text = getattr(content, "text", None)
            if not isinstance(text, str):
                continue
            try:
                task = json.loads(text)
                parts = task["artifacts"][-1]["parts"]
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue
            for part in reversed(parts):
                if part.get("kind") != "text" or not isinstance(part.get("text"), str):
                    continue
                try:
                    assessment = json.loads(part["text"])
                except json.JSONDecodeError:
                    continue
                if isinstance(assessment, dict):
                    return assessment
        raise RuntimeError("A2A policy helper returned no structured assessment.")


class ApprovalContinuationFoundryChatClient(FoundryChatClient):
    """Preserve the current approval response on service-managed continuation turns."""

    def _prepare_messages_for_openai(
        self,
        chat_messages: Sequence[Message],
        *,
        request_uses_service_side_storage: bool = True,
    ) -> list[dict[str, Any]]:
        prepared = super()._prepare_messages_for_openai(
            chat_messages,
            request_uses_service_side_storage=request_uses_service_side_storage,
        )
        if not request_uses_service_side_storage:
            return prepared

        for message in chat_messages:
            if "_attribution" in message.additional_properties:
                continue
            for content in message.contents:
                if content.type == "function_approval_response":
                    approval_response = self._prepare_content_for_openai(message.role, content)
                    if approval_response:
                        prepared.append(approval_response)
        return prepared


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


def create_runtime() -> tuple[Agent, CaseResolutionWorkflowService, PolicyA2AService]:
    configure_telemetry()
    credential = create_credential()
    client = ApprovalContinuationFoundryChatClient(
        project_endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        model=os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", DEFAULT_MODEL_DEPLOYMENT),
        credential=credential,
    )
    memory_store_name = os.getenv("MEMORY_STORE_NAME", "foundry-showcase-main")
    memory_scope = os.getenv("MEMORY_SCOPE", "{{$userId}}")
    memory_update_delay = optional_int_env("MEMORY_UPDATE_DELAY_SECONDS", 1)
    memory_default_user_id = os.getenv("MEMORY_DEFAULT_USER_ID", "playground-user")
    memory_tool = FoundryChatClient.get_memory_search_tool(
        memory_store_name=memory_store_name,
        scope=memory_scope,
        update_delay=memory_update_delay,
    )
    toolbox = FoundryToolbox(credential)
    toolbox.approval_mode = {
        "always_require_approval": {
            "case-write___apply_case_update",
        },
        "never_require_approval": {
            "case-read___search_cases",
            "case-read___get_case",
            "case-read___propose_case_update",
        },
    }
    skills_provider = SkillsProvider(
        TrustedToolboxSkillsSource(toolbox),
        source_id="foundry-toolbox-skills",
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
        disable_run_skill_script_approval=True,
    )
    policy_toolbox_name = os.getenv(
        "POLICY_TOOLBOX_NAME",
        "foundry-showcase-policy-tools",
    )
    policy_toolbox = FoundryToolbox(
        credential,
        url=(
            f"{required_env('FOUNDRY_PROJECT_ENDPOINT').rstrip('/')}/toolboxes/"
            f"{policy_toolbox_name}/mcp?api-version=v1"
        ),
        name="policy-delegation",
    )
    policy_service = PolicyA2AService(policy_toolbox)
    tools = [memory_tool.as_dict(), toolbox, policy_toolbox]
    log_event(
        "memory.foundry_tool_configured",
        memory_store_name=memory_store_name,
        memory_scope=memory_scope,
        memory_update_delay=memory_update_delay,
        memory_default_user_id=memory_default_user_id,
    )
    log_event(
        "toolbox.foundry_configured",
        toolbox_name=os.getenv("TOOLBOX_NAME"),
        toolbox_endpoint=os.getenv("TOOLBOX_ENDPOINT"),
    )
    log_event(
        "a2a.policy_helper_configured",
        policy_toolbox_name=policy_toolbox_name,
        policy_toolbox_endpoint=policy_toolbox.url,
    )

    agent = Agent(
        client=client,
        name="FoundryShowcaseAgent",
        instructions=(
            f"{PERSONALITY_INSTRUCTIONS} "
            "You run as the primary Foundry Hosted Agent for a support-operations "
            "demonstration. You receive compact persistent user context before "
            "the current user message. Use it quietly and naturally. "
            "You also have reusable Agent Skills. Load a skill only when the "
            "user request matches that skill's description, and read skill "
            "resources only when the loaded skill tells you they are needed. "
            "Use the case-read tools to inspect cases and create noncommitted "
            "update proposals. The case-write.apply_case_update tool is a "
            "high-impact action and must run only after the client completes "
            "the explicit Agent Framework approval exchange. "
            "Delegate support-case policy contradiction and risk checks to the "
            "read-only A2A policy helper before recommending high-impact updates. "
            "Never ask the policy helper to mutate a case, and identify its output "
            "as an advisory policy assessment rather than a completed write. "
            "For profile mutations, prefer explicit profile patch operations "
            "provided by the host API. Include correlation IDs only when "
            "explicitly asked; otherwise answer naturally."
        ),
        tools=tools,
        context_providers=[skills_provider],
        default_options={
            "store": False,
            "extra_headers": {"x-memory-user-id": memory_default_user_id},
        },
    )
    checkpoint_dir = Path(
        os.getenv(
            "WORKFLOW_CHECKPOINT_DIR",
            str(Path(tempfile.gettempdir()) / "foundry-showcase-workflows"),
        )
    )
    return (
        agent,
        CaseResolutionWorkflowService(
            MCPCaseTools(toolbox),
            checkpoint_dir,
            policy_tools=policy_service,
        ),
        policy_service,
    )


def create_agent() -> Agent:
    agent, _, _ = create_runtime()
    return agent


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


def parse_invocation_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        if "action" not in value and "message" not in value and "input" in value:
            return parse_invocation_payload(value["input"])
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"message": value}
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Invocation payload must be an object or a JSON object string.")


def build_quality_digest(cases: list[dict[str, Any]]) -> dict[str, Any]:
    priority_counts: dict[str, int] = {}
    owner_counts: dict[str, int] = {}
    for case in cases:
        priority = str(case.get("priority", "unknown"))
        owner = str(case.get("owner", "unassigned"))
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
        owner_counts[owner] = owner_counts.get(owner, 0) + 1
    return {
        "unresolvedCount": len(cases),
        "priorityCounts": priority_counts,
        "ownerCounts": owner_counts,
        "cases": [
            {
                key: case.get(key)
                for key in (
                    "case_id",
                    "title",
                    "status",
                    "priority",
                    "owner",
                    "updated_at",
                )
            }
            for case in cases
        ],
    }


def build_follow_up(case: dict[str, Any]) -> dict[str, Any]:
    status = case.get("status")
    if status == "resolved":
        recommendation = "No follow-up is required unless the customer reports recurrence."
    elif status == "pending_customer":
        recommendation = "Contact the customer for the missing information and keep the case open."
    elif status == "escalated":
        recommendation = "Review the escalation owner and confirm the next internal checkpoint."
    else:
        recommendation = "Review current evidence with the owner and define the next customer update."
    return {
        "caseId": case.get("case_id"),
        "status": status,
        "priority": case.get("priority"),
        "owner": case.get("owner"),
        "recommendation": recommendation,
    }


class ResponsesAndInvocationsHost(ResponsesHostServer):
    def __init__(
        self,
        agent: Agent,
        case_workflow: CaseResolutionWorkflowService,
        policy_delegate: PolicyA2AService,
    ) -> None:
        super().__init__(agent)
        self.case_workflow = case_workflow
        self.policy_delegate = policy_delegate
        invocations = InvocationAgentServerHost()

        @invocations.invoke_handler
        async def handle_invoke(request: Request) -> Response:
            data = parse_invocation_payload(await request.json())
            correlation_id = correlation_id_from(request, data)
            log_event(
                "invocation.payload_parsed",
                correlation_id=correlation_id,
                keys=sorted(data),
                action=data.get("action"),
                message_type=type(data.get("message")).__name__,
            )

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
            workflow_response = await self._handle_workflow_action(
                data,
                user_id=user_id,
                correlation_id=correlation_id,
            )
        except (ValueError, KeyError) as exc:
            log_event("workflow.action_invalid", correlation_id=correlation_id, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
        if workflow_response is not None:
            return workflow_response
        try:
            action_response = handle_memory_action(user_id, session_id, data)
        except (KeyError, ValueError) as exc:
            log_event("memory.action_invalid", correlation_id=correlation_id, error=str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
        if action_response is not None:
            return action_response

        await self._ensure_agent_ready()
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

    async def _handle_workflow_action(
        self,
        data: dict,
        *,
        user_id: str,
        correlation_id: str,
    ) -> JSONResponse | None:
        action = data.get("action")
        if action == "start_case_resolution":
            changes = data.get("changes")
            if not isinstance(changes, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in changes.items()
            ):
                raise ValueError("changes must be an object with string values.")
            with span("workflow.start", correlation_id, workflow="resolve_support_case"):
                envelope = await self.case_workflow.start(
                    CaseResolutionRequest(
                        case_id=data.get("caseId", ""),
                        changes=changes,
                        reason=data.get("reason", ""),
                        requested_by=data.get("requestedBy") or user_id,
                    )
                )
            log_event(
                "workflow.started",
                correlation_id=correlation_id,
                workflow_id=envelope.workflow_id,
                state=envelope.state,
            )
            return JSONResponse(
                {
                    **envelope.model_dump(mode="json", exclude_none=True),
                    "correlationId": correlation_id,
                }
            )
        if action == "resume_case_resolution":
            workflow_id = data.get("workflowId")
            checkpoint_id = data.get("checkpointId")
            request_id = data.get("requestId")
            for name, value in (
                ("workflowId", workflow_id),
                ("checkpointId", checkpoint_id),
                ("requestId", request_id),
            ):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{name} must be a non-empty string.")
            with span("workflow.resume", correlation_id, workflow="resolve_support_case"):
                envelope = await self.case_workflow.resume(
                    workflow_id=workflow_id,
                    checkpoint_id=checkpoint_id,
                    request_id=request_id,
                    response=CaseResolutionApprovalResponse(
                        approved=data.get("approved"),
                        confirmation_id=data.get("confirmationId"),
                        comment=data.get("comment", ""),
                    ),
                )
            log_event(
                "workflow.resumed",
                correlation_id=correlation_id,
                workflow_id=envelope.workflow_id,
                state=envelope.state,
            )
            return JSONResponse(
                {
                    **envelope.model_dump(mode="json", exclude_none=True),
                    "correlationId": correlation_id,
                }
            )
        if action == "assess_case_policy":
            policy_input = data.get("policyInput")
            if not isinstance(policy_input, dict) or not policy_input:
                raise ValueError("policyInput must be a non-empty object.")
            if not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in policy_input.items()
            ):
                raise ValueError("policyInput keys and values must be strings.")
            with span("a2a.policy_assessment", correlation_id, worker="policy-helper"):
                assessment = await self.policy_delegate.assess(policy_input)
            log_event(
                "a2a.policy_assessment_completed",
                correlation_id=correlation_id,
                decision=assessment.get("decision"),
                risk=assessment.get("risk"),
            )
            return JSONResponse(
                {
                    "assessment": assessment,
                    "correlationId": correlation_id,
                }
            )
        if action == "daily_support_quality_review":
            with span(
                "routine.daily_support_quality_review",
                correlation_id,
                routine="daily-support-quality-review",
            ):
                cases = await self.case_workflow.tools.search_cases(limit=50)
                unresolved = [
                    case for case in cases if case.get("status") != "resolved"
                ]
                digest = build_quality_digest(unresolved)
            log_event(
                "routine.daily_support_quality_review_completed",
                correlation_id=correlation_id,
                unresolved_count=digest["unresolvedCount"],
            )
            return JSONResponse(
                {
                    "action": action,
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "digest": digest,
                    "correlationId": correlation_id,
                }
            )
        if action == "case_follow_up_reminder":
            case_id = data.get("caseId")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("caseId must be a non-empty string.")
            with span(
                "routine.case_follow_up_reminder",
                correlation_id,
                routine="case-follow-up-reminder",
                case_id=case_id,
            ):
                case = await self.case_workflow.tools.get_case(case_id)
                follow_up = build_follow_up(case)
            log_event(
                "routine.case_follow_up_reminder_completed",
                correlation_id=correlation_id,
                case_id=case_id,
            )
            return JSONResponse(
                {
                    "action": action,
                    "generatedAt": datetime.now(UTC).isoformat(),
                    "followUp": follow_up,
                    "correlationId": correlation_id,
                }
            )
        return None


if __name__ == "__main__":
    runtime_agent, runtime_workflow, runtime_policy_delegate = create_runtime()
    ResponsesAndInvocationsHost(
        runtime_agent,
        runtime_workflow,
        runtime_policy_delegate,
    ).run()
