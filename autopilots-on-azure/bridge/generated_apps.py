from __future__ import annotations

import base64
import hashlib
import os
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from typing import Any

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.containerapps.sandbox._models import (
    AutoDeletePolicy,
    AutoSuspendPolicy,
    EgressHostRule,
    EgressPolicy,
    LifecyclePolicy,
)
from azure.identity import DefaultAzureCredential


APP_ID_PATTERN = re.compile(r"^[a-f0-9]{24}$")
SAFE_PATH_PATTERN = re.compile(
    r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]{1,200}$"
)
SAFE_EMAIL_PATTERN = re.compile(
    r"^[^@\s]{1,64}@[^@\s]{1,190}$"
)
MAX_FILES = 80
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_ACTIVE_APPS = 5
MIN_TTL_SECONDS = 15 * 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_TTL_SECONDS = 24 * 60 * 60
AUTO_SUSPEND_SECONDS = 5 * 60
CARD_RETENTION_SECONDS = {
    60 * 60,
    6 * 60 * 60,
    24 * 60 * 60,
    72 * 60 * 60,
}
SUPPORTED_RUNTIMES = {"python", "node"}
RUNTIME_DISKS = {
    "python": "python-3.12",
    "node": "node-22",
}


@dataclass(frozen=True)
class GeneratedAppsSettings:
    subscription_id: str
    resource_group: str
    sandbox_group: str
    region: str
    worker_id: str

    @classmethod
    def from_environment(cls) -> "GeneratedAppsSettings":
        values = {
            "subscription_id": os.getenv(
                "AZURE_SUBSCRIPTION_ID", ""
            ).strip(),
            "resource_group": os.getenv(
                "AZURE_RESOURCE_GROUP", ""
            ).strip(),
            "sandbox_group": os.getenv(
                "GENERATED_APPS_SANDBOX_GROUP", ""
            ).strip(),
            "region": os.getenv(
                "GENERATED_APPS_REGION", ""
            ).strip(),
            "worker_id": os.getenv(
                "WORKER_ID",
                os.getenv("AUTOPILOT_NAME", "worker"),
            ).strip(),
        }
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                "Generated app settings are missing: "
                + ", ".join(missing)
            )
        return cls(**values)


