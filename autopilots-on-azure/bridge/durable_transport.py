from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import time
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from azure.identity import ManagedIdentityCredential

from bridge.local_state import locked_file


CONNECTOR_API = "2026-05-01-preview"
MAX_MESSAGE_BYTES = 64 * 1024
delivery_identity: ContextVar[str] = ContextVar("transport_delivery", default="")
delivery_queue: ContextVar[str] = ContextVar("transport_queue", default="")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def effect_payload(body: dict[str, Any]) -> str:
    value = json.loads(canonical(body))
    value.pop("trace", None)
    return canonical(value)


def enabled() -> bool:
    return bool(os.getenv("TRANSPORT_CONNECTION_ID", "").strip())


def conversation_state_enabled() -> bool:
    return enabled() or bool(os.getenv("TRANSPORT_JOURNAL_PATH", "").strip())


@dataclass(frozen=True)
class TransportSettings:
    worker: str
    role: str
    identity_client_id: str
    namespace: str
    connection_id: str
    schedule_queue: str
    journal_path: Path

    @classmethod
    def from_environment(cls) -> TransportSettings:
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required for native scheduling transport.")
            return value

        role = required("TRANSPORT_ROLE")
        if role != "gateway":
            raise ValueError("Managed scheduling processing belongs to the gateway.")
        return cls(
            worker=required("WORKER_ID"),
            role=role,
            identity_client_id=required("AZURE_CLIENT_ID"),
            namespace=required("SCHEDULER_SERVICEBUS_NAMESPACE"),
            connection_id=required("TRANSPORT_CONNECTION_ID"),
            schedule_queue=os.getenv("SCHEDULER_SERVICEBUS_QUEUE", "").strip(),
            journal_path=Path(required("TRANSPORT_JOURNAL_PATH")),
        )

    @property
    def receiving_queues(self) -> tuple[str, ...]:
        return (self.schedule_queue,) if self.schedule_queue else ()


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS deliveries (
                    queue TEXT NOT NULL, message_id TEXT NOT NULL,
                    payload TEXT NOT NULL, lock_token TEXT NOT NULL,
                    connection_id TEXT NOT NULL, delivery_count INTEGER NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'pending',
                    result TEXT NOT NULL DEFAULT '',
                    settled INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    native_scope TEXT NOT NULL DEFAULT '', admission_at REAL NOT NULL DEFAULT 0,
                    native_run_id TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL DEFAULT 0,
                    effect_stage TEXT NOT NULL DEFAULT 'preclaim',
                    resolution TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(queue, message_id)
                )
            """)
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(deliveries)")}
            for name, definition in {
                "updated_at": "REAL NOT NULL DEFAULT 0",
                "effect_stage": "TEXT NOT NULL DEFAULT 'preclaim'",
                "resolution": "TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE deliveries ADD COLUMN {name} {definition}")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS conversation_state (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL
                )
            """)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def admit(
        self, queue: str, envelope: dict[str, Any], body: dict[str, Any], connection_id: str,
    ) -> dict[str, Any]:
        message_id = str(envelope["MessageId"])
        payload = canonical(body)
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM deliveries WHERE queue=? AND message_id=?", (queue, message_id),
            ).fetchone()
            if row:
                if effect_payload(json.loads(row["payload"])) != effect_payload(body):
                    raise ValueError("A duplicate broker MessageId changed its payload.")
                if row["lock_token"] == envelope["LockToken"] and row["connection_id"] != connection_id:
                    raise ValueError("The same PeekLock lease cannot change its owning connection.")
                connection.execute("""
                    UPDATE deliveries SET
                    settled=CASE WHEN lock_token<>? THEN 0 ELSE settled END,
                    lock_token=?, connection_id=?, delivery_count=?, updated_at=?
                    WHERE queue=? AND message_id=?
                """, (envelope["LockToken"], envelope["LockToken"], connection_id,
                      envelope["Properties"]["DeliveryCount"], time.time(), queue, message_id))
            else:
                connection.execute("""
                    INSERT INTO deliveries(queue,message_id,payload,lock_token,connection_id,delivery_count,updated_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (queue, message_id, payload, envelope["LockToken"], connection_id,
                      envelope["Properties"]["DeliveryCount"], time.time()))
        return self.get(queue, message_id)

    def get(self, queue: str, message_id: str) -> dict[str, Any]:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE queue=? AND message_id=?", (queue, message_id),
            ).fetchone()
        if row is None:
            raise ValueError("No durable receipt exists for this delivery.")
        return dict(row)

    def update(self, queue: str, message_id: str, **values: Any) -> None:
        permitted = {
            "phase", "result", "settled", "error",
            "native_scope", "admission_at", "native_run_id",
            "updated_at", "effect_stage", "resolution",
        }
        if not values or set(values) - permitted:
            raise ValueError("Invalid transport receipt update.")
        values.setdefault("updated_at", time.time())
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE deliveries SET " + ",".join(f"{key}=?" for key in values)
                + " WHERE queue=? AND message_id=?", (*values.values(), queue, message_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Cannot update a missing transport receipt.")

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with locked_file(self.path.with_suffix(self.path.suffix + ".backup.lock")):
            local = self.path.with_name(f".{self.path.name}.checkpoint")
            staged = destination.with_name(f".{destination.name}-{local.name}.next")
            try:
                with closing(self.connect()) as source, closing(sqlite3.connect(local)) as target:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Transport journal backup failed SQLite integrity validation.")
                shutil.copyfile(local, staged)
                staged.replace(destination)
            finally:
                local.unlink(missing_ok=True)
                staged.unlink(missing_ok=True)

    def mark_settled(self, queue: str, message_id: str, lock_token: str) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE deliveries SET settled=1, updated_at=? WHERE queue=? AND message_id=? AND lock_token=?",
                (time.time(), queue, message_id, lock_token),
            )

    def sweep(self, active: set[tuple[str, str]], *, max_attempts: int, stale_seconds: int) -> int:
        changed = 0
        with closing(self.connect()) as connection, connection:
            rows = connection.execute(
                "SELECT * FROM deliveries WHERE phase NOT IN ('completed','failed','unknown')"
            ).fetchall()
            for row in rows:
                if (row["queue"], row["message_id"]) in active:
                    continue
                if row["delivery_count"] < max_attempts or time.time() - row["updated_at"] < stale_seconds:
                    continue
                phase = (
                    "failed" if row["effect_stage"] == "preclaim"
                    and not row["native_scope"] and not row["native_run_id"] else "unknown"
                )
                connection.execute(
                    "UPDATE deliveries SET phase=?, error=?, updated_at=? WHERE queue=? AND message_id=?",
                    (phase, "Last broker attempt stranded; inspect the DLQ and resolve without replaying effects.",
                     time.time(), row["queue"], row["message_id"]),
                )
                changed += 1
        return changed

    def resolve(self, queue: str, message_id: str, *, outcome: str, operator: str, reason: str) -> None:
        if outcome not in {"completed", "skipped"} or not operator.strip() or not reason.strip():
            raise ValueError("Resolution requires completed/skipped, operator identity and evidence/reason.")
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT phase FROM deliveries WHERE queue=? AND message_id=?", (queue, message_id),
            ).fetchone()
            if row is None or row["phase"] not in {"unknown", "failed"}:
                raise ValueError("Only a quarantined delivery can be resolved.")
            connection.execute(
                "UPDATE deliveries SET phase=?, resolution=?, updated_at=? WHERE queue=? AND message_id=?",
                ("completed",
                 canonical({"outcome": outcome, "operator": operator.strip(), "reason": reason.strip(),
                            "at": time.time()}), time.time(), queue, message_id),
            )

    def load_state(self, key: str) -> Any:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM conversation_state WHERE key=?", (key,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_state(self, key: str, payload: Any) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT INTO conversation_state(key,payload) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload", (key, canonical(payload)),
            )

    def claim_effect(self, key: str) -> Any:
        with closing(self.connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM conversation_state WHERE key=?", (key,),
            ).fetchone()
            if row:
                return json.loads(row["payload"])
            connection.execute(
                "INSERT INTO conversation_state(key,payload) VALUES (?,?)",
                (key, canonical({"phase": "started"})),
            )
        return None


    def remove_obsolete_recovery_data(self, activity_queue: str) -> bool:
        with closing(self.connect()) as connection, connection:
            removed = connection.execute(
                "DELETE FROM deliveries WHERE queue=?", (activity_queue,)
            ).rowcount
            removed += connection.execute(
                "DELETE FROM conversation_state WHERE key LIKE 'payload:%' OR key LIKE 'outbound:%'"
            ).rowcount
            has_responses = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='responses'"
            ).fetchone()
            connection.execute("DROP TABLE IF EXISTS responses")
        return bool(removed or has_responses)


def decode_callback(
    envelope: dict[str, Any], queue: str, settings: TransportSettings,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not settings.schedule_queue or queue != settings.schedule_queue:
        raise ValueError("Native callback queue does not belong to this settling actor.")
    if not isinstance(envelope, dict):
        raise ValueError("Native callback envelope must be an object.")
    if len(canonical(envelope).encode("utf-8")) > MAX_MESSAGE_BYTES * 3:
        raise ValueError("Native callback exceeds the bounded envelope size.")
    body = json.loads(base64.b64decode(envelope["ContentData"], validate=True))
    message_id = envelope.get("MessageId")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
        raise ValueError("Native callback has no bounded broker MessageId.")
    if not isinstance(envelope.get("LockToken"), str) or not envelope["LockToken"]:
        raise ValueError("Native callback has no PeekLock token.")
    properties = envelope.get("Properties")
    if not isinstance(properties, dict):
        raise ValueError("Native callback has no broker properties.")
    count = properties.get("DeliveryCount")
    if isinstance(count, bool) or not str(count).isdigit() or int(count) < 1:
        raise ValueError("Native callback has no valid broker delivery count.")
    envelope["Properties"]["DeliveryCount"] = int(count)
    if not isinstance(body, dict):
        raise ValueError("Native callback body must be an object.")
    carrier = body.get("trace", {})
    if not isinstance(carrier, dict) or any(
        name not in {"traceparent", "tracestate", "x-autopilot-otel-session", "x-autopilot-otel-operation"}
        or not isinstance(value, str)
        for name, value in carrier.items()
    ):
        raise ValueError("The transport trace carrier contains unsupported fields.")
    if body.get("version") != "1.0" or body.get("type") not in {
        "hermes.cron.fire", "system.dream", "document.publish.retry",
    }:
        raise ValueError("Native schedule callback contains an unsupported job.")
    if body.get("workerId") != settings.worker:
        raise ValueError("Native scheduled job belongs to another Worker.")
    if body["type"] == "document.publish.retry" and not body.get("operationId"):
        raise ValueError("Native document retry requires its operation identity.")
    return envelope, body


class NativeBus:
    def __init__(self, settings: TransportSettings, credential: Any) -> None:
        self.settings = settings
        self.credential = credential

    def settle(self, queue: str, operation: str, lock_token: str) -> None:
        if queue not in self.settings.receiving_queues or operation not in {"renewlock", "complete", "deadletter"}:
            raise ValueError("Unsupported queue or native settlement operation.")
        queries = {"lockToken": lock_token, "sessionId": ""}
        if operation == "deadletter":
            queries.update(
                deadLetterReason="autopilots_quarantined",
                deadLetterErrorDescription=(
                    "Execution outcome requires operator inspection of the actor journal. "
                    "Automatic replay is blocked."
                ),
            )
        else:
            queries["queueType"] = "Main"
        token = self.credential.get_token("https://management.azure.com/.default").token
        request = urllib.request.Request(
            f"https://management.azure.com{self.settings.connection_id}/dynamicInvoke"
            f"?api-version={CONNECTOR_API}",
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            data=canonical({"request": {
                "method": "DELETE" if operation == "complete" else "POST",
                "path": f"/{quote(queue, safe='')}/messages/{operation}",
                "queries": queries,
            }}).encode("utf-8"),
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.load(response)
        status = result.get("response", {}).get("statusCode")
        if status not in (200, 204, "OK", "NoContent"):
            raise RuntimeError(f"Native {operation} failed with nested status {status!r}.")


@cache
def settings() -> TransportSettings:
    return TransportSettings.from_environment()


@cache
def journal() -> Journal:
    if enabled():
        return Journal(settings().journal_path)
    path = Path(os.environ["TRANSPORT_JOURNAL_PATH"])
    backup = os.getenv("TRANSPORT_JOURNAL_BACKUP", "").strip()
    if not path.exists() and backup and Path(backup).exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(backup, path)
    instance = Journal(path)
    with closing(instance.connect()) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("The restored conversation journal failed SQLite integrity validation.")
    return instance


def save_conversation_state(key: str, payload: Any) -> None:
    instance = journal()
    instance.save_state(key, payload)
    backup = os.getenv("TRANSPORT_JOURNAL_BACKUP", "").strip()
    if not enabled() and backup:
        instance.backup(Path(backup))


@contextmanager
def bus() -> Iterator[NativeBus]:
    with ManagedIdentityCredential(client_id=settings().identity_client_id) as credential:
        yield NativeBus(settings(), credential)
