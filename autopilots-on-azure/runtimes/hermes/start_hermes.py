from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import shutil
import sys
import threading
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, unquote
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from runtime_backup import BackupLoop, restore_latest
from runtime_identity import agentic_auth_enabled
from runtime_keepalive import get_keepalive
from bridge.hermes_telemetry import (
    GatewayTraceConflictError,
    gateway_trace_context,
    install_gateway_telemetry,
)
from bridge.telemetry import configure_telemetry, flush_telemetry, runtime_trace_headers
from bridge.telemetry_http import TelemetryMiddleware
from bridge.generated_files import (
    GeneratedFileError, GeneratedFileStore, MAX_EVIDENCE_BYTES, RUN_HEADER, RUN_PATTERN,
    INPUT_SCOPE_HEADER, SCOPE_HEADER, completed_run_evidence, generated_files_enabled,
)
from autopilots_identity.document_operations import (
    acknowledge_delivery as acknowledge_document_delivery,
    claim_delivery as claim_document_delivery,
    configure_background_retry,
    copy_now as copy_document_now,
    fail_delivery as fail_document_delivery,
    pending_choices as pending_document_choices,
    process_background_retry,
)
from autopilots_identity.interaction_actions import claim_interaction
from autopilots_identity.collaboration_mcp import (
    bind_pending_publish_scope,
)
from blueprint import (
    RoleReleaseInstall,
    install_or_refresh_role_release,
    role_release_settings_from_environment,
)
PLUGIN_NAME = "autopilots"
RETIRED_PLUGINS = ("keepalive", "autopilots-telemetry")
HERMES_ACCOUNT = "hermes"
UPSTREAM_STAGE2 = "/opt/hermes/docker/stage2-hook.sh"
UPSTREAM_HERMES = "/opt/hermes/bin/hermes"
S6_COMMANDS = "/command:/package/admin/s6/command"

WORKIQ_MCP_ENVIRONMENTS = {
    "workiq-mail": "WORKIQ_MAIL_MCP_URL",
    "workiq-word": "WORKIQ_WORD_MCP_URL",
    "workiq-teams": "WORKIQ_TEAMS_MCP_URL",
    "workiq-calendar": "WORKIQ_CALENDAR_MCP_URL",
    "workiq-onedrive": "WORKIQ_ONEDRIVE_MCP_URL",
    "workiq-sharepoint": "WORKIQ_SHAREPOINT_MCP_URL",
    "workiq-excel": "WORKIQ_EXCEL_MCP_URL",
    "workiq-copilot": "WORKIQ_COPILOT_MCP_URL",
}


DEFAULT_HERMES_HOME = "/data/hermes"
DEFAULT_GATEWAY_PORT = 9119
DEFAULT_AGENT_MCP_PROXY_PORT = 18081
DEFAULT_M365_COLLABORATION_MCP_PORT = 18082


def bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def hermes_home() -> Path:
    return Path(os.getenv("HERMES_HOME", DEFAULT_HERMES_HOME)).expanduser()


def runtime_backup_dir() -> Path | None:
    value = os.getenv("RUNTIME_BACKUP_DIR", "").strip()
    return Path(value) if value else None


def api_server_port() -> int:
    return int(os.getenv("API_SERVER_PORT", os.getenv("PORT", "8642")))


def gateway_port() -> int:
    return int(os.getenv("HERMES_GATEWAY_PORT", str(DEFAULT_GATEWAY_PORT)))


