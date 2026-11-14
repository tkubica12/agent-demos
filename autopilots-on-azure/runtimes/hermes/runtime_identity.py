"""Select Agent 365 authentication without requiring it for channel-only runtimes."""

import os


AGENT_IDENTITY_ENVIRONMENTS = (
    "AGENT365_TENANT_ID",
    "AGENT365_BLUEPRINT_CLIENT_ID",
    "AGENT365_AGENT_IDENTITY_CLIENT_ID",
)


def agentic_auth_enabled() -> bool:
    """Honor an explicit switch; existing Agent 365 runtimes supply identity IDs instead."""
    configured = os.getenv("USE_AGENTIC_AUTH")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return all(os.getenv(name, "").strip() for name in AGENT_IDENTITY_ENVIRONMENTS)
