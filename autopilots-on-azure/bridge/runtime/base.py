from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


StreamDeltaHandler = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class AgentAuthContext:
    selected_mode: str
    available_modes: tuple[str, ...]
    conversation_boundary: str
    invoking_user_id: str = ""
    invoking_user_name: str = ""
    agent_instance_id: str = ""
    agent_user_id: str = ""
    agent_user_principal_name: str = ""
    obo_available: bool = False

    def as_metadata(self) -> dict[str, Any]:
        return {
            "selectedAuthMode": self.selected_mode,
            "availableAuthModes": list(self.available_modes),
            "conversationBoundary": self.conversation_boundary,
            "invokingUserId": self.invoking_user_id,
            "invokingUserName": self.invoking_user_name,
            "agentInstanceId": self.agent_instance_id,
            "agentUserId": self.agent_user_id,
            "agentUserPrincipalName": self.agent_user_principal_name,
            "oboAvailable": self.obo_available,
        }


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    conversation_id: str
    user_id: str
    source: str
    must_answer: bool
    context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    auth: AgentAuthContext | None = None
    on_delta: StreamDeltaHandler | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class AgentResponse:
    text: str
    reaction: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AgentRuntimeAdapter(Protocol):
    @property
    def runtime_kind(self) -> str:
        ...

    async def invoke(self, request: AgentRequest) -> AgentResponse:
        ...