def write_env_file(home: Path) -> Path:
    configure_model_environment()
    agentic_auth = agentic_auth_enabled()
    env_path = home / ".env"
    values = {
        "API_SERVER_ENABLED": os.getenv("API_SERVER_ENABLED", "true"),
        "API_SERVER_HOST": "127.0.0.1",
        "API_SERVER_PORT": str(gateway_port()),
        "API_SERVER_KEY": os.getenv("API_SERVER_KEY", ""),
        "HERMES_HOME": str(home),
        "USE_AGENTIC_AUTH": "true" if agentic_auth else "false",
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", ""),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", ""),
        "AZURE_FOUNDRY_BASE_URL": os.getenv("AZURE_FOUNDRY_BASE_URL", ""),
        "AZURE_FOUNDRY_API_KEY": os.getenv("AZURE_FOUNDRY_API_KEY", ""),
        "HERMES_INFERENCE_MODEL": os.getenv("HERMES_INFERENCE_MODEL", ""),
        "HERMES_MODEL_PROVIDER": os.getenv("HERMES_MODEL_PROVIDER", ""),
        "HERMES_MODEL": os.getenv("HERMES_MODEL", ""),
        "PRIVATE_INCIDENTS_MCP_URL": os.getenv("PRIVATE_INCIDENTS_MCP_URL", ""),
        "PUBLIC_SHIPMENTS_MCP_URL": os.getenv("PUBLIC_SHIPMENTS_MCP_URL", ""),
        **{
            environment_name: os.getenv(environment_name, "") if agentic_auth else ""
            for environment_name in WORKIQ_MCP_ENVIRONMENTS.values()
        },
        "M365_COLLABORATION_MCP_URL": os.getenv(
            "M365_COLLABORATION_MCP_URL",
            "",
        ) if agentic_auth else "",
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
    runtime_config: dict[str, Any] = {
        "model": {
            "provider": os.getenv("HERMES_MODEL_PROVIDER", "azure-foundry"),
            "default": os.getenv("HERMES_MODEL", "gpt-6-1-sol"),
            "api_mode": "responses",
        },
        "memory": {"nudge_interval": int(os.getenv("HERMES_MEMORY_NUDGE_INTERVAL", "10"))},
        "skills": {
            "creation_nudge_interval": int(os.getenv("HERMES_SKILL_NUDGE_INTERVAL", "10")),
            "ledger": True,
            "write_approval": bool_env("HERMES_SKILL_WRITE_APPROVAL", False),
        },
        "auxiliary": {"background_review": {"enabled": True}},
        "curator": {"enabled": False},
        "cron": {"misfire_grace_minutes": 0},
        "plugins": {"auto_update_check_hours": 0},
        "updates": {"check": False},
        "telemetry": {"shared_metrics": {"enabled": False, "send": False}},
        "security": {"allow_lazy_installs": False},
        "gateway": {
            "platforms": {
                "api_server": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": gateway_port(),
                    "api_key": os.getenv("API_SERVER_KEY", ""),
                }
            }
        },
        "paths": {
            "home": str(home),
            "workspace": str(home / "workspace"),
        },
    }
    foundry_url = os.getenv("FOUNDRY_OPENAI_BASE_URL", "").strip()
    if foundry_url:
        runtime_config["model"].update(
            {
                "provider": "azure-foundry",
                "base_url": foundry_url.rstrip("/"),
                "auth_mode": "entra_id",
                "entra": {
                    "scope": os.getenv(
                        "FOUNDRY_TOKEN_SCOPE",
                        "https://ai.azure.com/.default",
                    )
                },
            }
        )
    mcp_servers: dict[str, Any] = {}
    private_mcp_url = os.getenv("PRIVATE_INCIDENTS_MCP_URL", "").strip()
    public_shipments_mcp_url = os.getenv("PUBLIC_SHIPMENTS_MCP_URL", "").strip()
    if private_mcp_url:
        mcp_servers["private-incidents"] = {"url": private_mcp_url}
    if public_shipments_mcp_url:
        mcp_servers["public-shipments"] = {"url": public_shipments_mcp_url}
    if agentic_auth_enabled():
        for name, environment_name in WORKIQ_MCP_ENVIRONMENTS.items():
            url = os.getenv(environment_name, "").strip()
            if url:
                mcp_servers[name] = {"url": url}
    collaboration_url = (
        os.getenv("M365_COLLABORATION_MCP_URL", "").strip()
        if agentic_auth_enabled() else ""
    )
    if collaboration_url:
        mcp_servers["m365-collaboration"] = {
            "url": collaboration_url,
            "connect_timeout": 30,
            "timeout": 900,
        }
    runtime_config["mcp_servers"] = mcp_servers
    if bool_env("USER_SCHEDULING_ENABLED", False):
        runtime_config["cron"].update({"provider": "azure", "mirror_delivery": False})
    config = _deep_merge(base or {}, runtime_config)
    if not bool_env("USER_SCHEDULING_ENABLED", False):
        for inherited in ("provider", "mirror_delivery"):
            config["cron"].pop(inherited, None)
    plugins = dict(config.get("plugins") or {})
    if PLUGIN_NAME in (plugins.get("disabled") or []):
        raise ValueError(f"Hermes {PLUGIN_NAME} plugin must not be disabled.")
    enabled_plugins = plugins.get("enabled") or []
    if not isinstance(enabled_plugins, list):
        raise ValueError("Hermes plugins.enabled must be a list.")
    plugins["enabled"] = list(dict.fromkeys([
        *(name for name in enabled_plugins if name not in RETIRED_PLUGINS), PLUGIN_NAME,
    ]))
    config["plugins"] = plugins
    config["model"].pop("name", None)
    configured_servers = config.get("mcp_servers")
    if isinstance(configured_servers, dict):
        for name in (
            "private-incidents",
            "public-shipments",
            *WORKIQ_MCP_ENVIRONMENTS,
            "m365-collaboration",
        ):
            if name not in mcp_servers:
                configured_servers.pop(name, None)
        if not configured_servers:
            config.pop("mcp_servers", None)
    return config




