from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from dataclasses import replace
from typing import Protocol

from azure.core import MatchConditions
from azure.core.credentials import TokenCredential
from azure.data.tables import TableEntity, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

from .models import CaseUpdateProposal, SupportCase, utc_now, validate_changes
from .sample_data import SAMPLE_CASES


TABLE_NAME = "supportcases"


class CaseRepository(Protocol):
    def seed(self, cases: list[SupportCase]) -> None: ...

    def search(
        self,
        query: str = "",
        status: str | None = None,
        priority: str | None = None,
        limit: int = 10,
    ) -> list[SupportCase]: ...

    def get(self, case_id: str) -> SupportCase | None: ...

    def propose(self, case_id: str, changes: dict[str, str | None], reason: str) -> CaseUpdateProposal: ...

    def apply(self, proposal_id: str, confirmation_id: str) -> tuple[SupportCase, CaseUpdateProposal]: ...


def _case_key(case_id: str) -> str:
    return case_id.strip().upper()


def _proposal_parts(proposal_id: str) -> tuple[str, str]:
    case_id, separator, proposal_uuid = proposal_id.partition(":")
    if not separator or not case_id or not proposal_uuid:
        raise ValueError("proposal_id must use the '<case-id>:<proposal-uuid>' format.")
    return _case_key(case_id), proposal_uuid


class InMemoryCaseRepository:
    def __init__(self, cases: list[SupportCase] | None = None) -> None:
        self._cases: dict[str, SupportCase] = {}
        self._proposals: dict[str, CaseUpdateProposal] = {}
        self._audit: list[dict[str, str]] = []
        self._lock = threading.Lock()
        self.seed(cases or SAMPLE_CASES)

    def seed(self, cases: list[SupportCase]) -> None:
        with self._lock:
            for case in cases:
                self._cases[_case_key(case.case_id)] = deepcopy(case)

    def search(
        self,
        query: str = "",
        status: str | None = None,
        priority: str | None = None,
        limit: int = 10,
    ) -> list[SupportCase]:
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50.")
        needle = query.strip().lower()
        matches = []
        for case in self._cases.values():
            searchable = " ".join(
                [case.case_id, case.title, case.customer, case.summary, " ".join(case.tags)]
            ).lower()
            if needle and needle not in searchable:
                continue
            if status and case.status != status:
                continue
            if priority and case.priority != priority:
                continue
            matches.append(deepcopy(case))
        matches.sort(key=lambda case: (case.status == "resolved", case.case_id))
        return matches[:limit]

    def get(self, case_id: str) -> SupportCase | None:
        case = self._cases.get(_case_key(case_id))
        return deepcopy(case) if case else None

    def propose(
        self,
        case_id: str,
        changes: dict[str, str | None],
        reason: str,
    ) -> CaseUpdateProposal:
        key = _case_key(case_id)
        if key not in self._cases:
            raise KeyError(f"Case not found: {key}")
        if not reason.strip():
            raise ValueError("reason must be a non-empty string.")
        proposal = CaseUpdateProposal(
            proposal_id=f"{key}:{uuid.uuid4()}",
            case_id=key,
            changes=validate_changes(changes),
            reason=reason.strip(),
        )
        with self._lock:
            self._proposals[proposal.proposal_id] = deepcopy(proposal)
        return proposal

    def apply(
        self,
        proposal_id: str,
        confirmation_id: str,
    ) -> tuple[SupportCase, CaseUpdateProposal]:
        if len(confirmation_id.strip()) < 8:
            raise ValueError("confirmation_id must be at least 8 characters.")
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                raise KeyError(f"Proposal not found: {proposal_id}")
            if proposal.status != "proposed":
                raise ValueError(f"Proposal is already {proposal.status}.")
            case = self._cases[proposal.case_id]
            updated_case = replace(
                case,
                **proposal.changes,
                updated_at=utc_now(),
            )
            applied = replace(
                proposal,
                status="applied",
                applied_at=utc_now(),
                confirmation_id=confirmation_id.strip(),
            )
            self._cases[proposal.case_id] = updated_case
            self._proposals[proposal_id] = applied
            self._audit.append(
                {
                    "case_id": proposal.case_id,
                    "proposal_id": proposal_id,
                    "confirmation_id": confirmation_id.strip(),
                    "created_at": utc_now(),
                }
            )
        return deepcopy(updated_case), deepcopy(applied)


def _case_to_entity(case: SupportCase) -> TableEntity:
    return TableEntity(
        {
            "PartitionKey": case.case_id,
            "RowKey": "case",
            "title": case.title,
            "customer": case.customer,
            "status": case.status,
            "priority": case.priority,
            "owner": case.owner,
            "summary": case.summary,
            "tags_json": json.dumps(case.tags),
            "resolution_note": case.resolution_note,
            "updated_at": case.updated_at,
        }
    )


def _entity_to_case(entity: TableEntity) -> SupportCase:
    return SupportCase(
        case_id=str(entity["PartitionKey"]),
        title=str(entity["title"]),
        customer=str(entity["customer"]),
        status=str(entity["status"]),
        priority=str(entity["priority"]),
        owner=str(entity["owner"]),
        summary=str(entity["summary"]),
        tags=json.loads(str(entity.get("tags_json", "[]"))),
        resolution_note=str(entity.get("resolution_note", "")),
        updated_at=str(entity["updated_at"]),
    )


