from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from case_workflow import (
    CaseResolutionApprovalResponse,
    CaseResolutionRequest,
    CaseResolutionWorkflowService,
    assess_case_update,
)


class FakeCaseTools:
    def __init__(self) -> None:
        self.case = {
            "case_id": "CASE-1001",
            "status": "open",
            "priority": "high",
            "owner": "Avery",
            "resolution_note": "",
        }
        self.proposals: dict[str, dict] = {}
        self.apply_calls = 0

    async def get_case(self, case_id: str) -> dict:
        if case_id != self.case["case_id"]:
            raise KeyError(case_id)
        return deepcopy(self.case)

    async def propose_case_update(
        self,
        case_id: str,
        reason: str,
        changes: dict[str, str],
    ) -> dict:
        proposal_id = f"proposal-{len(self.proposals) + 1}"
        proposal = {
            "proposal_id": proposal_id,
            "case_id": case_id,
            "reason": reason,
            "changes": deepcopy(changes),
        }
        self.proposals[proposal_id] = proposal
        return deepcopy(proposal)

    async def apply_case_update(
        self,
        proposal_id: str,
        confirmation_id: str,
    ) -> dict:
        self.apply_calls += 1
        proposal = self.proposals[proposal_id]
        self.case.update(proposal["changes"])
        return {
            "case": deepcopy(self.case),
            "proposal": deepcopy(proposal),
            "audit": {
                "action": "case.update_applied",
                "confirmation_id": confirmation_id,
            },
        }


class FakePolicyTools:
    def __init__(self, assessment: dict) -> None:
        self.assessment = assessment
        self.inputs: list[dict[str, str]] = []

    async def assess(self, policy_input: dict[str, str]) -> dict:
        self.inputs.append(deepcopy(policy_input))
        return deepcopy(self.assessment)


def request(changes: dict[str, str]) -> CaseResolutionRequest:
    return CaseResolutionRequest(
        case_id="CASE-1001",
        changes=changes,
        reason="Resolve the customer issue.",
        requested_by="local-test",
    )


def test_risk_assessment_is_deterministic() -> None:
    assert assess_case_update({"resolution_note": "Fixed"}).level == "low"
    assert assess_case_update({"owner": "Jordan"}).level == "medium"
    assert assess_case_update({"priority": "critical"}).level == "high"
    assert assess_case_update({"status": "resolved"}).level == "high"


@pytest.mark.asyncio
async def test_low_risk_update_applies_without_confirmation(tmp_path: Path) -> None:
    tools = FakeCaseTools()
    service = CaseResolutionWorkflowService(tools, tmp_path)

    envelope = await service.start(request({"resolution_note": "Fixed"}))

    assert envelope.state == "completed"
    assert envelope.result is not None
    assert envelope.result.risk == "low"
    assert envelope.result.confirmation_id.startswith("workflow-auto-")
    assert tools.case["resolution_note"] == "Fixed"
    assert tools.apply_calls == 1


@pytest.mark.asyncio
async def test_medium_risk_update_resumes_from_checkpoint(tmp_path: Path) -> None:
    tools = FakeCaseTools()
    started = await CaseResolutionWorkflowService(tools, tmp_path).start(
        request({"owner": "Jordan"})
    )

    assert started.state == "pending_confirmation"
    assert started.approval is not None
    assert started.approval.risk == "medium"
    assert tools.case["owner"] == "Avery"

    resumed = await CaseResolutionWorkflowService(tools, tmp_path).resume(
        workflow_id=started.workflow_id,
        checkpoint_id=started.checkpoint_id,
        request_id=started.request_id or "",
        response=CaseResolutionApprovalResponse(
            approved=True,
            confirmation_id="confirm-medium-1",
        ),
    )

    assert resumed.state == "completed"
    assert resumed.result is not None
    assert resumed.result.confirmation_id == "confirm-medium-1"
    assert tools.case["owner"] == "Jordan"
    assert tools.apply_calls == 1

    retried = await CaseResolutionWorkflowService(tools, tmp_path).resume(
        workflow_id=started.workflow_id,
        checkpoint_id=started.checkpoint_id,
        request_id=started.request_id or "",
        response=CaseResolutionApprovalResponse(
            approved=True,
            confirmation_id="confirm-medium-1",
        ),
    )

    assert retried == resumed
    assert tools.apply_calls == 1


@pytest.mark.asyncio
async def test_high_risk_rejection_does_not_create_proposal(tmp_path: Path) -> None:
    tools = FakeCaseTools()
    service = CaseResolutionWorkflowService(tools, tmp_path)
    started = await service.start(request({"status": "resolved"}))

    rejected = await service.resume(
        workflow_id=started.workflow_id,
        checkpoint_id=started.checkpoint_id,
        request_id=started.request_id or "",
        response=CaseResolutionApprovalResponse(
            approved=False,
            comment="Need customer confirmation first.",
        ),
    )

    assert rejected.state == "rejected"
    assert rejected.result is not None
    assert rejected.result.message == "Need customer confirmation first."
    assert tools.case["status"] == "open"
    assert tools.proposals == {}
    assert tools.apply_calls == 0


@pytest.mark.asyncio
async def test_policy_denial_rejects_before_confirmation_or_proposal(
    tmp_path: Path,
) -> None:
    tools = FakeCaseTools()
    policy = FakePolicyTools(
        {
            "decision": "deny",
            "risk": "high",
            "contradictions": ["Resolving a case requires a non-empty resolution note."],
            "rationale": "The proposed update conflicts with support-case policy.",
        }
    )
    service = CaseResolutionWorkflowService(tools, tmp_path, policy_tools=policy)

    rejected = await service.start(request({"status": "resolved"}))

    assert rejected.state == "rejected"
    assert rejected.result is not None
    assert rejected.result.risk == "high"
    assert "non-empty resolution note" in rejected.result.message
    assert policy.inputs == [
        {
            "current_status": "open",
            "current_priority": "high",
            "proposed_status": "resolved",
            "proposed_owner": "",
            "proposed_priority": "",
            "proposed_resolution_note": "",
        }
    ]
    assert tools.proposals == {}
    assert tools.apply_calls == 0


@pytest.mark.asyncio
async def test_approval_requires_confirmation_id(tmp_path: Path) -> None:
    tools = FakeCaseTools()
    service = CaseResolutionWorkflowService(tools, tmp_path)
    started = await service.start(request({"owner": "Jordan"}))

    with pytest.raises(ValueError, match="confirmation_id"):
        await service.resume(
            workflow_id=started.workflow_id,
            checkpoint_id=started.checkpoint_id,
            request_id=started.request_id or "",
            response=CaseResolutionApprovalResponse(approved=True),
        )
