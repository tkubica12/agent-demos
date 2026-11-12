"""Self-ingress activity while native execution or a runtime request is running."""

from __future__ import annotations

import atexit
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any


INTERVAL_SECONDS = 60
MAX_OPERATION_SECONDS = 1800
logger = logging.getLogger(__name__)


def sandbox_health_url() -> str | None:
    sandbox_id = os.getenv("ADC_SANDBOX_ID", "").strip()
    suffix = os.getenv("ADC_PROXY_DNS_SUFFIX", "").strip()
    if not sandbox_id and not suffix:
        return None
    if not sandbox_id or not suffix:
        raise ValueError("Sandbox keepalive requires ADC_SANDBOX_ID and ADC_PROXY_DNS_SUFFIX.")
    sandbox_id = str(uuid.UUID(sandbox_id))
    if not re.fullmatch(r"[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*", suffix):
        raise ValueError("ADC_PROXY_DNS_SUFFIX must be a DNS suffix.")
    port = int(os.getenv("RUNTIME_INGRESS_PORT", "8642"))
    if not 1 <= port <= 65535:
        raise ValueError("RUNTIME_INGRESS_PORT must be between 1 and 65535.")
    return f"https://{sandbox_id}--{port}.{suffix}/health"


def ping(url: str) -> int | None:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        logger.warning("Keepalive ingress returned HTTP %s.", error.code)
        return error.code
    except OSError:
        logger.exception("Keepalive ingress request failed.")
        return None


class Keepalive:
    def __init__(
        self,
        url: str,
        *,
        interval: float = INTERVAL_SECONDS,
        maximum: float = MAX_OPERATION_SECONDS,
        clock: Callable[[], float] = time.time,
        send: Callable[[str], int | None] = ping,
    ) -> None:
        if not math.isfinite(interval) or not math.isfinite(maximum) or not 0 < interval < maximum:
            raise ValueError("Keepalive requires 0 < interval < maximum.")
        self.url = url
        self.interval = interval
        self.maximum = maximum
        self._clock = clock
        self._send = send
        self._lock = threading.Lock()
        self._operations: dict[object, tuple[float, threading.Thread, str]] = {}
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_ping = 0.0
        self.last_status: int | None = None

    @contextmanager
    def activity(self, name: str) -> Iterator[None]:
        token = object()
        with self._lock:
            self._operations[token] = (self._clock(), threading.current_thread(), name)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="hermes-keepalive", daemon=True,
                )
                self._thread.start()
        self._wake.set()
        try:
            yield
        finally:
            with self._lock:
                self._operations.pop(token, None)

    def tick(self) -> int:
        now = self._clock()
        with self._lock:
            for token, (started, owner, name) in list(self._operations.items()):
                if not owner.is_alive() or now - started >= self.maximum:
                    logger.error("Dropping orphaned or expired keepalive operation: %s.", name)
                    del self._operations[token]
            active = len(self._operations)
        if active and now >= self._next_ping:
            self.last_status = self._send(self.url)
            self._next_ping = now + self.interval
        elif not active:
            self._next_ping = 0.0
        return active

    def status(self) -> dict[str, int]:
        with self._lock:
            return {"active": len(self._operations)}

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.tick()
            self._wake.wait(self.interval)

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=15)


@lru_cache(maxsize=1)
def get_keepalive() -> Keepalive | None:
    url = sandbox_health_url()
    if url is None:
        return None
    supervisor = Keepalive(url)
    atexit.register(supervisor.close)
    return supervisor


def register(context: Any) -> None:
    supervisor = get_keepalive()
    if supervisor is None:
        logger.info("Keepalive is inactive outside an ACA Sandbox.")
        return

    def llm_execution(
        *, request: dict[str, Any], next_call: Callable[..., Any], **_: Any,
    ) -> Any:
        with supervisor.activity("model"):
            return next_call(request)

    def tool_execution(
        *, args: dict[str, Any], next_call: Callable[..., Any], **_: Any,
    ) -> Any:
        with supervisor.activity("tool"):
            return next_call(args)

    context.register_middleware("llm_execution", llm_execution)
    context.register_middleware("tool_execution", tool_execution)
