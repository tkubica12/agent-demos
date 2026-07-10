from __future__ import annotations

import json
import os
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


def write_env_file(home: Path) -> Path:
    configure_model_environment()
    env_path = home / ".env"
    values = {
        "API_SERVER_ENABLED": os.getenv("API_SERVER_ENABLED", "true"),
        "API_SERVER_HOST": os.getenv("API_SERVER_HOST", "0.0.0.0"),
        "API_SERVER_PORT": str(api_server_port()),
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
    env_path.write_text("\n".join(f"{key}={value}" for key, value in values.items() if value) + "\n", encoding="utf-8")
    return env_path


def hermes_config(home: Path) -> dict[str, Any]:
    configure_model_environment()
    api_port = api_server_port()
    config: dict[str, Any] = {
        "model": {
            "provider": os.getenv("HERMES_MODEL_PROVIDER", "azure-foundry"),
            "name": os.getenv("HERMES_MODEL", os.getenv("OPENCLAW_MODEL_ID", "gpt-5-4-mini")),
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
        config["mcp_servers"] = mcp_servers
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
        model = os.getenv("HERMES_MODEL") or os.getenv("OPENCLAW_MODEL_ID") or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or "gpt-5-4-mini"
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
    config_path.write_text(yaml.safe_dump(hermes_config(home), sort_keys=False), encoding="utf-8")
    return config_path


def start_gateway(home: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    command = [
        "hermes",
        "gateway",
        "run",
        "--accept-hooks",
    ]
    return subprocess.Popen(command, env=env)


def create_health_app(home: Path, gateway: subprocess.Popen | None) -> FastAPI:
    app = FastAPI(title="Hermes ACA Sandbox runtime")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok" if gateway is None or gateway.poll() is None else "gateway-exited",
            "runtime": "hermes",
            "hermesHome": str(home),
            "configExists": (home / "config.yaml").exists(),
            "envExists": (home / ".env").exists(),
            "gatewayPort": gateway_port(),
            "apiServerPort": api_server_port(),
            "gatewayPid": gateway.pid if gateway and gateway.poll() is None else None,
        }

    @app.get("/health/detailed")
    def health_detailed() -> dict[str, Any]:
        return health()

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
            return response.json()
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return app


def main() -> None:
    home = hermes_home()
    configure_model_environment()
    home.mkdir(parents=True, exist_ok=True)
    (home / "workspace").mkdir(parents=True, exist_ok=True)
    env_path = write_env_file(home)
    config_path = write_config(home)
    print(f"Hermes home: {home}", flush=True)
    print(f"Hermes env: {env_path}", flush=True)
    print(f"Hermes config: {config_path}", flush=True)

    foundry_proxy = start_foundry_proxy()
    mcp_proxy = start_agent_mcp_proxy()
    if foundry_proxy or mcp_proxy:
        time.sleep(2)
    gateway = None
    if bool_env("HERMES_START_GATEWAY", True):
        try:
            gateway = start_gateway(home)
            print(f"Started Hermes gateway pid={gateway.pid}", flush=True)
            time.sleep(3)
        except Exception as exc:
            print(f"Failed to start Hermes gateway: {exc}", file=sys.stderr, flush=True)

    try:
        if bool_env("HERMES_HEALTH_WRAPPER", False):
            app = create_health_app(home, gateway)
            uvicorn.run(app, host=os.getenv("API_SERVER_HOST", "0.0.0.0"), port=api_server_port())
            return

        if gateway is None:
            app = create_health_app(home, gateway)
            uvicorn.run(app, host=os.getenv("API_SERVER_HOST", "0.0.0.0"), port=api_server_port())
            return

        raise SystemExit(gateway.wait())
    finally:
        for process in (mcp_proxy, foundry_proxy):
            if process and process.poll() is None:
                process.terminate()


if __name__ == "__main__":
    main()
