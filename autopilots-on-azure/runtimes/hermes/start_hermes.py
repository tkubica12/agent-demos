from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import shutil
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from bridge.hermes_telemetry import (
    GatewayTraceConflictError,
    PLUGIN_NAME as TELEMETRY_PLUGIN_NAME,
    gateway_trace_context,
    install_gateway_telemetry,
)
from bridge.telemetry import configure_telemetry, flush_telemetry, runtime_trace_headers
from bridge.telemetry_http import TelemetryMiddleware
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
from collective_learning import (
    CollectiveLearningError,
    approved_learning_packet,
    attest_learning_packet,
    attest_refresh_rejection,
    pending_learning_packet,
    pending_refresh_rejection,
    prepare_learning_packet,
    prepare_refresh_rejection,
    worker_refresh_readiness,
)
from learning import (
    assert_legacy_state_migrated,
    abort_learning_turn,
    begin_learning_turn,
    build_learning_status,
    ensure_learning_state,
    initialize_governed_state,
    reconcile_learning_turn,
    validate_skill_namespaces,
)
from cron_runtime import (
    acknowledge_cron_delivery,
    bind_cron_delivery,
    bind_cron_local,
    checkpoint_system_schedule,
    claim_system_schedule,
    complete_system_schedule,
    cron_diagnostics,
    cron_delivery_receipt_status,
    fire_cron_job,
    get_delivery_reference,
    get_system_schedule_checkpoint,
    ensure_system_dream_schedule,
    enqueue_system_dream_now,
    list_cron_jobs,
    reconcile_cron_provider,
    upsert_delivery_reference,
)

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
        "WORKIQ_WORD_MCP_URL": os.getenv("WORKIQ_WORD_MCP_URL", ""),
        **{
            environment_name: os.getenv(environment_name, "")
            for environment_name in WORKIQ_MCP_ENVIRONMENTS.values()
        },
        "M365_COLLABORATION_MCP_URL": os.getenv(
            "M365_COLLABORATION_MCP_URL",
            "",
        ),
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
            "default": os.getenv("HERMES_MODEL", os.getenv("OPENCLAW_MODEL_ID", "gpt-5-6-terra")),
        },
        "memory": {"nudge_interval": 0},
        "skills": {"creation_nudge_interval": 0},
        "curator": {"enabled": False},
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
                        "https://cognitiveservices.azure.com/.default",
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
    for name, environment_name in WORKIQ_MCP_ENVIRONMENTS.items():
        url = os.getenv(environment_name, "").strip()
        if url:
            mcp_servers[name] = {"url": url}
    collaboration_url = os.getenv(
        "M365_COLLABORATION_MCP_URL",
        "",
    ).strip()
    if collaboration_url:
        mcp_servers["m365-collaboration"] = {
            "url": collaboration_url,
            "connect_timeout": 30,
            "timeout": 900,
        }
    if mcp_servers:
        runtime_config["mcp_servers"] = mcp_servers
    if bool_env("USER_SCHEDULING_ENABLED", False):
        runtime_config["cron"] = {
            "provider": "azure",
            "mirror_delivery": False,
        }
    config = _deep_merge(base or {}, runtime_config)
    plugins = dict(config.get("plugins") or {})
    if TELEMETRY_PLUGIN_NAME in (plugins.get("disabled") or []):
        raise ValueError("Hermes native telemetry plugin must not be disabled.")
    enabled_plugins = plugins.get("enabled") or []
    if not isinstance(enabled_plugins, list):
        raise ValueError("Hermes plugins.enabled must be a list.")
    plugins["enabled"] = list(dict.fromkeys([*enabled_plugins, TELEMETRY_PLUGIN_NAME]))
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


def install_runtime_plugins(profile_home: Path) -> None:
    source_root = Path("/app/runtime_plugins")
    if not source_root.is_dir():
        return
    destination_root = profile_home / "plugins"
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in source_root.iterdir():
        if source.is_dir():
            shutil.copytree(
                source,
                destination_root / source.name,
                dirs_exist_ok=True,
            )


def configure_model_environment() -> None:
    foundry_url = os.getenv("FOUNDRY_OPENAI_BASE_URL", "").strip()
    if foundry_url:
        os.environ["OPENAI_BASE_URL"] = foundry_url.rstrip("/")
        os.environ["AZURE_FOUNDRY_BASE_URL"] = foundry_url.rstrip("/")
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("AZURE_FOUNDRY_API_KEY", None)
        os.environ["HERMES_MODEL_PROVIDER"] = "azure-foundry"
        model = os.getenv("HERMES_MODEL") or os.getenv("OPENCLAW_MODEL_ID") or os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or "gpt-5-6-terra"
        os.environ.setdefault("HERMES_MODEL", model)
        os.environ.setdefault("HERMES_INFERENCE_MODEL", model)


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


