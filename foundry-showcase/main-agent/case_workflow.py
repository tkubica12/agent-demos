import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Literal, Protocol

from agent_framework import (
    Case,
    Default,
    Executor,
    FileCheckpointStorage,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from pydantic import BaseModel, Field, field_validator


class CaseResolutionRequest(BaseModel):
    case_id: str
    changes: dict[str, str]
    reason: str
    requested_by: str

    @field_validator("case_id", "reason", "requested_by")
    @classmethod
    def require_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Value must not be empty.")
        return clean

    @field_validator("changes")
    @classmethod
    def validate_changes(cls, value: dict[str, str]) -> dict[str, str]:
        supported = {"status", "owner", "priority", "resolution_note"}
        clean = {key: item.strip() for key, item in value.items() if item.strip()}
        if not clean:
            raise ValueError("At least one non-empty case change is required.")
        unsupported = clean.keys() - supported
        if unsupported:
            raise ValueError(f"Unsupported case fields: {sorted(unsupported)}")
        return clean


class RiskAssessment(BaseModel):
    level: Literal["low", "medium", "high"]
    rationale: str


class PolicyAssessment(BaseModel):
    decision: Literal["allow", "review", "deny"]
    risk: Literal["low", "medium", "high"]
    contradictions: list[str]
    rationale: str


class PreparedCaseResolution(BaseModel):
    workflow_id: str
    request: CaseResolutionRequest
    current_case: dict[str, Any]
    assessment: RiskAssessment
    policy_assessment: PolicyAssessment | None = None


class CaseResolutionApprovalRequest(BaseModel):
    workflow_id: str
    case_id: str
    changes: dict[str, str]
    risk: Literal["medium", "high"]
    rationale: str
    prompt: str
    prepared: PreparedCaseResolution = Field(exclude=True)


class CaseResolutionApprovalResponse(BaseModel):
    approved: bool
    confirmation_id: str | None = None
    comment: str = ""

    @field_validator("confirmation_id")
    @classmethod
    def clean_confirmation_id(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class ApplyDecision(BaseModel):
    prepared: PreparedCaseResolution
    approved: bool
    confirmation_id: str | None = None
    comment: str = ""


class CaseResolutionResult(BaseModel):
    workflow_id: str
    case_id: str
    status: Literal["completed", "rejected"]
    risk: Literal["low", "medium", "high"]
    proposal_id: str | None = None
    confirmation_id: str | None = None
    case: dict[str, Any] | None = None
    audit: dict[str, Any] | None = None
    message: str


class WorkflowEnvelope(BaseModel):
    workflow_id: str
    state: Literal["pending_confirmation", "completed", "rejected"]
    checkpoint_id: str
    request_id: str | None = None
    approval: CaseResolutionApprovalRequest | None = None
    result: CaseResolutionResult | None = None


class CaseTools(Protocol):
    async def get_case(self, case_id: str) -> dict[str, Any]: ...

    async def propose_case_update(
        self,
        case_id: str,
        reason: str,
        changes: dict[str, str],
    ) -> dict[str, Any]: ...

    async def apply_case_update(
        self,
        proposal_id: str,
        confirmation_id: str,
    ) -> dict[str, Any]: ...


class PolicyTools(Protocol):
    async def assess(self, policy_input: dict[str, str]) -> dict[str, Any]: ...


RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def assess_case_update(changes: dict[str, str]) -> RiskAssessment:
    if changes.get("priority") == "critical":
        return RiskAssessment(
            level="high",
            rationale="Setting critical priority requires elevated confirmation.",
        )
    if changes.get("status") in {"escalated", "resolved"}:
        return RiskAssessment(
            level="high",
            rationale="Escalating or resolving a case is a high-impact lifecycle change.",
        )
    if set(changes) == {"resolution_note"}:
        return RiskAssessment(
            level="low",
            rationale="Adding a resolution note does not change ownership, priority, or lifecycle.",
        )
    return RiskAssessment(
        level="medium",
        rationale="The update changes operational case routing or noncritical state.",
    )


class PrepareCaseResolution(Executor):
    def __init__(
        self,
        workflow_id: str,
        tools: CaseTools,
        policy_tools: PolicyTools | None,
    ) -> None:
        super().__init__(id="prepare_case_resolution")
        self.workflow_id = workflow_id
        self.tools = tools
        self.policy_tools = policy_tools

    @handler
    async def prepare(
        self,
        request: CaseResolutionRequest,
        ctx: WorkflowContext[PreparedCaseResolution],
    ) -> None:
        current_case = await self.tools.get_case(request.case_id)
        local_assessment = assess_case_update(request.changes)
        policy_assessment = None
        if self.policy_tools is not None:
            raw_policy_assessment = await self.policy_tools.assess(
                {
                    "current_status": str(current_case.get("status", "")),
                    "current_priority": str(current_case.get("priority", "")),
                    "proposed_status": request.changes.get("status", ""),
                    "proposed_owner": request.changes.get("owner", ""),
                    "proposed_priority": request.changes.get("priority", ""),
                    "proposed_resolution_note": request.changes.get(
                        "resolution_note",
                        "",
                    ),
                }
            )
            policy_assessment = PolicyAssessment.model_validate(raw_policy_assessment)
            if RISK_ORDER[policy_assessment.risk] > RISK_ORDER[local_assessment.level]:
                local_assessment = RiskAssessment(
                    level=policy_assessment.risk,
                    rationale=(
                        f"{local_assessment.rationale} "
                        f"Policy helper: {policy_assessment.rationale}"
                    ),
                )
        prepared = PreparedCaseResolution(
            workflow_id=self.workflow_id,
            request=request,
            current_case=current_case,
            assessment=local_assessment,
            policy_assessment=policy_assessment,
        )
        await ctx.send_message(prepared)


class RequestCaseConfirmation(Executor):
    def __init__(self) -> None:
        super().__init__(id="request_case_confirmation")

    @handler
    async def request(
        self,
        prepared: PreparedCaseResolution,
        ctx: WorkflowContext,
    ) -> None:
        risk = prepared.assessment.level
        if risk not in {"medium", "high"}:
            raise ValueError(f"Confirmation is not valid for {risk}-risk changes.")
        await ctx.request_info(
            request_data=CaseResolutionApprovalRequest(
                workflow_id=prepared.workflow_id,
                case_id=prepared.request.case_id,
                changes=prepared.request.changes,
                risk=risk,
                rationale=prepared.assessment.rationale,
                prompt=(
                    f"Approve {risk}-risk update to {prepared.request.case_id}: "
                    f"{json.dumps(prepared.request.changes, sort_keys=True)}"
                ),
                prepared=prepared,
            ),
            response_type=CaseResolutionApprovalResponse,
        )

    @response_handler
    async def receive_response(
        self,
        original_request: CaseResolutionApprovalRequest,
        response: CaseResolutionApprovalResponse,
        ctx: WorkflowContext[ApplyDecision, str],
    ) -> None:
        if response.approved and not response.confirmation_id:
            raise ValueError("Approved updates require a non-empty confirmation_id.")
        await ctx.send_message(
            ApplyDecision(
                prepared=original_request.prepared,
                approved=response.approved,
                confirmation_id=response.confirmation_id,
                comment=response.comment,
            )
        )


class ApplyCaseResolution(Executor):
    def __init__(self, tools: CaseTools) -> None:
        super().__init__(id="apply_case_resolution")
        self.tools = tools

    @handler
    async def apply_low_risk(
        self,
        prepared: PreparedCaseResolution,
        ctx: WorkflowContext[None, CaseResolutionResult],
    ) -> None:
        await self._apply(
            prepared,
            confirmation_id=f"workflow-auto-{uuid.uuid4()}",
            ctx=ctx,
        )

    @handler
    async def apply_confirmed(
        self,
        decision: ApplyDecision,
        ctx: WorkflowContext[None, CaseResolutionResult],
    ) -> None:
        if not decision.approved or not decision.confirmation_id:
            raise ValueError("Only confirmed decisions can reach the apply executor.")
        await self._apply(decision.prepared, decision.confirmation_id, ctx)

    async def _apply(
        self,
        prepared: PreparedCaseResolution,
        confirmation_id: str,
        ctx: WorkflowContext[None, CaseResolutionResult],
    ) -> None:
        request = prepared.request
        proposal = await self.tools.propose_case_update(
            request.case_id,
            request.reason,
            request.changes,
        )
        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str) or not proposal_id:
            raise RuntimeError("Case proposal response did not include proposal_id.")
        applied = await self.tools.apply_case_update(proposal_id, confirmation_id)
        await ctx.yield_output(
            CaseResolutionResult(
                workflow_id=prepared.workflow_id,
                case_id=request.case_id,
                status="completed",
                risk=prepared.assessment.level,
                proposal_id=proposal_id,
                confirmation_id=confirmation_id,
                case=applied.get("case"),
                audit=applied.get("audit"),
                message="Case update applied through the governed proposal path.",
            )
        )


class RejectCaseResolution(Executor):
    def __init__(self) -> None:
        super().__init__(id="reject_case_resolution")

    @handler
    async def reject(
        self,
        decision: ApplyDecision,
        ctx: WorkflowContext[None, CaseResolutionResult],
    ) -> None:
        prepared = decision.prepared
        await ctx.yield_output(
            CaseResolutionResult(
                workflow_id=prepared.workflow_id,
                case_id=prepared.request.case_id,
                status="rejected",
                risk=prepared.assessment.level,
                message=decision.comment or "Case update rejected by the reviewer.",
            )
        )


class RejectPolicyViolation(Executor):
    def __init__(self) -> None:
        super().__init__(id="reject_policy_violation")

    @handler
    async def reject(
        self,
        prepared: PreparedCaseResolution,
        ctx: WorkflowContext[None, CaseResolutionResult],
    ) -> None:
        policy = prepared.policy_assessment
        if policy is None or policy.decision != "deny":
            raise ValueError("Only denied policy assessments can reach this executor.")
        await ctx.yield_output(
            CaseResolutionResult(
                workflow_id=prepared.workflow_id,
                case_id=prepared.request.case_id,
                status="rejected",
                risk=prepared.assessment.level,
                message=" ".join(policy.contradictions) or policy.rationale,
            )
        )


class CaseResolutionWorkflowService:
    def __init__(
        self,
        tools: CaseTools,
        checkpoint_dir: Path,
        policy_tools: PolicyTools | None = None,
    ) -> None:
        self.tools = tools
        self.policy_tools = policy_tools
        self.completion_dir = checkpoint_dir / "completed"
        self.completion_dir.mkdir(parents=True, exist_ok=True)
        self.storage = FileCheckpointStorage(
            checkpoint_dir,
            allowed_checkpoint_types=[
                "case_workflow:CaseResolutionRequest",
                "case_workflow:RiskAssessment",
                "case_workflow:PolicyAssessment",
                "case_workflow:PreparedCaseResolution",
                "case_workflow:CaseResolutionApprovalRequest",
                "case_workflow:CaseResolutionApprovalResponse",
                "case_workflow:ApplyDecision",
                "case_workflow:CaseResolutionResult",
            ],
        )
        self._lock = asyncio.Lock()

    def _build(self, workflow_id: str):
        prepare = PrepareCaseResolution(workflow_id, self.tools, self.policy_tools)
        confirmation = RequestCaseConfirmation()
        apply = ApplyCaseResolution(self.tools)
        reject = RejectCaseResolution()
        reject_policy = RejectPolicyViolation()
        return (
            WorkflowBuilder(
                start_executor=prepare,
                checkpoint_storage=self.storage,
                name=f"resolve-support-case-v1-{workflow_id}",
                output_from=[apply, reject, reject_policy],
            )
            .add_switch_case_edge_group(
                prepare,
                [
                    Case(
                        condition=lambda item: (
                            item.policy_assessment is not None
                            and item.policy_assessment.decision == "deny"
                        ),
                        target=reject_policy,
                    ),
                    Case(
                        condition=lambda item: item.assessment.level == "low",
                        target=apply,
                    ),
                    Default(target=confirmation),
                ],
            )
            .add_switch_case_edge_group(
                confirmation,
                [
                    Case(condition=lambda item: item.approved, target=apply),
                    Default(target=reject),
                ],
            )
            .build()
        )

    async def start(self, request: CaseResolutionRequest) -> WorkflowEnvelope:
        workflow_id = str(uuid.uuid4())
        workflow = self._build(workflow_id)
        async with self._lock:
            result = await workflow.run(request)
            checkpoint = await self.storage.get_latest(
                workflow_name=f"resolve-support-case-v1-{workflow_id}"
            )
        if checkpoint is None:
            raise RuntimeError("Workflow completed without creating a checkpoint.")
        return self._envelope(workflow_id, checkpoint.checkpoint_id, result)

    async def resume(
        self,
        *,
        workflow_id: str,
        checkpoint_id: str,
        request_id: str,
        response: CaseResolutionApprovalResponse,
    ) -> WorkflowEnvelope:
        workflow = self._build(workflow_id)
        async with self._lock:
            completed = self._read_completion(workflow_id)
            if completed is not None:
                return completed
            result = await workflow.run(
                checkpoint_id=checkpoint_id,
                responses={request_id: response},
            )
            checkpoint = await self.storage.get_latest(
                workflow_name=f"resolve-support-case-v1-{workflow_id}"
            )
            if checkpoint is None:
                raise RuntimeError("Workflow resumed without creating a checkpoint.")
            envelope = self._envelope(workflow_id, checkpoint.checkpoint_id, result)
            if envelope.state != "pending_confirmation":
                self._write_completion(envelope)
            return envelope

    def _completion_path(self, workflow_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(workflow_id))
        except ValueError as exc:
            raise ValueError("workflow_id must be a UUID.") from exc
        return self.completion_dir / f"{normalized}.json"

    def _read_completion(self, workflow_id: str) -> WorkflowEnvelope | None:
        path = self._completion_path(workflow_id)
        if not path.exists():
            return None
        return WorkflowEnvelope.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_completion(self, envelope: WorkflowEnvelope) -> None:
        path = self._completion_path(envelope.workflow_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            envelope.model_dump_json(exclude_none=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _envelope(
        workflow_id: str,
        checkpoint_id: str,
        events,
    ) -> WorkflowEnvelope:
        outputs = events.get_outputs()
        if outputs:
            result = outputs[-1]
            if not isinstance(result, CaseResolutionResult):
                raise TypeError(f"Unexpected workflow output type: {type(result).__name__}")
            return WorkflowEnvelope(
                workflow_id=workflow_id,
                state="completed" if result.status == "completed" else "rejected",
                checkpoint_id=checkpoint_id,
                result=result,
            )

        requests = [event for event in events if event.type == "request_info"]
        if len(requests) != 1:
            raise RuntimeError(f"Expected one pending confirmation, found {len(requests)}.")
        event = requests[0]
        approval = event.data
        if not isinstance(approval, CaseResolutionApprovalRequest):
            raise TypeError(f"Unexpected approval request type: {type(approval).__name__}")
        return WorkflowEnvelope(
            workflow_id=workflow_id,
            state="pending_confirmation",
            checkpoint_id=checkpoint_id,
            request_id=event.request_id,
            approval=approval,
        )


class MCPCaseTools:
    def __init__(self, mcp_tool) -> None:
        self.mcp_tool = mcp_tool

    async def get_case(self, case_id: str) -> dict[str, Any]:
        return await self._invoke("get_case", {"case_id": case_id})

    async def propose_case_update(
        self,
        case_id: str,
        reason: str,
        changes: dict[str, str],
    ) -> dict[str, Any]:
        return await self._invoke(
            "propose_case_update",
            {"case_id": case_id, "reason": reason, **changes},
        )

    async def apply_case_update(
        self,
        proposal_id: str,
        confirmation_id: str,
    ) -> dict[str, Any]:
        return await self._invoke(
            "apply_case_update",
            {
                "proposal_id": proposal_id,
                "confirmation_id": confirmation_id,
            },
        )

    async def _invoke(self, remote_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await self.mcp_tool.connect()
        candidates = [
            function
            for function in self.mcp_tool.functions
            if function.name == remote_name
            or function.name.endswith(f"___{remote_name}")
            or (function.additional_properties or {}).get("_mcp_remote_name") == remote_name
        ]
        if len(candidates) != 1:
            names = sorted(function.name for function in self.mcp_tool.functions)
            raise RuntimeError(
                f"Expected one Toolbox function for {remote_name}, found {len(candidates)} in {names}."
            )
        contents = await candidates[0].invoke(arguments=arguments)
        for content in reversed(contents):
            text = getattr(content, "text", None)
            if not isinstance(text, str):
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise RuntimeError(f"Toolbox function {remote_name} returned no JSON object.")
