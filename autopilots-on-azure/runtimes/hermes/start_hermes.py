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
    env_path = home / ".env"
    values = {
        "API_SERVER_ENABLED": os.getenv("API_SERVER_ENABLED", "true"),
        "API_SERVER_HOST": os.getenv("API_SERVER_HOST", "0.0.0.0"),
        "API_SERVER_PORT": str(api_server_port()),
        "API_SERVER_KEY": os.getenv("API_SERVER_KEY", ""),
        "HERMES_HOME": str(home),
        "PRIVATE_INCIDENTS_MCP_URL": os.getenv("PRIVATE_INCIDENTS_MCP_URL", ""),
        "PRIVATE_INCIDENTS_MCP_STATIC_KEY": os.getenv("PRIVATE_INCIDENTS_MCP_STATIC_KEY", ""),
    }
    env_path.write_text("\n".join(f"{key}={value}" for key, value in values.items() if value) + "\n", encoding="utf-8")
    return env_path


def hermes_config(home: Path) -> dict[str, Any]:
    api_port = api_server_port()
    config: dict[str, Any] = {
        "model": {
            "provider": os.getenv("HERMES_MODEL_PROVIDER", "openai"),
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
    private_mcp_url = os.getenv("PRIVATE_INCIDENTS_MCP_URL", "").strip()
    if private_mcp_url:
        config["mcp_servers"] = {
            "private-incidents": {
                "url": private_mcp_url,
                "headers": {"Authorization": f"Bearer {os.getenv('PRIVATE_INCIDENTS_MCP_STATIC_KEY', 'demo-static-key')}"},
            }
        }
    return config


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
    home.mkdir(parents=True, exist_ok=True)
    (home / "workspace").mkdir(parents=True, exist_ok=True)
    env_path = write_env_file(home)
    config_path = write_config(home)
    print(f"Hermes home: {home}", flush=True)
    print(f"Hermes env: {env_path}", flush=True)
    print(f"Hermes config: {config_path}", flush=True)

    gateway = None
    if bool_env("HERMES_START_GATEWAY", True):
        try:
            gateway = start_gateway(home)
            print(f"Started Hermes gateway pid={gateway.pid}", flush=True)
            time.sleep(3)
        except Exception as exc:
            print(f"Failed to start Hermes gateway: {exc}", file=sys.stderr, flush=True)

    app = create_health_app(home, gateway)
    uvicorn.run(app, host=os.getenv("API_SERVER_HOST", "0.0.0.0"), port=api_server_port())


if __name__ == "__main__":
    main()
