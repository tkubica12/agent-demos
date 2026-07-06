from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openclaw_gateway.paths import data_dir, workspace_dir


def openclaw_home_dir() -> Path:
    return Path(os.getenv("OPENCLAW_HOME_DIR", str(data_dir() / "home"))).expanduser()


def openclaw_state_dir() -> Path:
    return openclaw_home_dir() / ".openclaw"


def openclaw_config_path() -> Path:
    return Path(os.getenv("OPENCLAW_CONFIG_PATH", str(openclaw_state_dir() / "openclaw.json"))).expanduser()


def openclaw_env() -> dict[str, str]:
    env = os.environ.copy()
    home = openclaw_home_dir()
    state = openclaw_state_dir()
    config_path = openclaw_config_path()
    env["HOME"] = str(home)
    env["OPENCLAW_CONFIG_PATH"] = str(config_path)
    env["OPENCLAW_WORKSPACE_DIR"] = str(workspace_dir())
    env.setdefault("OPENCLAW_AGENT_DIR", str(state / "agents"))
    return env


def _model_config(gateway: bool = False) -> dict[str, Any]:
    provider_id = os.getenv("OPENCLAW_MODEL_PROVIDER_ID", "foundry")
    model_id = os.getenv("OPENCLAW_MODEL_ID") or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5-4-mini")
    base_url = os.getenv("OPENCLAW_MODEL_BASE_URL")
    api_key_env = os.getenv("OPENCLAW_MODEL_API_KEY_ENV", "OPENCLAW_MODEL_API_KEY")
    auth_header_name = os.getenv("OPENCLAW_MODEL_AUTH_HEADER_NAME", "Authorization")
    private_incidents_mcp_url = os.getenv("PRIVATE_INCIDENTS_MCP_URL", "").strip()
    private_incidents_mcp_static_key = os.getenv("PRIVATE_INCIDENTS_MCP_STATIC_KEY", "demo-static-key")

    provider: dict[str, Any] = {
        "api": os.getenv("OPENCLAW_MODEL_API", "openai-completions"),
        "models": [
            {
                "id": model_id,
                "name": model_id,
                "input": ["text"],
                "contextWindow": int(os.getenv("OPENCLAW_MODEL_CONTEXT_WINDOW", "128000")),
                "maxTokens": int(os.getenv("OPENCLAW_MODEL_MAX_TOKENS", "8192")),
            }
        ],
    }
    if base_url:
        provider["baseUrl"] = base_url

    if auth_header_name.lower() == "api-key":
        provider["headers"] = {"api-key": f"${{{api_key_env}}}"}
    else:
        provider["apiKey"] = f"${{{api_key_env}}}"

    config: dict[str, Any] = {
        "agents": {
            "defaults": {
                "workspace": str(workspace_dir()),
                "model": {"primary": f"{provider_id}/{model_id}"},
                "models": {f"{provider_id}/{model_id}": {"alias": "Azure model"}},
                "memorySearch": {"provider": "none"},
                "sandbox": {"mode": "off"},
            }
        },
        "models": {
            "mode": "merge",
            "providers": {provider_id: provider},
        },
        "tools": {"profile": "coding"},
        "session": {"dmScope": "per-channel-peer"},
    }
    if private_incidents_mcp_url:
        config["mcp"] = {
            "servers": {
                "private-incidents": {
                    "url": private_incidents_mcp_url,
                    "transport": "streamable-http",
                    "connectTimeout": 5,
                    "timeout": 20,
                    "supportsParallelToolCalls": True,
                    "headers": {
                        "Authorization": "Bearer ${PRIVATE_INCIDENTS_MCP_STATIC_KEY}",
                    },
                },
            },
        }
        config["mcp"]["servers"]["private-incidents"]["headers"] = {"Authorization": f"Bearer {private_incidents_mcp_static_key}"}
        config["tools"]["alsoAllow"] = ["group:plugins", "bundle-mcp"]
    if gateway:
        allowed_origins = [
            "http://localhost:18789",
            "http://127.0.0.1:18789",
        ]
        extra_origins = os.getenv("OPENCLAW_CONTROL_UI_ALLOWED_ORIGINS", "")
        for origin in extra_origins.split(","):
            origin = origin.strip().rstrip("/")
            if not origin:
                continue
            parsed = urlparse(origin)
            allowed_origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else origin
            if allowed_origin not in allowed_origins:
                allowed_origins.append(allowed_origin)

        control_ui: dict[str, Any] = {"enabled": True, "allowedOrigins": allowed_origins}
        if os.getenv("OPENCLAW_CONTROL_UI_ALLOW_HOST_HEADER_ORIGIN_FALLBACK", "").lower() in {"1", "true", "yes"}:
            control_ui["dangerouslyAllowHostHeaderOriginFallback"] = True
        if os.getenv("OPENCLAW_CONTROL_UI_DISABLE_DEVICE_AUTH", "").lower() in {"1", "true", "yes"}:
            control_ui["dangerouslyDisableDeviceAuth"] = True

        config["gateway"] = {
            "mode": "local",
            "bind": os.getenv("OPENCLAW_GATEWAY_BIND", "lan"),
            "port": int(os.getenv("PORT", "18789")),
            "trustedProxies": [
                "127.0.0.1",
                "::1",
                "10.0.0.0/8",
                "172.16.0.0/12",
            ],
            "auth": {
                "mode": "token",
                "token": "${OPENCLAW_GATEWAY_TOKEN}",
            },
            "controlUi": control_ui,
        }
    return config


def ensure_openclaw_files(gateway: bool = False) -> dict[str, str]:
    home = openclaw_home_dir()
    state = openclaw_state_dir()
    workspace = workspace_dir()
    config_path = openclaw_config_path()
    home.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    if not (workspace / "MEMORY.md").exists():
        (workspace / "MEMORY.md").write_text(
            "# Long-term memory\n\nThis OpenClaw workspace is persisted on Azure filesystem storage.\n",
            encoding="utf-8",
        )
    (workspace / "memory").mkdir(parents=True, exist_ok=True)

    generated = _model_config(gateway=gateway)
    config_path.write_text(json.dumps(generated, indent=2), encoding="utf-8")
    return {
        "homeDir": str(home),
        "stateDir": str(state),
        "configPath": str(config_path),
        "workspaceDir": str(workspace),
    }