def start_m365_collaboration_mcp() -> subprocess.Popen | None:
    if not os.getenv("M365_COLLABORATION_MCP_URL", "").strip():
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
        "--replace",
        "--accept-hooks",
    ]
    return subprocess.Popen(command, env=env)


def create_health_app(
    home: Path,
    profile_home: Path,
    role_release: RoleReleaseInstall | None,
    gateway: subprocess.Popen | None,
) -> FastAPI:
    app = FastAPI(title="Hermes ACA Sandbox runtime")
    app.add_middleware(TelemetryMiddleware, runtime=True)
    app.router.add_event_handler("shutdown", flush_telemetry)
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
        payload = {
            "status": "ok" if running else "gateway-unavailable",
            "runtime": "hermes",
            "hermesHome": str(home),
            "profileHome": str(profile_home),
            "profileName": os.getenv("HERMES_PROFILE", "default"),
            "configExists": (profile_home / "config.yaml").exists(),
            "envExists": (profile_home / ".env").exists(),
            "roleRelease": role_release_health,
            "gatewayPort": gateway_port(),
            "apiServerPort": api_server_port(),
            "gatewayPid": gateway.pid if gateway and gateway.poll() is None else None,
        }
        return JSONResponse(payload, status_code=200 if running else 503)

    @app.get("/health/detailed")
    def health_detailed() -> JSONResponse:
        return health()

    def require_internal_key(request: Request) -> None:
        expected = os.getenv("API_SERVER_KEY", "")
        supplied = request.headers.get("x-autopilot-key", "")
        if not expected or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="A valid X-Autopilot-Key header is required.")

    @app.get("/internal/learning/status")
    def learning_status(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        return build_learning_status(profile_home)

    @app.post("/internal/learning/turns")
    async def begin_turn(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        return begin_learning_turn(
            profile_home,
            attachment_private=bool(
                payload.get("attachmentPrivate")
                if isinstance(payload, dict)
                else False
            ),
        )

    @app.post("/internal/learning/reconcile")
    async def reconcile_turn(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        token = payload.get("token") if isinstance(payload, dict) else None
        provenance = payload.get("provenance") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise HTTPException(status_code=400, detail="token must be a non-empty string.")
        if not isinstance(provenance, list) or len(provenance) > 10:
            raise HTTPException(status_code=400, detail="provenance must be an array with at most 10 items.")
        try:
            return reconcile_learning_turn(
                profile_home,
                token=token,
                provenance=provenance,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/learning/abort")
    async def abort_turn(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        token = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise HTTPException(status_code=400, detail="token must be a non-empty string.")
        try:
            return abort_learning_turn(profile_home, token=token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.post("/internal/collective-learning/prepare")
    def prepare_collective_learning(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return prepare_learning_packet(profile_home)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/collective-learning/pending")
    def pending_collective_learning(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return pending_learning_packet(profile_home)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/collective-learning/attest")
    async def attest_collective_learning(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        receipt = payload.get("receipt") if isinstance(payload, dict) else None
        if not isinstance(receipt, dict):
            raise HTTPException(status_code=400, detail="receipt must be one JSON object.")
        try:
            return attest_learning_packet(profile_home, receipt=receipt)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/collective-learning/export")
    def export_collective_learning(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return approved_learning_packet(profile_home)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/collective-learning/prepare-rejection")
    def prepare_rejection(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return prepare_refresh_rejection(profile_home)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/collective-learning/pending-rejection")
    def pending_rejection(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return pending_refresh_rejection(profile_home)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/collective-learning/attest-rejection")
    async def attest_rejection(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        receipt = payload.get("receipt") if isinstance(payload, dict) else None
        if not isinstance(receipt, dict):
            raise HTTPException(status_code=400, detail="receipt must be one JSON object.")
        try:
            return attest_refresh_rejection(profile_home, receipt=receipt)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/collective-learning/refresh-ready")
    def refresh_ready(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return worker_refresh_readiness(profile_home)
        except CollectiveLearningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/internal/cron/jobs")
    def cron_jobs(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        return {"jobs": list_cron_jobs(profile_home)}

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

    @app.post("/internal/cron/reconcile")
    async def cron_reconcile(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        return await asyncio.to_thread(reconcile_cron_provider, profile_home)

    @app.get("/internal/cron/diagnostics")
    async def cron_diagnostic_status(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        return await asyncio.to_thread(cron_diagnostics, profile_home)

    @app.post("/internal/cron/delivery-reference")
    async def cron_delivery_reference(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        try:
            return upsert_delivery_reference(
                profile_home,
                reference_key=str(payload.get("referenceKey") or ""),
                conversation=payload.get("conversation") or {},
                boundary=str(payload.get("boundary") or ""),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            ) from exc

    @app.get("/internal/cron/delivery-reference")
    async def cron_get_delivery_reference(
        request: Request,
        referenceKey: str,
    ) -> dict[str, Any]:
        require_internal_key(request)
        reference = get_delivery_reference(
            profile_home,
            referenceKey,
        )
        if not isinstance(reference, dict):
            raise HTTPException(
                status_code=404,
                detail="Delivery reference was not found.",
            )
        return {
            "referenceKey": referenceKey,
            "deliveryReference": reference,
        }

    @app.post("/internal/cron/bind-delivery")
    async def cron_bind_delivery(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        job_ids = payload.get("jobIds")
        if not isinstance(job_ids, list) or not all(isinstance(value, str) for value in job_ids):
            raise HTTPException(status_code=400, detail="jobIds must be an array of strings.")
        try:
            return bind_cron_delivery(
                profile_home,
                job_ids=job_ids,
                reference_key=str(payload.get("referenceKey") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/cron/bind-local")
    async def cron_bind_local(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        job_ids = payload.get("jobIds")
        if not isinstance(job_ids, list) or not all(isinstance(value, str) for value in job_ids):
            raise HTTPException(status_code=400, detail="jobIds must be an array of strings.")
        try:
            return bind_cron_local(profile_home, job_ids=job_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/cron/fire")
    async def cron_fire(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        try:
            return await asyncio.to_thread(
                fire_cron_job,
                profile_home,
                job_id=str(payload.get("jobId") or ""),
                revision=str(payload.get("revision") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "type": exc.__class__.__name__,
                    "message": str(exc)[:2000],
                },
            ) from exc

    @app.post("/internal/cron/system/claim")
    async def cron_system_claim(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        try:
            return await asyncio.to_thread(
                claim_system_schedule,
                profile_home,
                job_id=str(payload.get("jobId") or ""),
                revision=str(payload.get("revision") or ""),
                occurrence_id=str(
                    payload.get("occurrenceId")
                    or payload.get("revision")
                    or ""
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/cron/system/run-now")
    async def cron_system_run_now(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        try:
            return await asyncio.to_thread(
                enqueue_system_dream_now,
                profile_home,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/cron/system/checkpoint/read")
    async def cron_system_checkpoint_read(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Checkpoint request must be an object.")
        try:
            return await asyncio.to_thread(
                get_system_schedule_checkpoint,
                profile_home,
                job_id=str(payload.get("jobId") or ""),
                revision=str(payload.get("revision") or ""),
                occurrence_id=str(payload.get("occurrenceId") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/internal/cron/system/checkpoint")
    async def cron_system_checkpoint(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("payload"), dict):
            raise HTTPException(status_code=400, detail="Checkpoint payload must be an object.")
        try:
            return await asyncio.to_thread(
                checkpoint_system_schedule,
                profile_home,
                job_id=str(payload.get("jobId") or ""),
                revision=str(payload.get("revision") or ""),
                occurrence_id=str(payload.get("occurrenceId") or ""),
                owner_token=str(payload.get("ownerToken") or ""),
                expected_phase=str(payload.get("expectedPhase") or ""),
                phase=str(payload.get("phase") or ""),
                payload=payload["payload"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/internal/cron/system/complete")
    async def cron_system_complete(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        try:
            return await asyncio.to_thread(
                complete_system_schedule,
                profile_home,
                job_id=str(payload.get("jobId") or ""),
                revision=str(payload.get("revision") or ""),
                occurrence_id=str(
                    payload.get("occurrenceId")
                    or payload.get("revision")
                    or ""
                ),
                success=bool(payload.get("success")),
                error=str(payload.get("error") or ""),
                summary=payload.get("summary")
                if isinstance(payload.get("summary"), dict)
                else {},
                owner_token=str(payload.get("ownerToken") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/internal/cron/ack-delivery")
    async def cron_ack_delivery(request: Request) -> dict[str, Any]:
        require_internal_key(request)
        payload = await request.json()
        try:
            return await asyncio.to_thread(
                acknowledge_cron_delivery,
                profile_home,
                job_id=str(payload.get("jobId") or ""),
                revision=str(payload.get("revision") or ""),
                delivery_activity_id=str(
                    payload.get("deliveryActivityId") or ""
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/internal/cron/delivery-receipt/{job_id}/{revision}")
    async def cron_delivery_receipt(
        job_id: str,
        revision: str,
        request: Request,
    ) -> dict[str, Any]:
        require_internal_key(request)
        return await asyncio.to_thread(
            cron_delivery_receipt_status,
            profile_home,
            job_id=job_id,
            revision=revision,
        )

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        target = f"http://127.0.0.1:{gateway_port()}/{path}"
        native_session = re.fullmatch(r"api/sessions/([^/]+)/chat", path)
        native_turn = request.method == "POST" and native_session is not None
        trace_context = (
            gateway_trace_context(native_session.group(1), profile_home=profile_home)
            if native_turn
            else nullcontext()
        )
        timeout = httpx.Timeout(
            float(os.getenv("HERMES_BRIDGE_TIMEOUT_SECONDS", "600")),
            connect=10,
        )
        body = await request.body()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                with trace_context:
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
                                }
                            },
                            **runtime_trace_headers(),
                        },
                    )
                    if native_turn and response.status_code >= 500:
                        response.raise_for_status()
        except GatewayTraceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            response = exc.response
        if response.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse(content=response.json(), status_code=response.status_code)
        raise HTTPException(status_code=response.status_code, detail=response.text)

    return app


def main() -> None:
    configure_telemetry("hermes-runtime")
    home = hermes_home()
    configure_model_environment()
    home.mkdir(parents=True, exist_ok=True)
    role_release = None
    profile_home = home
    role_release_settings = role_release_settings_from_environment()
    if role_release_settings:
        role_release = install_or_refresh_role_release(home, role_release_settings)
        profile_home = role_release.profile_home
        activate_profile(home, role_release_settings.role_blueprint)
        action = "refreshed" if role_release.changed else "reused"
        print(
            f"Hermes Role Release {action}: "
            f"{role_release_settings.role_blueprint}@{role_release_settings.role_release}",
            flush=True,
        )
    profile_home.mkdir(parents=True, exist_ok=True)
    install_runtime_plugins(profile_home)
    install_gateway_telemetry(profile_home)
    os.environ["HERMES_HOME"] = str(profile_home)
    (profile_home / "workspace").mkdir(parents=True, exist_ok=True)
    if role_release:
        assert_legacy_state_migrated(profile_home)
        ensure_learning_state(profile_home)
        initialize_governed_state(profile_home)
        validate_skill_namespaces(profile_home)
    env_path = write_env_file(profile_home)
    config_path = write_config(profile_home)
    system_dream_schedule = ensure_system_dream_schedule(
        profile_home,
        enabled=bool_env("SERVICEBUS_DREAM_ENABLED", False),
        schedule=os.getenv(
            "SERVICEBUS_DREAM_CRON_EXPRESSION",
            "0 2 * * *",
        ),
    )
    print(f"Hermes home: {home}", flush=True)
    print(f"Hermes profile home: {profile_home}", flush=True)
    print(f"Hermes env: {env_path}", flush=True)
    print(f"Hermes config: {config_path}", flush=True)
    print(f"System Dreaming schedule: {system_dream_schedule}", flush=True)

    mcp_proxy = None
    collaboration_mcp = None
    gateway = None
    try:
        mcp_proxy = start_agent_mcp_proxy()
        collaboration_mcp = start_m365_collaboration_mcp()
        if mcp_proxy or collaboration_mcp:
            time.sleep(2)
        if bool_env("HERMES_START_GATEWAY", True):
            gateway = start_gateway(profile_home)
            print(f"Started Hermes gateway pid={gateway.pid}", flush=True)
            time.sleep(3)
            if gateway.poll() is not None:
                raise RuntimeError(f"Hermes gateway exited during startup with code {gateway.returncode}.")

        if bool_env("HERMES_HEALTH_WRAPPER", False) or gateway is None:
            app = create_health_app(home, profile_home, role_release, gateway)
            uvicorn.run(app, host=os.getenv("API_SERVER_HOST", "0.0.0.0"), port=api_server_port())
            return

        raise SystemExit(gateway.wait())
    finally:
        for process in (
            gateway,
            collaboration_mcp,
            mcp_proxy,
        ):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
