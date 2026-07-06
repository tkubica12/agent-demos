from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


StreamDeltaHandler = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    conversation_id: str
    user_id: str
    source: str
    must_answer: bool
    context: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
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
