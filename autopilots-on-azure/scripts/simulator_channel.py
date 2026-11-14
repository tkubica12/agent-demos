from __future__ import annotations

import re
from urllib.parse import urlsplit


def simulator_worker_environment(config: dict, infrastructure: dict, previous: dict[str, str]) -> dict[str, str]:
    origin = str(config["simulator_url"]).rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("simulator_url must be an HTTPS origin without a path or credentials.")
    if config["user_scheduling_enabled"] or config["servicebus_dream_enabled"]:
        raise ValueError("Simulator scheduling requires a separate channel-aware scheduling implementation.")
    if config.get("demo_mcp_enabled"):
        raise ValueError("Simulator deployments do not provision Agent 365 demo MCP authorization.")
    environment = {
        key: value for key, value in previous.items()
        if not key.startswith(("AGENT365_", "WORKIQ_", "CONNECTIONS__", "CONNECTIONSMAP__", "PRIVATE_INCIDENTS_", "PUBLIC_SHIPMENTS_"))
    }
    environment.update({
        "USE_AGENTIC_AUTH": "false",
        "ATTACHMENT_DOWNLOAD_ORIGINS": origin,
        "DOCUMENT_RETRY_ENABLED": "false",
    })
    if config["simulator_auth_mode"] == "demo":
        key = str(config["simulator_demo_key"])
        if len(key) < 32 or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError("Simulator DEMO requires a URL-safe per-agent key of at least 32 characters.")
        environment.update({"DEMO_CHANNEL_KEY": key, "DEMO_SERVICE_URL": f"{origin}/demo/{key}"})
    else:
        agent = str(config["simulator_agent_client_id"])
        channel = str(config["simulator_channel_client_id"])
        if not agent or not channel:
            raise ValueError("Simulator Entra mode requires distinct agent and channel client IDs.")
        if agent == channel:
            raise ValueError("Simulator agent and channel applications must be distinct.")
        prefix = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__"
        environment.update({
            prefix + "AUTHTYPE": "FederatedCredentials",
            prefix + "CLIENTID": agent,
            prefix + "TENANTID": infrastructure["tenant_id"],
            prefix + "FEDERATEDCLIENTID": infrastructure["sandbox_groups"]["gateway"]["identity_client_id"],
            prefix + "SCOPES__0": channel + "/.default",
            "CONNECTIONSMAP__0__SERVICEURL": "^" + re.escape(origin) + "/?$",
            "CONNECTIONSMAP__0__CONNECTION": "SERVICE_CONNECTION",
        })
    return environment


def simulator_environment(config: dict, binding: dict, identities: dict, agents: list[dict]) -> dict[str, str]:
    import json

    origin = binding["url"].rstrip("/")
    environment = {
        "ENV": "production",
        "TSIM_AUTH_MODE": config.get("auth_mode", "entra"),
        "TSIM_TEST_IDP": "0",
        "TSIM_DATABASE_URL": "sqlite:////mnt/tsim/simulator.db",
        "TSIM_STORAGE_PATH": "/mnt/tsim/files",
        "TSIM_ALLOWED_ORIGINS": origin,
        "TSIM_REDIRECT_URI": origin + "/",
        "TSIM_SERVICE_URL": origin,
        "TSIM_AGENTS_JSON": json.dumps(agents, separators=(",", ":")),
    }
    if environment["TSIM_AUTH_MODE"] == "demo":
        environment.update({
            "TSIM_DEMO_ACCESS_CODE": config["demo_access_code"],
            "TSIM_DEMO_EXPIRES_AT": config["demo_expires_at"],
            "TSIM_DEMO_ACKNOWLEDGE": "test-data-only",
        })
    else:
        client = identities["spa"]["client_id"]
        environment.update({
            "TSIM_TENANT_ID": config["tenant_id"],
            "TSIM_CLIENT_ID": client,
            "TSIM_SCOPE": f"api://{client}/access_as_user",
            "TSIM_REDIRECT_URI": origin + "/",
            "TSIM_CHANNEL_CLIENT_ID": identities["channel"]["client_id"],
            "TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID": binding["identity_client_id"],
        })
    return environment
