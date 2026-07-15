from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from blueprint import BlueprintInstall, install_or_update_blueprint, settings_from_environment
from learning import (
    append_candidates,
    build_learning_packet,
    ensure_learning_state,
    ensure_private_cache,
    render_hot_learning_skill,
)


DEFAULT_HERMES_HOME = "/data/hermes"
DEFAULT_GATEWAY_PORT = 9119
DEFAULT_FOUNDRY_PROXY_PORT = 18080
DEFAULT_AGENT_MCP_PROXY_PORT = 18081


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def hermes_home() -> Path:
    return Path(os.getenv("HERMES_HOME", DEFAULT_HERMES_HOME)).expanduser()


def api_server_port() -> int:
    return int(os.getenv("API_SERVER_PORT", os.getenv("PORT", "8642")))


def gateway_port() -> int:
    return int(os.getenv("HERMES_GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT)))


def gateway_api_port() -> int:
    return gateway_port() if bool_env("HERMES_HEALTH_WRAPPER", False) else api_server_port()


def write_env_file(home: Path) -> Path:
    configure_model_environment()
    env_path = home / ".env"
    values = {
        "API_SERVER_ENABLED": os.getenv("API_SERVER_ENABLED", "true"),
        "API_SERVER_HOST": os.getenv("API_SERVER_HOST", "0.0.0.0"),
        "API_SERVER_PORT": str(gateway_api_port()),
        "API_SERVER_KEY": os.getenv("API_SERVER_KEY", ""),
        "HERMES_HOME": str(home),
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", ""),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "AZURE_FOUNDRY_BASE_URL": os.getenv("AZURE_FOUNDRY_BASE_URL", ""),
        "AZURE_FOUNDRY_API_KEY": os.getenv("AZURE_FOUNDRY_API_KEY", ""),
        "HERMES_INFERENCE_MODEL": os.getenv("HERMES_INFERENCE_MODEL", ""),
        "HERMES_MODEL_PROVIDER": os.getenv("HERMES_MODEL_PROVIDER", ""),
        "HERMES_MODEL": os.getenv("HERMES_MODEL", ""),
        "PRIVATE_INCIDENTS_MCP_URL": os.getenv("PRIVATE_INCIDENTS_MCP_URL", ""),
        "PUBLIC_SHIPMENTS_MCP_URL": os.getenv("PUBLIC_SHIPMENTS_MCP_URL", ""),
        "WORKIQ_MAIL_MCP_URL": os.getenv("WORKIQ_MAIL_MCP_URL", ""),
    }
    managed = {key: value for key, value in values.items() if value}
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    rendered: list[str] = []
    replaced: set[str] = set()
    for line in existing_lines:
        key, separator, _ = line.partition("=")
        if separator and key in values:
            if key in managed:
                rendered.append(f"{key}={managed[key]}")
                replaced.add(key)
            continue
        rendered.append(line)
    if rendered and rendered[-1]:
        rendered.append("")
    rendered.extend(f"{key}={value}" for key, value in managed.items() if key not in replaced)
    env_path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    return env_path


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def hermes_config(home: Path, base: dict[str, Any] | None = None) -> dict[str, Any]:
    configure_model_environment()
    api_port = gateway_api_port()
    runtime_config: dict[str, Any] = {
        "model": {
            "provider": os.getenv("HERMES_MODEL_PROVIDER", "azure-foundry"),
            "name": os.getenv("HERMES_MODEL", os.getenv("OPENCLAW_MODEL_ID", "gpt-5-6-terra")),
        },
        "gateway": {
            "platforms": {
                "api_server": {
                    "enabled": bool_env("API_SERVER_ENABLED", True),
                    "host": os.getenv("API_SERVER_HOST", "0.0.0.0"),
                    "port": api_port,
                    "api_key": os.getenv("API_SERVER_KEY", ""),
                }
            }
        },
        "paths": {
            "home": str(home),
            "workspace": str(home / "workspace"),
        },
    }
    mcp_servers: dict[str, Any] = {}
    private_mcp_url = os.getenv("PRIVATE_INCIDENTS_MCP_URL", "").strip()
    public_shipments_mcp_url = os.getenv("PUBLIC_SHIPMENTS_MCP_URL", "").strip()
    workiq_mail_mcp_url = os.getenv("WORKIQ_MAIL_MCP_URL", "").strip()
    if private_mcp_url:
        mcp_servers["private-incidents"] = {"url": private_mcp_url}
    if public_shipments_mcp_url:
        mcp_servers["public-shipments"] = {"url": public_shipments_mcp_url}
    if workiq_mail_mcp_url:
        mcp_servers["workiq-mail"] = {"url": workiq_mail_mcp_url}
    if mcp_servers:
        runtime_config["mcp_servers"] = mcp_servers
    config = _deep_merge(base or {}, runtime_config)
    configured_servers = config.get("mcp_servers")
    if isinstance(configured_servers, dict):
        for name in ("private-incidents", "public-shipments", "workiq-mail"):
            if name not in mcp_servers:
                configured_servers.pop(name, None)
        if not configured_servers:
            config.pop("mcp_servers", None)
    return config


def foundry_proxy_port() -> int:
    return int(os.getenv("FOUNDRY_PROXY_PORT", str(DEFAULT_FOUNDRY_PROXY_PORT)))


def configure_model_environment() -> None:
    if os.getenv("FOUNDRY_OPENAI_BASE_URL"):
        proxy_url = f"http://127.0.0.1:{foundry_proxy_port()}/v1"
        os.environ.setdefault("OPENAI_BASE_URL", proxy_url)
        os.environ.setdefault("OPENAI_API_KEY", "unused-managed-identity-token-proxy")
        os.environ.setdefault("AZURE_FOUNDRY_BASE_URL", proxy_url)
        os.environ.setdefault("AZURE_FOUNDRY_API_KEY", "unused-managed-identity-token-proxy")
        os.environ.setdefault("HERMES_MODEL_PROVIDER", "azure-foundry")
        model = os.getenv("HERMES_MODEL") or os.getenv("OPENCLAW_MODEL_ID") or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or "gpt-5-6-terra"
        os.environ.setdefault("HERMES_MODEL", model)
        os.environ.setdefault("HERMES_INFERENCE_MODEL", model)


def start_foundry_proxy() -> subprocess.Popen | None:
    if not os.getenv("FOUNDRY_OPENAI_BASE_URL"):
        print("FOUNDRY_OPENAI_BASE_URL is not set; Foundry proxy is disabled.", flush=True)
        return None
    port = str(foundry_proxy_port())
    print(f"Starting Foundry managed-identity proxy on 127.0.0.1:{port}", flush=True)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "foundry_token_proxy:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
        ],
        env=os.environ.copy(),
    )