def _proposal_to_entity(proposal: CaseUpdateProposal) -> TableEntity:
    _, proposal_uuid = _proposal_parts(proposal.proposal_id)
    return TableEntity(
        {
            "PartitionKey": proposal.case_id,
            "RowKey": f"proposal:{proposal_uuid}",
            "proposal_id": proposal.proposal_id,
            "changes_json": json.dumps(proposal.changes, sort_keys=True),
            "reason": proposal.reason,
            "status": proposal.status,
            "created_at": proposal.created_at,
            "applied_at": proposal.applied_at or "",
            "confirmation_id": proposal.confirmation_id or "",
        }
    )


def _entity_to_proposal(entity: TableEntity) -> CaseUpdateProposal:
    return CaseUpdateProposal(
        proposal_id=str(entity["proposal_id"]),
        case_id=str(entity["PartitionKey"]),
        changes=json.loads(str(entity["changes_json"])),
        reason=str(entity["reason"]),
        status=str(entity["status"]),
        created_at=str(entity["created_at"]),
        applied_at=str(entity.get("applied_at", "")) or None,
        confirmation_id=str(entity.get("confirmation_id", "")) or None,
    )


class TableCaseRepository:
    def __init__(
        self,
        endpoint: str,
        credential: TokenCredential,
        table_name: str = TABLE_NAME,
    ) -> None:
        service = TableServiceClient(endpoint=endpoint, credential=credential)
        self._table = service.get_table_client(table_name)

    def seed(self, cases: list[SupportCase]) -> None:
        for case in cases:
            self._table.upsert_entity(_case_to_entity(case), mode=UpdateMode.REPLACE)

    def search(
        self,
        query: str = "",
        status: str | None = None,
        priority: str | None = None,
        limit: int = 10,
    ) -> list[SupportCase]:
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50.")
        needle = query.strip().lower()
        matches = []
        for entity in self._table.query_entities("RowKey eq 'case'"):
            case = _entity_to_case(entity)
            searchable = " ".join(
                [case.case_id, case.title, case.customer, case.summary, " ".join(case.tags)]
            ).lower()
            if needle and needle not in searchable:
                continue
            if status and case.status != status:
                continue
            if priority and case.priority != priority:
                continue
            matches.append(case)
        matches.sort(key=lambda case: (case.status == "resolved", case.case_id))
        return matches[:limit]

    def get(self, case_id: str) -> SupportCase | None:
        key = _case_key(case_id)
        try:
            return _entity_to_case(self._table.get_entity(key, "case"))
        except Exception as exc:
            from azure.core.exceptions import ResourceNotFoundError

            if isinstance(exc, ResourceNotFoundError):
                return None
            raise

    def propose(
        self,
        case_id: str,
        changes: dict[str, str | None],
        reason: str,
    ) -> CaseUpdateProposal:
        key = _case_key(case_id)
        if self.get(key) is None:
            raise KeyError(f"Case not found: {key}")
        if not reason.strip():
            raise ValueError("reason must be a non-empty string.")
        proposal = CaseUpdateProposal(
            proposal_id=f"{key}:{uuid.uuid4()}",
            case_id=key,
            changes=validate_changes(changes),
            reason=reason.strip(),
        )
        self._table.create_entity(_proposal_to_entity(proposal))
        return proposal

    def apply(
        self,
        proposal_id: str,
        confirmation_id: str,
    ) -> tuple[SupportCase, CaseUpdateProposal]:
        if len(confirmation_id.strip()) < 8:
            raise ValueError("confirmation_id must be at least 8 characters.")
        case_id, proposal_uuid = _proposal_parts(proposal_id)
        case_entity = self._table.get_entity(case_id, "case")
        proposal_entity = self._table.get_entity(case_id, f"proposal:{proposal_uuid}")
        proposal = _entity_to_proposal(proposal_entity)
        if proposal.status != "proposed":
            raise ValueError(f"Proposal is already {proposal.status}.")

        current_case = _entity_to_case(case_entity)
        updated_case = replace(
            current_case,
            **proposal.changes,
            updated_at=utc_now(),
        )
        applied = replace(
            proposal,
            status="applied",
            applied_at=utc_now(),
            confirmation_id=confirmation_id.strip(),
        )
        audit_entity = TableEntity(
            {
                "PartitionKey": case_id,
                "RowKey": f"audit:{uuid.uuid4()}",
                "action": "case.update_applied",
                "proposal_id": proposal_id,
                "confirmation_id": confirmation_id.strip(),
                "changes_json": json.dumps(proposal.changes, sort_keys=True),
                "created_at": utc_now(),
            }
        )
        self._table.submit_transaction(
            [
                (
                    "update",
                    _case_to_entity(updated_case),
                    {
                        "mode": UpdateMode.REPLACE,
                        "etag": case_entity.metadata["etag"],
                        "match_condition": MatchConditions.IfNotModified,
                    },
                ),
                (
                    "update",
                    _proposal_to_entity(applied),
                    {
                        "mode": UpdateMode.REPLACE,
                        "etag": proposal_entity.metadata["etag"],
                        "match_condition": MatchConditions.IfNotModified,
                    },
                ),
                ("create", audit_entity),
            ]
        )
        return updated_case, applied


def create_repository() -> CaseRepository:
    backend = os.getenv("CASE_STORE_BACKEND", "table").strip().lower()
    if backend == "memory":
        return InMemoryCaseRepository()
    if backend != "table":
        raise RuntimeError(f"Unsupported CASE_STORE_BACKEND: {backend}")
    endpoint = os.getenv("CASE_TABLE_ENDPOINT")
    if not endpoint:
        raise RuntimeError("CASE_TABLE_ENDPOINT must be set for the table backend.")
    credential = DefaultAzureCredential(
        managed_identity_client_id=os.getenv("AZURE_CLIENT_ID"),
    )
    repository = TableCaseRepository(endpoint, credential)
    if not repository.search(limit=1):
        repository.seed(SAMPLE_CASES)
    return repository
