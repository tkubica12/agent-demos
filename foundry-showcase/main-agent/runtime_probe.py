"""Sandbox identity and concurrency probe for the hosted agent.

Foundry hosted agents scale per session, not per replica. This module exposes the
evidence needed to observe that behavior from the outside: which sandbox served a
request, how many requests that sandbox has served, and how many of them
overlapped in time.
"""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

INSTANCE_ID = uuid.uuid4().hex
PROCESS_STARTED_AT = time.time()

_REDACTED_HEADER_PARTS = ("authorization", "cookie", "token", "secret")
_PLATFORM_HEADER_PREFIXES = ("x-agent-", "x-ms-", "x-session-", "x-correlation-")
_PLATFORM_ENV_PARTS = ("AGENT", "SESSION")
_REDACTED_ENV_PARTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CONNECTION_STRING")
_MARKER_NAME = ".foundry-showcase-sandbox-marker.json"


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, UTC).isoformat()


def _read_first_int(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError):
        return None
    if raw in {"max", "-1"}:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def container_limits() -> dict[str, Any]:
    memory_limit = _read_first_int("/sys/fs/cgroup/memory.max") or _read_first_int(
        "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    )
    cpu_quota: float | None = None
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()
        if quota != "max":
            cpu_quota = int(quota) / int(period)
    except (OSError, ValueError):
        cpu_quota = None
    return {
        "cpuCount": os.cpu_count(),
        "cpuQuota": cpu_quota,
        "memoryLimitMiB": round(memory_limit / (1024 * 1024), 1) if memory_limit else None,
    }


def platform_headers(headers: Any) -> dict[str, str]:
    collected: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if not lowered.startswith(_PLATFORM_HEADER_PREFIXES):
            continue
        if any(part in lowered for part in _REDACTED_HEADER_PARTS):
            value = "<redacted>"
        collected[lowered] = value
    return collected


def platform_environment() -> dict[str, str]:
    collected: dict[str, str] = {}
    for name, value in os.environ.items():
        if not any(part in name.upper() for part in _PLATFORM_ENV_PARTS):
            continue
        if any(part in name.upper() for part in _REDACTED_ENV_PARTS):
            value = "<redacted>"
        collected[name] = value
    return collected


class SandboxProbe:
    """Tracks per-process request counters inside a single hosted agent sandbox."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0
        self.requests_served = 0
        self.first_request_at: float | None = None
        self.last_request_at: float | None = None

    @contextmanager
    def track(self) -> Iterator[None]:
        now = time.time()
        self.in_flight += 1
        self.requests_served += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        if self.first_request_at is None:
            self.first_request_at = now
        self.last_request_at = now
        try:
            yield
        finally:
            self.in_flight -= 1

    def session_marker(self, session_hint: str | None) -> dict[str, Any]:
        """Read or create a marker in ``$HOME`` to expose session filesystem reuse."""

        marker_path = Path(os.path.expanduser("~")) / _MARKER_NAME
        existing: dict[str, Any] | None = None
        try:
            existing = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing is None:
            existing = {
                "createdByInstanceId": INSTANCE_ID,
                "createdAt": _iso(time.time()),
                "sessionHint": session_hint,
                "resumeCount": 0,
            }
        elif existing.get("createdByInstanceId") != INSTANCE_ID:
            existing["resumeCount"] = int(existing.get("resumeCount", 0)) + 1
            existing["lastResumedByInstanceId"] = INSTANCE_ID
            existing["lastResumedAt"] = _iso(time.time())
        try:
            marker_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            existing["markerPath"] = str(marker_path)
        except OSError as exc:
            existing["markerError"] = str(exc)
        return existing

    def snapshot(self, *, headers: Any = None, session_hint: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instanceId": INSTANCE_ID,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "processStartedAt": _iso(PROCESS_STARTED_AT),
            "processUptimeSeconds": round(time.time() - PROCESS_STARTED_AT, 3),
            "requestsServed": self.requests_served,
            "inFlight": self.in_flight,
            "maxInFlight": self.max_in_flight,
            "firstRequestAt": _iso(self.first_request_at),
            "lastRequestAt": _iso(self.last_request_at),
            "limits": container_limits(),
            "platformEnvironment": platform_environment(),
            "sessionMarker": self.session_marker(session_hint),
        }
        if headers is not None:
            payload["platformHeaders"] = platform_headers(headers)
        return payload


probe = SandboxProbe()