def start_agent_mcp_proxy() -> subprocess.Popen | None:
    if not os.getenv("AGENT_MCP_SERVERS_JSON"):
        print("AGENT_MCP_SERVERS_JSON is not set; Agent Identity MCP adapter is disabled.", flush=True)
        return None
    port = os.getenv("AGENT_MCP_PROXY_PORT", str(DEFAULT_AGENT_MCP_PROXY_PORT))
    print(f"Starting Agent Identity MCP adapter on 127.0.0.1:{port}", flush=True)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "autopilots_identity.mcp_proxy:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
        ],
        env=os.environ.copy(),
    )


def write_config(home: Path) -> Path:
    config_path = home / "config.yaml"
    base: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"{config_path} must contain a YAML mapping.")
        base = loaded
    config_path.write_text(yaml.safe_dump(hermes_config(home, base), sort_keys=False), encoding="utf-8")
    return config_path


def activate_profile(home: Path, name: str) -> Path:
    active_path = home / "active_profile"
    temporary = active_path.with_suffix(".tmp")
    temporary.write_text(f"{name}\n", encoding="utf-8")
    temporary.replace(active_path)
    os.environ["HERMES_PROFILE"] = name
    return active_path


def start_gateway(profile_home: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_home)
    command = [
        "hermes",
        "gateway",
        "run",
        "--accept-hooks",
    ]
    return subprocess.Popen(command, env=env)


