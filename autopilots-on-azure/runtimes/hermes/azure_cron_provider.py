from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import httpx
from azure.core.credentials import AccessToken, TokenCredential
from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from cron.scheduler_provider import CronScheduler


TOKEN_EXCHANGE_SCOPE = "api://AzureADTokenExchange/.default"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for Azure cron scheduling.")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def schedule_revision(job: dict[str, Any]) -> str:
    relevant = {
        key: job.get(key)
        for key in (
            "id",
            "prompt",
            "skills",
            "model",
            "provider",
            "schedule",
            "repeat",
            "enabled",
            "state",
            "next_run_at",
            "deliver",
            "origin",
            "attach_to_session",
        )
    }
    return hashlib.sha256(canonical_json(relevant).encode("utf-8")).hexdigest()


class AgentIdentityCredential(TokenCredential):
    def __init__(
        self,
        *,
        credential_factory: Callable[[], TokenCredential] = DefaultAzureCredential,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ) -> None:
        self._credential_factory = credential_factory
        self._client_factory = client_factory
        self._credential: TokenCredential | None = None
        self._cache: dict[str, AccessToken] = {}
        self._lock = threading.Lock()

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        if not scopes:
            raise ValueError("A Service Bus token scope is required.")
        scope = scopes[0]
        with self._lock:
            cached = self._cache.get(scope)
            if cached and cached.expires_on > time.time() + 60:
                return cached
            token = self._exchange_token(scope)
            self._cache[scope] = token
            return token

    def _exchange_token(self, scope: str) -> AccessToken:
        tenant_id = required_env("AGENT365_TENANT_ID")
        blueprint_client_id = required_env("AGENT365_BLUEPRINT_CLIENT_ID")
        agent_identity_client_id = required_env("AGENT365_AGENT_IDENTITY_CLIENT_ID")
        credential = self._credential
        if credential is None:
            credential = self._credential_factory()
            self._credential = credential
        managed_token = credential.get_token(TOKEN_EXCHANGE_SCOPE).token
        endpoint = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        with self._client_factory(timeout=30) as client:
            blueprint_response = client.post(
                endpoint,
                data={
                    "client_id": blueprint_client_id,
                    "scope": TOKEN_EXCHANGE_SCOPE,
                    "grant_type": "client_credentials",
                    "client_assertion_type": CLIENT_ASSERTION_TYPE,
                    "client_assertion": managed_token,
                    "fmi_path": agent_identity_client_id,
                },
            )
            blueprint_response.raise_for_status()
            blueprint_token = blueprint_response.json()["access_token"]
            response = client.post(
                endpoint,
                data={
                    "client_id": agent_identity_client_id,
                    "scope": scope,
                    "grant_type": "client_credentials",
                    "client_assertion_type": CLIENT_ASSERTION_TYPE,
                    "client_assertion": blueprint_token,
                },
            )
            response.raise_for_status()
            payload = response.json()
        expires_in = int(payload.get("expires_in", 3600))
        return AccessToken(str(payload["access_token"]), int(time.time()) + expires_in)