def install_runtime_plugins(profile_home: Path, source_root: Path = Path("/app/runtime_plugins")) -> None:
    """Replace image-owned plugins exactly, so files removed from the image disappear from the profile."""
    destination_root = profile_home / "plugins"
    destination_root.mkdir(parents=True, exist_ok=True)
    for name in RETIRED_PLUGINS:
        shutil.rmtree(destination_root / name, ignore_errors=True)
    if not source_root.is_dir():
        return
    for source in source_root.iterdir():
        if source.is_dir():
            shutil.rmtree(destination_root / source.name, ignore_errors=True)
            shutil.copytree(source, destination_root / source.name)


def hermes_account() -> tuple[int, int]:
    import pwd

    entry = pwd.getpwnam(HERMES_ACCOUNT)
    return entry.pw_uid, entry.pw_gid


def own_profile(profile_home: Path, uid: int, gid: int) -> None:
    """Give the unprivileged Hermes account the profile; a full walk only runs after a 0.19 root-owned layout."""
    if profile_home.stat().st_uid == uid:
        targets = [profile_home / name for name in ("config.yaml", ".env", "plugins", "workspace", ".telemetry")]
    else:
        targets = [profile_home]
    for target in targets:
        if not target.exists():
            continue
        for root, directories, files in os.walk(target):
            for name in [*directories, *files]:
                path = Path(root) / name
                if not path.is_symlink():
                    os.chown(path, uid, gid)
        os.chown(target, uid, gid)


def as_hermes(uid: int, gid: int) -> dict[str, Any]:
    return {"user": uid, "group": gid, "extra_groups": []}


def run_upstream_stage2(profile_home: Path) -> None:
    """Run the official image bootstrap (ownership, seed directories, bundled skills) as Hermes' non-PID-1 path does."""
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_home)
    env["PATH"] = f"{S6_COMMANDS}:{env.get('PATH', '')}"
    subprocess.run([UPSTREAM_STAGE2], env=env, check=True)


def configure_model_environment() -> None:
    foundry_url = os.getenv("FOUNDRY_OPENAI_BASE_URL", "").strip()
    if foundry_url:
        os.environ["OPENAI_BASE_URL"] = foundry_url.rstrip("/")
        os.environ["AZURE_FOUNDRY_BASE_URL"] = foundry_url.rstrip("/")
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("AZURE_FOUNDRY_API_KEY", None)
        os.environ["HERMES_MODEL_PROVIDER"] = "azure-foundry"
        model = os.getenv("HERMES_MODEL") or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or "gpt-6-1-sol"
        os.environ.setdefault("HERMES_MODEL", model)
        os.environ.setdefault("HERMES_INFERENCE_MODEL", model)


def start_agent_mcp_proxy(**account: Any) -> subprocess.Popen | None:
    if not agentic_auth_enabled():
        print("Agent 365 authentication is disabled; Agent Identity MCP adapter is disabled.", flush=True)
        return None
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
        **account,
    )


