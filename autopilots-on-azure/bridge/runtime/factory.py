from __future__ import annotations

import os

from bridge.runtime.base import AgentRuntimeAdapter
from bridge.runtime.openclaw import OpenClawRuntimeAdapter


def runtime_kind_from_env() -> str:
    return os.getenv("AGENT_RUNTIME", "openclaw").strip().lower() or "openclaw"


def create_runtime_adapter() -> AgentRuntimeAdapter:
    runtime_kind = runtime_kind_from_env()
    if runtime_kind == "openclaw":
        return OpenClawRuntimeAdapter()
    raise ValueError(f"Unsupported AGENT_RUNTIME '{runtime_kind}'. Hermes support starts in later milestones.")
