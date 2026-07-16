from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


CASE_STATUSES = {"open", "pending_customer", "escalated", "resolved"}
CASE_PRIORITIES = {"low", "medium", "high", "critical"}
UPDATABLE_FIELDS = {"status", "owner", "priority", "resolution_note"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SupportCase:
    case_id: str
    title: str
    customer: str
    status: str
    priority: str
    owner: str
    summary: str
    tags: list[str] = field(default_factory=list)
    resolution_note: str = ""
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in CASE_STATUSES:
            raise ValueError(f"Unsupported case status: {self.status}")
        if self.priority not in CASE_PRIORITIES:
            raise ValueError(f"Unsupported case priority: {self.priority}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CaseUpdateProposal:
    proposal_id: str
    case_id: str
    changes: dict[str, str]
    reason: str
    status: str = "proposed"
    created_at: str = field(default_factory=utc_now)
    applied_at: str | None = None
    confirmation_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_changes(changes: dict[str, str | None]) -> dict[str, str]:
    clean = {
        key: value.strip()
        for key, value in changes.items()
        if value is not None and value.strip()
    }
    if not clean:
        raise ValueError("At least one case field must be changed.")
    unsupported = clean.keys() - UPDATABLE_FIELDS
    if unsupported:
        raise ValueError(f"Unsupported case update fields: {sorted(unsupported)}")
    if "status" in clean and clean["status"] not in CASE_STATUSES:
        raise ValueError(f"Unsupported case status: {clean['status']}")
    if "priority" in clean and clean["priority"] not in CASE_PRIORITIES:
        raise ValueError(f"Unsupported case priority: {clean['priority']}")
    return clean