def create_health_app(
    home: Path,
    profile_home: Path,
    blueprint: BlueprintInstall | None,
    gateway: subprocess.Popen | None,
) -> FastAPI:
    app = FastAPI(title="Hermes ACA Sandbox runtime")
    blueprint_health = None
    if blueprint:
        blueprint_health = {
            "name": blueprint.manifest["blueprintName"],
            "version": blueprint.manifest["blueprintVersion"],
            "commit": blueprint.manifest["blueprintCommit"],
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok" if gateway is None or gateway.poll() is None else "gateway-exited",
            "runtime": "hermes",
            "hermesHome": str(home),
            "profileHome": str(profile_home),
            "profileName": os.getenv("HERMES_PROFILE", "default"),
            "configExists": (profile_home / "config.yaml").exists(),
            "envExists": (profile_home / ".env").exists(),
            "blueprint": blueprint_health,
            "gatewayPort": gateway_port(),
            "apiServerPort": api_server_port(),
            "gatewayPid": gateway.pid if gateway and gateway.poll() is None else None,
        }

    @app.get("/health/detailed")
    def health_detailed() -> dict[str, Any]:
        return health()

    def require_internal_key(request: Request) -> None:
        expected = os.getenv("API_SERVER_KEY", "")
        supplied = request.headers.get("x-autopilot-key", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="A valid X-Autopilot-Key header is required.")

    @app.get("/internal/learning/packet")
    def learning_packet(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        return build_learning_packet(profile_home)

    @app.post("/internal/learning/candidates")
    async def learning_candidates(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list) or len(candidates) > 10:
            raise HTTPException(status_code=400, detail="candidates must be an array with at most 10 items.")
        return append_candidates(profile_home, candidates)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        target = f"http://127.0.0.1:{gateway_port()}/{path}"
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(
                request.method,
                target,
                content=await request.body(),
                headers={key: value for key, value in request.headers.items() if key.lower() != "host"},
            )
        if response.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse(content=response.json(), status_code=response.status_code)
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return app


def main() -> None:
    home = hermes_home()
    configure_model_environment()
    home.mkdir(parents=True, exist_ok=True)
    blueprint = None
    profile_home = home
    blueprint_settings = settings_from_environment()
    if blueprint_settings:
        blueprint = install_or_update_blueprint(home, blueprint_settings)
        profile_home = blueprint.profile_home
        activate_profile(home, blueprint_settings.name)
        action = "updated" if blueprint.changed else "reused"
        print(
            f"Hermes blueprint {action}: {blueprint_settings.name}@{blueprint_settings.version or blueprint_settings.commit}",
            flush=True,
        )
    profile_home.mkdir(parents=True, exist_ok=True)
    (profile_home / "workspace").mkdir(parents=True, exist_ok=True)
    ensure_learning_state(profile_home)
    ensure_private_cache(profile_home)
    render_hot_learning_skill(profile_home)
    env_path = write_env_file(profile_home)
    config_path = write_config(profile_home)
    print(f"Hermes home: {home}", flush=True)
    print(f"Hermes profile home: {profile_home}", flush=True)
    print(f"Hermes env: {env_path}", flush=True)
    print(f"Hermes config: {config_path}", flush=True)

    foundry_proxy = start_foundry_proxy()
    mcp_proxy = start_agent_mcp_proxy()
    if foundry_proxy or mcp_proxy:
        time.sleep(2)
    gateway = None
    if bool_env("HERMES_START_GATEWAY", True):
        try:
            gateway = start_gateway(profile_home)
            print(f"Started Hermes gateway pid={gateway.pid}", flush=True)
            time.sleep(3)
        except Exception as exc:
            print(f"Failed to start Hermes gateway: {exc}", file=sys.stderr, flush=True)

    try:
        if bool_env("HERMES_HEALTH_WRAPPER", False):
            app = create_health_app(home, profile_home, blueprint, gateway)
            uvicorn.run(app, host=os.getenv("API_SERVER_HOST", "0.0.0.0"), port=api_server_port())
            return

        if gateway is None:
            app = create_health_app(home, profile_home, blueprint, gateway)
            uvicorn.run(app, host=os.getenv("API_SERVER_HOST", "0.0.0.0"), port=api_server_port())
            return

        raise SystemExit(gateway.wait())
    finally:
        for process in (mcp_proxy, foundry_proxy):
            if process and process.poll() is None:
                process.terminate()


if __name__ == "__main__":
    main()