def start_m365_collaboration_mcp(**account: Any) -> subprocess.Popen | None:
    if not agentic_auth_enabled() or not os.getenv("M365_COLLABORATION_MCP_URL", "").strip():
        return None
    port = os.getenv(
        "M365_COLLABORATION_MCP_PORT",
        str(DEFAULT_M365_COLLABORATION_MCP_PORT),
    )
    print(
        "Starting Agent User collaboration MCP on "
        f"127.0.0.1:{port}",
        flush=True,
    )
    env = os.environ.copy()
    env["M365_COLLABORATION_MCP_PORT"] = port
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "autopilots_identity.collaboration_mcp",
        ],
        env=env,
        **account,
    )


def wait_for_loopback(process: subprocess.Popen, port: int, timeout: float = 30) -> None:
    """Start Hermes as soon as a local adapter accepts connections instead of after a fixed delay."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Loopback service on port {port} exited with code {process.returncode}.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Loopback service on port {port} did not start within {timeout:.0f} seconds.")


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


def start_gateway(profile_home: Path, **account: Any) -> subprocess.Popen:
    env = os.environ.copy()
    env["HERMES_HOME"] = str(profile_home)
    env["HOME"] = str(profile_home / "home")
    return subprocess.Popen(
        [UPSTREAM_HERMES, "gateway", "run", "--replace", "--accept-hooks"],
        env=env,
        cwd=str(profile_home),
        **account,
    )


def create_health_app(
    home: Path,
    profile_home: Path,
    role_release: RoleReleaseInstall | None,
    gateway: subprocess.Popen | None,
    backup: BackupLoop | None = None,
) -> FastAPI:
    app = FastAPI(title="Hermes ACA Sandbox runtime")
    app.add_middleware(TelemetryMiddleware, runtime=True)
    app.router.add_event_handler("shutdown", flush_telemetry)
    keepalive = get_keepalive()
    process_nonce = secrets.token_hex(16)
    generated_files = None
    if generated_files_enabled():
        worker_id = os.getenv("WORKER_ID", "").strip()
        generated_files = GeneratedFileStore(
            Path(tempfile.gettempdir()) / "autopilots-generated-files"
            / hashlib.sha256(worker_id.encode("utf-8")).hexdigest()[:32],
            worker_id=worker_id, workspace_owner=hermes_account(),
        )

    @app.middleware("http")
    async def keep_runtime_request_active(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and Path("/app/.application-maintenance").exists():
            return JSONResponse(
                {"error": "Runtime source maintenance; the request was not admitted."},
                status_code=503, headers={"Retry-After": "10"},
            )
        if keepalive is not None and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            with keepalive.activity("runtime-request"):
                return await call_next(request)
        return await call_next(request)

    role_release_health = None
    if role_release:
        role_release_health = {
            "roleBlueprint": role_release.manifest["roleBlueprint"],
            "roleRelease": role_release.manifest["roleRelease"],
            "commit": role_release.manifest["roleReleaseCommit"],
            "workerId": role_release.manifest["workerId"],
        }

    @app.get("/health")
    def health() -> JSONResponse:
        running = gateway is not None and gateway.poll() is None
        return JSONResponse({
            "status": "ok" if running else "gateway-unavailable",
            "release": os.getenv("APPLICATION_RELEASE", ""),
        }, status_code=200 if running else 503)

    @app.get("/health/detailed")
    def health_detailed(request: Request) -> JSONResponse:
        require_internal_key(request)
        running = gateway is not None and gateway.poll() is None
        payload = {
            "status": "ok" if running else "gateway-unavailable",
            "release": os.getenv("APPLICATION_RELEASE", ""),
            "nativeRuns": True,
            "processId": os.getpid(),
            "memoryNonce": process_nonce,
            "runtime": "hermes",
            "hermesHome": str(home),
            "profileHome": str(profile_home),
            "configExists": (profile_home / "config.yaml").exists(),
            "envExists": (profile_home / ".env").exists(),
            "roleRelease": role_release_health,
            "gatewayPort": gateway_port(),
            "apiServerPort": api_server_port(),
            "gatewayPid": gateway.pid if gateway and gateway.poll() is None else None,
            "backup": backup.status() if backup else None,
        }
        return JSONResponse(payload, status_code=200 if running else 503)

    def require_internal_key(request: Request) -> None:
        expected = os.getenv("API_SERVER_KEY", "")
        supplied = request.headers.get("x-autopilot-key", "")
        if not expected or expected == "not-configured" or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="A valid X-Autopilot-Key header is required.")

    @app.get("/internal/runtime/status")
    async def runtime_status(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        key = os.environ["API_SERVER_KEY"]
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"http://127.0.0.1:{gateway_port()}/internal/runtime/activity",
                headers={"X-Autopilot-Key": key, "Authorization": "Bearer " + key},
            )
        response.raise_for_status()
        native = response.json()
        if not isinstance(native, dict) or not isinstance(native.get("active"), int):
            raise HTTPException(status_code=502, detail="Native API did not attest its active work.")
        wrapper = keepalive.status()["active"] if keepalive is not None else 0
        return {"active": native["active"] + wrapper, "native": native, "wrapperOperations": wrapper}

    @app.post("/internal/documents/pending")
    async def document_pending(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        operation_scope = (
            payload.get("operationScope")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(operation_scope, str) or not operation_scope:
            raise HTTPException(
                status_code=400,
                detail="operationScope must be a non-empty string.",
            )
        return await pending_document_choices(operation_scope)

    @app.post("/internal/documents/bind-scope")
    async def document_bind_scope(
        request: Request,
    ) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Document operation body must be an object.",
            )
        try:
            return bind_pending_publish_scope(
                str(payload.get("operationId") or ""),
                str(payload.get("operationScope") or ""),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.post("/internal/documents/background")
    async def document_background(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Document operation body must be an object.",
            )
        try:
            return configure_background_retry(
                str(payload.get("operationId") or ""),
                str(payload.get("operationScope") or ""),
                str(payload.get("recipientIdentifier") or ""),
                payload.get("deliveryReference")
                if isinstance(
                    payload.get("deliveryReference"),
                    dict,
                )
                else {},
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.post("/internal/documents/process")
    async def document_process(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        operation_id = (
            payload.get("operationId")
            if isinstance(payload, dict)
            else None
        )
        if not isinstance(operation_id, str) or not operation_id:
            raise HTTPException(
                status_code=400,
                detail="operationId must be a non-empty string.",
            )
        return await process_background_retry(operation_id)

    @app.post("/internal/documents/copy")
    async def document_copy(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Document operation body must be an object.",
            )
        try:
            return await copy_document_now(
                str(payload.get("operationId") or ""),
                str(payload.get("operationScope") or ""),
                str(payload.get("recipientIdentifier") or ""),
                payload.get("deliveryReference")
                if isinstance(
                    payload.get("deliveryReference"),
                    dict,
                )
                else {},
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.post("/internal/documents/ack-delivery")
    async def document_ack_delivery(
        request: Request,
    ) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Document delivery body must be an object.",
            )
        try:
            return acknowledge_document_delivery(
                str(payload.get("operationId") or ""),
                str(payload.get("deliveryActivityId") or ""),
                str(payload.get("deliveryAttemptId") or ""),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.post("/internal/documents/claim-delivery")
    async def document_claim_delivery(
        request: Request,
    ) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        operation_id = (
            payload.get("operationId")
            if isinstance(payload, dict)
            else None
        )
        try:
            return claim_document_delivery(
                str(operation_id or "")
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.post("/internal/documents/fail-delivery")
    async def document_fail_delivery(
        request: Request,
    ) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail="Document delivery body must be an object.",
            )
        try:
            return fail_document_delivery(
                str(payload.get("operationId") or ""),
                str(payload.get("deliveryAttemptId") or ""),
                str(payload.get("error") or ""),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.post("/internal/interactions/claim")
    async def interaction_claim(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        try:
            return claim_interaction(
                profile_home,
                interaction_id=str(
                    payload.get("interactionId") or ""
                ),
                choice_id=str(payload.get("choiceId") or ""),
                expires_at_unix=float(
                    payload.get("expiresAtUnix") or 0
                ),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    native_trace_admissions: dict[str, Any] = {}
    native_trace_runs: dict[str, str] = {}
    native_trace_sessions: dict[str, str] = {}

    @app.get("/internal/runs/{run_id}/evidence")
    async def native_run_evidence(run_id: str, request: Request) -> dict[str, Any]:
        require_internal_key(request)
        if generated_files is None:
            raise HTTPException(status_code=503, detail="Generated chat files are disabled in this runtime.")
        if not RUN_PATTERN.fullmatch(run_id):
            raise HTTPException(status_code=400, detail="Invalid native run ID.")
        headers = {
            "Authorization": "Bearer " + os.environ["API_SERVER_KEY"],
            "X-Autopilot-Key": os.environ["API_SERVER_KEY"],
        }
        target = f"http://127.0.0.1:{gateway_port()}/v1/runs/{run_id}"
        try:
            async with asyncio.timeout(20), httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                async def read_native(url: str, content_type: str) -> bytes:
                    async with client.stream("GET", url, headers=headers) as response:
                        if response.status_code != 200:
                            raise HTTPException(
                                status_code=502,
                                detail=f"Native evidence is unavailable (HTTP {response.status_code}).",
                            )
                        if response.headers.get("content-type", "").split(";")[0] != content_type:
                            raise GeneratedFileError("Native evidence has an unexpected content type.")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            data.extend(chunk)
                            if len(data) > MAX_EVIDENCE_BYTES:
                                raise GeneratedFileError("Native evidence exceeds the 1 MiB limit.")
                        return bytes(data)

                status = json.loads(await read_native(target, "application/json"))
                if not isinstance(status, dict) or status.get("status") != "completed":
                    raise HTTPException(status_code=409, detail="Native evidence requires a completed run.")
                events = await read_native(target + "/events", "text/event-stream")
                skill_loads = json.loads(await read_native(
                    f"http://127.0.0.1:{gateway_port()}/internal/skills/evidence/{run_id}",
                    "application/json",
                ))
                return completed_run_evidence(run_id, status, events, skill_loads)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise HTTPException(status_code=504, detail="Native evidence timed out.") from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="Native evidence is unreachable.") from exc
        except (GeneratedFileError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, GeneratedFileError) else "Native evidence is not valid JSON."
            raise HTTPException(status_code=502, detail=detail) from exc

    @app.get("/internal/generated-files/{turn_id}/{file_id}")
    async def download_generated_file(turn_id: str, file_id: str, request: Request) -> Response:
        require_internal_key(request)
        if generated_files is None:
            raise HTTPException(status_code=503, detail="Generated chat files are disabled in this runtime.")
        try:
            entry, data = generated_files.download(
                turn_id, file_id, scope=request.headers.get(SCOPE_HEADER, ""),
                run_id=request.headers.get(RUN_HEADER, ""),
            )
        except GeneratedFileError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return Response(
            content=data, media_type=entry["contentType"],
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(entry['name'], safe='')}"},
        )

    @app.put("/internal/turn-inputs/{admission_id}/{index}")
    async def stage_original_input(admission_id: str, index: int, request: Request) -> JSONResponse:
        require_internal_key(request)
        if generated_files is None:
            raise HTTPException(status_code=503, detail="Original chat inputs require the turn-file protocol.")
        try:
            entry = await generated_files.stage_input(
                admission_id, session_id=request.headers.get("X-Hermes-Session-Id", ""),
                scope=request.headers.get(SCOPE_HEADER, ""),
                input_scope=request.headers.get(INPUT_SCOPE_HEADER, ""), index=index,
                name=unquote(request.headers.get("X-Autopilot-Input-Name", "")),
                content_type=request.headers.get("X-Autopilot-Input-Type", ""),
                size=int(request.headers.get("X-Autopilot-Input-Size", "0")),
                sha256=request.headers.get("X-Autopilot-Input-SHA256", ""), chunks=request.stream(),
            )
        except (GeneratedFileError, ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail="Original input staging refused: " + str(exc)) from exc
        return JSONResponse(entry, status_code=201)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        target = f"http://127.0.0.1:{gateway_port()}/{path}"
        native_turn = request.method == "POST" and path == "v1/runs"
        native_status = re.fullmatch(r"v1/runs/([A-Za-z0-9_-]{1,160})", path)
        timeout = httpx.Timeout(
            float(os.getenv("HERMES_BRIDGE_TIMEOUT_SECONDS", "600")),
            connect=10,
        )
        body = await request.body()
        admission = ""
        new_admission = False
        file_turn = None
        try:
            if native_turn:
                expected = os.getenv("API_SERVER_KEY", "")
                supplied = request.headers.get("authorization", "")
                if not expected or not secrets.compare_digest(supplied, "Bearer " + expected):
                    raise HTTPException(status_code=401, detail="A valid native Bearer credential is required.")
                try:
                    payload = json.loads(body)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Run admission body must be JSON.") from exc
                session_id = payload.get("session_id") if isinstance(payload, dict) else None
                if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
                    raise HTTPException(status_code=400, detail="An explicit native session_id is required.")
                admission = request.headers.get("idempotency-key", "")
                if not admission or len(admission) > 256:
                    raise HTTPException(status_code=400, detail="An Idempotency-Key is required.")
                if admission in native_trace_sessions and native_trace_sessions[admission] != session_id:
                    raise HTTPException(status_code=409, detail="The admission belongs to another native session.")
                if admission not in native_trace_admissions:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        canonical = await client.get(
                            f"http://127.0.0.1:{gateway_port()}/api/sessions/{quote(session_id, safe='')}/messages",
                            params={"limit": 0}, headers={"Authorization": supplied},
                        )
                    canonical.raise_for_status()
                    resolved = canonical.json().get("session_id")
                    if not isinstance(resolved, str) or not resolved or len(resolved) > 256:
                        raise HTTPException(status_code=502, detail="Native API did not resolve the session identity.")
                    context = gateway_trace_context(
                        resolved, profile_home=profile_home, admission_id=admission,
                        ttl_seconds=3600,
                    )
                    context.__enter__()
                    native_trace_admissions[admission] = context
                    native_trace_sessions[admission] = session_id
                    new_admission = True
                if generated_files is not None and request.headers.get(SCOPE_HEADER):
                    file_turn = generated_files.admit(
                        admission, session_id=native_trace_sessions[admission],
                        scope=request.headers[SCOPE_HEADER],
                    )
                    if "inputTurnId" in payload or "inputFileCount" in payload:
                        file_turn = generated_files.require_inputs(
                            admission, turn_id=payload.pop("inputTurnId", ""),
                            count=payload.pop("inputFileCount", None),
                            session_id=native_trace_sessions[admission], scope=request.headers[SCOPE_HEADER],
                            input_scope=request.headers.get(INPUT_SCOPE_HEADER, ""),
                        )
                    elif file_turn.get("inputs"):
                        raise GeneratedFileError("Staged originals require explicit input identity and count.")
                    payload["instructions"] = (
                        str(payload.get("instructions") or "") + generated_files.instructions(file_turn["turnId"])
                    )
                    body = json.dumps(payload).encode("utf-8")
                elif "inputTurnId" in payload or "inputFileCount" in payload:
                    raise GeneratedFileError("Original inputs require the authenticated turn-file scope.")
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    request.method,
                    target,
                    content=body,
                    headers={
                        **{
                            key: value
                            for key, value in request.headers.items()
                            if key.lower() not in {
                                "host",
                                "traceparent",
                                "tracestate",
                                "baggage",
                                "x-autopilot-otel-session",
                                "x-autopilot-otel-operation",
                                "content-length",
                            }
                        },
                        **runtime_trace_headers(),
                    },
                )
                if native_turn and response.status_code >= 500:
                    response.raise_for_status()
        except GatewayTraceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except GeneratedFileError as exc:
            if new_admission:
                context = native_trace_admissions.pop(admission, None)
                native_trace_sessions.pop(admission, None)
                if context is not None:
                    context.__exit__(None, None, None)
            raise HTTPException(status_code=422, detail=f"Generated-file handoff refused: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            response = exc.response
        except httpx.ConnectError as exc:
            raise HTTPException(status_code=503, detail="Hermes native API is not accepting requests yet.") from exc
        if response.headers.get("content-type", "").startswith("application/json"):
            result = response.json()
            if native_turn and response.status_code == 202:
                native_trace_runs[str(result["run_id"])] = admission
                if file_turn is not None:
                    try:
                        generated_files.bind_run(file_turn["turnId"], str(result["run_id"]))
                    except GeneratedFileError as exc:
                        raise HTTPException(status_code=409, detail=str(exc)) from exc
            elif native_turn and new_admission and 400 <= response.status_code < 500:
                context = native_trace_admissions.pop(admission, None)
                native_trace_sessions.pop(admission, None)
                if context is not None:
                    context.__exit__(None, None, None)
            if request.method == "GET" and native_status and result.get("status") in {
                "completed", "failed", "cancelled", "interrupted",
            }:
                identity = native_trace_runs.pop(native_status.group(1), "")
                context = native_trace_admissions.pop(identity, None)
                native_trace_sessions.pop(identity, None)
                if context is not None:
                    context.__exit__(None, None, None)
                if generated_files is not None and result.get("status") == "completed":
                    try:
                        sealed = generated_files.seal(
                            native_status.group(1), str(result.get("output") or ""),
                            scope=request.headers.get(SCOPE_HEADER, ""),
                        )
                    except GeneratedFileError as exc:
                        raise HTTPException(status_code=422, detail=f"Generated-file handoff refused: {exc}") from exc
                    if sealed is not None:
                        result["output"] = sealed["output"]
                        result["generatedAttachments"] = {"protocolVersion": 1, "files": sealed["files"]}
            return JSONResponse(content=result, status_code=response.status_code)
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return app


def main() -> None:
    configure_telemetry("hermes-runtime")
    home = hermes_home()
    configure_model_environment()
    uid, gid = hermes_account()
    backup_dir = runtime_backup_dir()
    if backup_dir:
        restored = restore_latest(home, backup_dir, worker_id=os.getenv("WORKER_ID", ""), **as_hermes(uid, gid))
        if restored:
            print(f"Hermes Worker restored from {restored.name}", flush=True)
    else:
        print("RUNTIME_BACKUP_DIR is not set; Worker backups are disabled.", flush=True)
    home.mkdir(parents=True, exist_ok=True)
    role_release = None
    profile_home = home
    role_release_settings = role_release_settings_from_environment()
    if role_release_settings:
        role_release = install_or_refresh_role_release(home, role_release_settings)
        profile_home = role_release.profile_home
        action = "refreshed" if role_release.changed else "reused"
        print(
            f"Hermes Role Release {action}: "
            f"{role_release_settings.role_blueprint}@{role_release_settings.role_release}",
            flush=True,
        )
    profile_home.mkdir(parents=True, exist_ok=True)
    (profile_home / "home").mkdir(exist_ok=True)
    (profile_home / "workspace").mkdir(exist_ok=True)
    install_runtime_plugins(
        profile_home,
        source_root=Path(os.getenv("AUTOPILOTS_APPLICATION_ROOT", "/app")) / "runtime_plugins",
    )
    install_gateway_telemetry(profile_home)
    os.environ["HERMES_HOME"] = str(profile_home)
    env_path = write_env_file(profile_home)
    config_path = write_config(profile_home)
    own_profile(profile_home, uid, gid)
    print(f"Hermes profile home: {profile_home}; env {env_path}; config {config_path}", flush=True)

    account = as_hermes(uid, gid)
    mcp_proxy = None
    collaboration_mcp = None
    gateway = None
    try:
        mcp_proxy = start_agent_mcp_proxy(**account)
        collaboration_mcp = start_m365_collaboration_mcp(**account)
        run_upstream_stage2(profile_home)
        if mcp_proxy:
            wait_for_loopback(mcp_proxy, int(os.getenv("AGENT_MCP_PROXY_PORT", str(DEFAULT_AGENT_MCP_PROXY_PORT))))
        if collaboration_mcp:
            wait_for_loopback(
                collaboration_mcp,
                int(os.getenv("M365_COLLABORATION_MCP_PORT", str(DEFAULT_M365_COLLABORATION_MCP_PORT))),
            )
        gateway = start_gateway(profile_home, **account)
        print(f"Started Hermes gateway pid={gateway.pid}", flush=True)
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
        os.environ["HOME"] = str(profile_home / "home")
        backup_loop = BackupLoop(profile_home, backup_dir).start() if backup_dir else None
        app = create_health_app(home, profile_home, role_release, gateway, backup_loop)
        uvicorn.run(app, host="0.0.0.0", port=api_server_port())
    finally:
        for process in (gateway, collaboration_mcp, mcp_proxy):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