class AzureCronScheduler(CronScheduler):
    def __init__(
        self,
        *,
        client_factory: Callable[..., ServiceBusClient] = ServiceBusClient,
        credential_factory: Callable[[], TokenCredential] = AgentIdentityCredential,
    ) -> None:
        self._client_factory = client_factory
        self._credential_factory = credential_factory
        self._client: ServiceBusClient | None = None
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "azure"

    @property
    def state_path(self) -> Path:
        from cron.jobs import CRON_DIR

        return CRON_DIR / "azure-schedules.json"

    def is_available(self) -> bool:
        return bool(
            os.getenv("SCHEDULER_SERVICEBUS_NAMESPACE", "").strip()
            and os.getenv("SCHEDULER_SERVICEBUS_QUEUE", "").strip()
            and os.getenv("AGENT365_TENANT_ID", "").strip()
            and os.getenv("AGENT365_BLUEPRINT_CLIENT_ID", "").strip()
            and os.getenv("AGENT365_AGENT_IDENTITY_CLIENT_ID", "").strip()
        )

    def start(self, stop_event, *, adapters=None, loop=None, interval=60):
        self.reconcile()

    def stop(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def on_jobs_changed(self) -> None:
        if os.getenv("SERVICEBUS_DREAM_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            from cron.jobs import CRON_DIR
            from cron_runtime import ensure_system_dream_schedule

            ensure_system_dream_schedule(
                CRON_DIR.parent,
                enabled=True,
                schedule=required_env(
                    "SERVICEBUS_DREAM_CRON_EXPRESSION"
                ),
            )
        self.reconcile()

    def current_revision(self, job_id: str) -> str:
        from cron.jobs import get_job

        job = get_job(job_id)
        return schedule_revision(job) if job else ""

    def fire_due(
        self,
        job_id: str,
        *,
        adapters: Any = None,
        loop: Any = None,
    ) -> bool:
        ran = super().fire_due(job_id, adapters=adapters, loop=loop)
        self.reconcile()
        return ran

    def reconcile(self) -> None:
        from cron.jobs import CRON_DIR, load_jobs

        with self._lock:
            bindings_path = CRON_DIR.parent / "local" / "cron-delivery.json"
            bindings: dict[str, Any] = {}
            if bindings_path.is_file():
                payload = json.loads(bindings_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    bindings = payload
            desired = {
                job["id"]: job
                for job in load_jobs()
                if job.get("enabled")
                and job.get("next_run_at")
                and job.get("state") != "paused"
                and not job.get("script")
                and not job.get("no_agent")
                and isinstance(bindings.get(job["id"]), dict)
                and (
                    bool(bindings[job["id"]].get("referenceKey"))
                    or bindings[job["id"]].get("local") is True
                    or bindings[job["id"]].get("systemType") == "dream"
                )
            }
            observed = self._load_state()
            updated = dict(observed)
            for job_id, job in desired.items():
                revision = schedule_revision(job)
                fire_at = str(job["next_run_at"])
                current = observed.get(job_id) or {}
                if current.get("revision") == revision and current.get("fireAt") == fire_at:
                    continue
                self._cancel(current.get("sequenceNumber"))
                sequence_number = self._schedule(
                    job,
                    revision,
                    bindings[job_id],
                )
                updated[job_id] = {
                    "fireAt": fire_at,
                    "revision": revision,
                    "sequenceNumber": sequence_number,
                }
            for job_id in set(observed) - set(desired):
                self._cancel(observed[job_id].get("sequenceNumber"))
                updated.pop(job_id, None)
            self._write_state(updated)

    def _servicebus_client(self) -> ServiceBusClient:
        if self._client is None:
            self._client = self._client_factory(
                required_env("SCHEDULER_SERVICEBUS_NAMESPACE"),
                credential=self._credential_factory(),
            )
        return self._client

    def _schedule(
        self,
        job: dict[str, Any],
        revision: str,
        binding: dict[str, Any],
    ) -> int:
        worker_id = required_env("WORKER_ID")
        fire_at = datetime.fromisoformat(str(job["next_run_at"]).replace("Z", "+00:00"))
        message_type = (
            "system.dream"
            if binding.get("systemType") == "dream"
            else "hermes.cron.fire"
        )
        body = {
            "version": "1.0",
            "type": message_type,
            "workerId": worker_id,
            "jobId": job["id"],
            "revision": revision,
            "occurrenceId": revision,
            "dueAt": str(job["next_run_at"]),
        }
        message = ServiceBusMessage(
            canonical_json(body),
            message_id=f"{worker_id}:{job['id']}:{revision}",
            content_type="application/json",
            subject=message_type,
        )
        with self._servicebus_client().get_queue_sender(
            required_env("SCHEDULER_SERVICEBUS_QUEUE")
        ) as sender:
            sequence_numbers = sender.schedule_messages(message, fire_at)
        return int(sequence_numbers[0])

    def enqueue_now(
        self,
        job: dict[str, Any],
        revision: str,
        binding: dict[str, Any],
    ) -> dict[str, str]:
        worker_id = required_env("WORKER_ID")
        message_type = (
            "system.dream"
            if binding.get("systemType") == "dream"
            else "hermes.cron.fire"
        )
        occurrence_id = f"manual-{uuid.uuid4().hex}"
        message_id = f"{worker_id}:{job['id']}:{occurrence_id}"
        message = ServiceBusMessage(
            canonical_json({
                "version": "1.0",
                "type": message_type,
                "workerId": worker_id,
                "jobId": job["id"],
                "revision": revision,
                "occurrenceId": occurrence_id,
                "dueAt": datetime.now().astimezone().isoformat(),
            }),
            message_id=message_id,
            content_type="application/json",
            subject=message_type,
        )
        with self._servicebus_client().get_queue_sender(
            required_env("SCHEDULER_SERVICEBUS_QUEUE")
        ) as sender:
            sender.send_messages(message)
        return {
            "messageId": message_id,
            "occurrenceId": occurrence_id,
        }

    def _cancel(self, sequence_number: Any) -> None:
        if sequence_number in (None, ""):
            return
        try:
            with self._servicebus_client().get_queue_sender(
                required_env("SCHEDULER_SERVICEBUS_QUEUE")
            ) as sender:
                sender.cancel_scheduled_messages(int(sequence_number))
        except Exception:
            # Service Bus cancellation racing activation is non-atomic.
            # Revision validation at fire time remains the correctness boundary.
            return

    def _load_state(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.is_file():
            return {}
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}

    def _write_state(self, payload: dict[str, dict[str, Any]]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            dir=str(self.state_path.parent),
            prefix=".azure-schedules-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.state_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


def register(ctx) -> None:
    ctx.register_cron_scheduler(AzureCronScheduler())