def _validate_command(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array.")
    if len(value) > 20:
        raise ValueError(f"{field} exceeds 20 arguments.")
    command = []
    for item in value:
        argument = str(item)
        if not argument or len(argument) > 300:
            raise ValueError(f"{field} contains an invalid argument.")
        if "\x00" in argument or "\n" in argument or "\r" in argument:
            raise ValueError(f"{field} contains control characters.")
        command.append(argument)
    return command


def validate_generated_app_payload(
    payload: object,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Generated app payload must be an object.")
    runtime = str(payload.get("runtime") or "").strip().lower()
    if runtime not in SUPPORTED_RUNTIMES:
        raise ValueError("runtime must be python or node.")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 60:
        raise ValueError("name is required and must be at most 60 characters.")
    files = payload.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ValueError(f"files must contain 1 to {MAX_FILES} entries.")
    normalized_files = []
    total = 0
    for file in files:
        if not isinstance(file, dict):
            raise ValueError("Each file must be an object.")
        path = str(file.get("path") or "")
        if not SAFE_PATH_PATTERN.fullmatch(path):
            raise ValueError(f"Unsafe generated app path: {path!r}.")
        try:
            content = base64.b64decode(
                str(file.get("contentBase64") or ""),
                validate=True,
            )
        except ValueError as exc:
            raise ValueError(f"Invalid base64 content for {path}.") from exc
        total += len(content)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(
                f"Generated app exceeds {MAX_TOTAL_BYTES} bytes."
            )
        normalized_files.append({"path": path, "content": content})
    participants = payload.get("participantEmails")
    if (
        not isinstance(participants, list)
        or not participants
        or len(participants) > 20
    ):
        raise ValueError(
            "participantEmails must contain 1 to 20 addresses."
        )
    participant_emails = []
    for value in participants:
        email = str(value).strip().lower()
        if not SAFE_EMAIL_PATTERN.fullmatch(email):
            raise ValueError("participantEmails contains an invalid address.")
        if email not in participant_emails:
            participant_emails.append(email)
    requesting_user_email = str(
        payload.get("requestingUserEmail") or ""
    ).strip().lower()
    if not SAFE_EMAIL_PATTERN.fullmatch(requesting_user_email):
        raise ValueError("requestingUserEmail is required.")
    if requesting_user_email not in participant_emails:
        raise ValueError(
            "The requesting user must be a generated app participant."
        )
    requesting_user_id = str(
        payload.get("requestingUserId") or ""
    ).strip()
    if (
        not requesting_user_id
        or len(requesting_user_id) > 256
        or any(
            character in requesting_user_id
            for character in ("\x00", "\r", "\n")
        )
    ):
        raise ValueError("requestingUserId is required.")
    port = int(payload.get("port") or 0)
    if port < 1024 or port > 65535:
        raise ValueError("port must be between 1024 and 65535.")
    ttl_seconds = int(
        payload.get("ttlSeconds") or DEFAULT_TTL_SECONDS
    )
    if ttl_seconds < MIN_TTL_SECONDS or ttl_seconds > MAX_TTL_SECONDS:
        raise ValueError(
            f"ttlSeconds must be between {MIN_TTL_SECONDS} "
            f"and {MAX_TTL_SECONDS}."
        )
    egress_hosts = payload.get("egressHosts") or []
    if not isinstance(egress_hosts, list) or len(egress_hosts) > 20:
        raise ValueError("egressHosts must contain at most 20 hosts.")
    normalized_hosts = []
    for host in egress_hosts:
        value = str(host).strip().lower()
        if (
            not value
            or len(value) > 253
            or not re.fullmatch(r"(?:\*\.)?[a-z0-9.-]+", value)
        ):
            raise ValueError("egressHosts contains an invalid host.")
        normalized_hosts.append(value)
    app_id = str(payload.get("appId") or "").strip().lower()
    if app_id and not APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError("appId is invalid.")
    return {
        "appId": app_id or uuid.uuid4().hex[:24],
        "name": name,
        "runtime": runtime,
        "files": normalized_files,
        "participantEmails": participant_emails,
        "requestingUserEmail": requesting_user_email,
        "requestingUserId": requesting_user_id,
        "port": port,
        "installCommand": (
            _validate_command(payload["installCommand"], "installCommand")
            if payload.get("installCommand")
            else []
        ),
        "testCommand": (
            _validate_command(payload["testCommand"], "testCommand")
            if payload.get("testCommand")
            else []
        ),
        "startCommand": _validate_command(
            payload.get("startCommand"),
            "startCommand",
        ),
        "ttlSeconds": ttl_seconds,
        "egressHosts": normalized_hosts,
    }


class GeneratedAppsManager:
    def __init__(
        self,
        settings: GeneratedAppsSettings,
        *,
        credential_factory=DefaultAzureCredential,
        client_factory=SandboxGroupClient,
    ) -> None:
        self.settings = settings
        self._credential_factory = credential_factory
        self._client_factory = client_factory

    def _client(self) -> SandboxGroupClient:
        return self._client_factory(
            endpoint_for_region(self.settings.region),
            self._credential_factory(),
            subscription_id=self.settings.subscription_id,
            resource_group=self.settings.resource_group,
            sandbox_group=self.settings.sandbox_group,
        )

    def _worker_apps(self, client: SandboxGroupClient) -> list[dict]:
        apps = []
        for item in client.list_sandboxes(
            labels={"generatedAppWorker": self.settings.worker_id}
        ):
            labels = getattr(item, "labels", None) or {}
            ports = []
            for port in getattr(item, "ports", None) or []:
                ports.append(
                    {
                        "port": getattr(port, "port", None),
                        "url": getattr(port, "url", None),
                    }
                )
            lifecycle = getattr(item, "lifecycle", None)
            auto_suspend = (
                getattr(lifecycle, "auto_suspend", None)
                if lifecycle is not None
                else None
            )
            auto_delete = (
                getattr(lifecycle, "auto_delete", None)
                if lifecycle is not None
                else None
            )
            apps.append(
                {
                    "id": getattr(item, "id", ""),
                    "state": getattr(item, "state", ""),
                    "labels": labels,
                    "ports": ports,
                    "autoSuspendSeconds": int(
                        getattr(auto_suspend, "interval", 0) or 0
                    ),
                    "retentionSeconds": int(
                        getattr(
                            auto_delete,
                            "delete_interval_seconds",
                            0,
                        )
                        or 0
                    ),
                }
            )
        return apps

    @staticmethod
    def _owner_hash(user_id: str) -> str:
        normalized = str(user_id or "").strip()
        if not normalized:
            raise ValueError("requestingUserId is required.")
        return hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()[:32]

    def _owned_apps(
        self,
        client: SandboxGroupClient,
        requesting_user_id: str,
    ) -> list[dict]:
        owner_hash = self._owner_hash(requesting_user_id)
        return [
            item
            for item in self._worker_apps(client)
            if item.get("labels", {}).get("generatedAppOwner")
            == owner_hash
        ]

    @staticmethod
    def _app_summary(item: dict[str, Any]) -> dict[str, Any]:
        labels = item.get("labels", {})
        port = next(iter(item.get("ports") or []), {})
        return {
            "appId": labels.get("generatedAppId"),
            "name": labels.get("generatedAppName"),
            "sandboxId": item.get("id"),
            "state": item.get("state"),
            "url": port.get("url"),
            "autoSuspendSeconds": int(
                item.get("autoSuspendSeconds")
                or AUTO_SUSPEND_SECONDS
            ),
            "retentionSeconds": int(
                item.get("retentionSeconds")
                or DEFAULT_TTL_SECONDS
            ),
            "retentionBasis": "after-suspension",
        }

    @staticmethod
    def _add_authenticated_port(
        sandbox_client,
        port: int,
        participant_emails: list[str],
    ) -> str:
        response = sandbox_client._dp_post(
            f"{sandbox_client._sbx_path}/ports/add",
            {
                "port": port,
                "activationMode": "OnDemand",
                "protocol": "Http",
                "auth": {
                    "entraId": {
                        "enabled": True,
                        "emails": participant_emails,
                    }
                },
            },
        )
        if isinstance(response, dict):
            candidates = (
                response.get("ports")
                if isinstance(response.get("ports"), list)
                else [response]
            )
        elif isinstance(response, list):
            candidates = response
        else:
            candidates = []
        for candidate in candidates:
            if (
                isinstance(candidate, dict)
                and int(candidate.get("port") or 0) == port
                and candidate.get("url")
            ):
                return str(candidate["url"])
        raise RuntimeError(
            "Generated app port did not return a URL."
        )

    def list_apps(
        self,
        requesting_user_id: str,
    ) -> dict[str, Any]:
        client = self._client()
        return {
            "apps": [
                self._app_summary(item)
                for item in self._owned_apps(
                    client,
                    requesting_user_id,
                )
            ]
        }

    def delete_app(
        self,
        app_id: str,
        requesting_user_id: str,
    ) -> dict[str, Any]:
        if not APP_ID_PATTERN.fullmatch(app_id):
            raise ValueError("appId is invalid.")
        client = self._client()
        removed = 0
        for item in self._owned_apps(
            client,
            requesting_user_id,
        ):
            if (
                item.get("labels", {}).get("generatedAppId")
                != app_id
            ):
                continue
            client.begin_delete_sandbox(
                item["id"],
                polling_timeout=600,
            ).result()
            removed += 1
        return {"appId": app_id, "deleted": removed > 0}

    def renew_app(
        self,
        app_id: str,
        requesting_user_id: str,
        retention_seconds: int,
    ) -> dict[str, Any]:
        if not APP_ID_PATTERN.fullmatch(app_id):
            raise ValueError("appId is invalid.")
        if retention_seconds not in CARD_RETENTION_SECONDS:
            raise ValueError(
                "retentionSeconds must be 3600, 21600, 86400, "
                "or 259200."
            )
        client = self._client()
        matched = [
            item
            for item in self._owned_apps(
                client,
                requesting_user_id,
            )
            if item.get("labels", {}).get("generatedAppId")
            == app_id
        ]
        if not matched:
            raise ValueError("Generated app was not found.")
        item = matched[0]
        sandbox_client = client.get_sandbox_client(item["id"])
        sandbox_client.set_lifecycle_policy(
            LifecyclePolicy(
                auto_suspend=AutoSuspendPolicy(
                    enabled=True,
                    interval=AUTO_SUSPEND_SECONDS,
                    mode="Memory",
                ),
                auto_delete=AutoDeletePolicy(
                    enabled=True,
                    delete_interval_seconds=retention_seconds,
                ),
            )
        )
        item["autoSuspendSeconds"] = AUTO_SUSPEND_SECONDS
        item["retentionSeconds"] = retention_seconds
        return {
            "status": "renewed",
            **self._app_summary(item),
        }

    def deploy_app(self, payload: object) -> dict[str, Any]:
        spec = validate_generated_app_payload(payload)
        client = self._client()
        worker_apps = self._worker_apps(client)
        owner_hash = self._owner_hash(spec["requestingUserId"])
        existing = [
            item
            for item in worker_apps
            if item.get("labels", {}).get("generatedAppId")
            == spec["appId"]
        ]
        if existing and any(
            item.get("labels", {}).get("generatedAppOwner")
            != owner_hash
            for item in existing
        ):
            raise ValueError(
                "The generated app belongs to another user."
            )
        active_count = len(worker_apps) - len(existing)
        if active_count >= MAX_ACTIVE_APPS:
            raise RuntimeError(
                f"Worker generated app quota is {MAX_ACTIVE_APPS}."
            )
        egress_policy = EgressPolicy(
            default_action="Deny",
            host_rules=[
                EgressHostRule(pattern=host, action="Allow")
                for host in spec["egressHosts"]
            ],
            traffic_inspection="Full",
        )
        sandbox_client = None
        try:
            sandbox_client = client.begin_create_sandbox(
                disk=RUNTIME_DISKS[spec["runtime"]],
                cpu="1000m",
                memory="2048Mi",
                auto_suspend_seconds=AUTO_SUSPEND_SECONDS,
                auto_suspend_mode="Memory",
                labels={
                    "app": "autopilots-generated-web-app",
                    "generatedAppId": spec["appId"],
                    "generatedAppName": re.sub(
                        r"[^A-Za-z0-9_-]",
                        "-",
                        spec["name"],
                    )[:50],
                    "generatedAppWorker": self.settings.worker_id,
                    "generatedAppOwner": owner_hash,
                },
                egress_policy=egress_policy,
                entrypoint=["sleep"],
                cmd=["infinity"],
                polling_timeout=600,
            ).result()
            for file in spec["files"]:
                sandbox_client.write_file(
                    f"/app/{file['path']}",
                    file["content"],
                    create_dirs=True,
                )
            for command_name in ("installCommand", "testCommand"):
                command = spec[command_name]
                if not command:
                    continue
                result = sandbox_client.exec(
                    shlex.join(command),
                    working_directory="/app",
                )
                if result.exit_code != 0:
                    raise RuntimeError(
                        f"{command_name} failed: "
                        f"{result.stderr or result.stdout}"
                    )
            start = shlex.join(spec["startCommand"])
            result = sandbox_client.exec(
                (
                    f"nohup {start} >/tmp/generated-app.log "
                    "2>&1 </dev/null &"
                ),
                working_directory="/app",
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"startCommand failed: {result.stderr}"
                )
            health = sandbox_client.exec(
                (
                    "sh -lc "
                    + shlex.quote(
                        "for attempt in 1 2 3 4 5 6 7 8 9 10; do "
                        f"curl -fsS --max-time 2 "
                        f"http://127.0.0.1:{spec['port']}/health "
                        ">/dev/null 2>&1 && exit 0; "
                        "sleep 2; done; exit 1"
                    )
                ),
                working_directory="/app",
            )
            if health.exit_code != 0:
                log = sandbox_client.exec(
                    "sh -lc 'tail -n 80 /tmp/generated-app.log "
                    "2>/dev/null || true'"
                )
                raise RuntimeError(
                    "Generated app did not become healthy: "
                    + str(log.stdout or log.stderr or "")
                )
            sandbox_client.set_lifecycle_policy(
                LifecyclePolicy(
                    auto_suspend=AutoSuspendPolicy(
                        enabled=True,
                        interval=AUTO_SUSPEND_SECONDS,
                        mode="Memory",
                    ),
                    auto_delete=AutoDeletePolicy(
                        enabled=True,
                        delete_interval_seconds=spec["ttlSeconds"],
                    ),
                )
            )
            url = self._add_authenticated_port(
                sandbox_client,
                spec["port"],
                spec["participantEmails"],
            )
            for item in existing:
                client.begin_delete_sandbox(
                    item["id"],
                    polling_timeout=600,
                ).result()
            return {
                "status": "deployed",
                "appId": spec["appId"],
                "name": spec["name"],
                "sandboxId": sandbox_client.sandbox_id,
                "url": url,
                "participantEmails": spec["participantEmails"],
                "requestingUserEmail": spec["requestingUserEmail"],
                "autoSuspendSeconds": AUTO_SUSPEND_SECONDS,
                "retentionSeconds": spec["ttlSeconds"],
                "retentionBasis": "after-suspension",
                "artifactDigest": hashlib.sha256(
                    b"".join(
                        file["content"] for file in spec["files"]
                    )
                ).hexdigest(),
            }
        except Exception:
            if sandbox_client is not None:
                sandbox_client.begin_delete(
                    polling_timeout=600
                ).result()
            raise
