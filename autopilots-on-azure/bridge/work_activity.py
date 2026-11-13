"""Gateway work accounting across HTTP turns and background continuations."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi.responses import JSONResponse


logger = logging.getLogger(__name__)
_active: dict[object, float] = {}
_keepalive: asyncio.Task[None] | None = None


def active_work_count() -> int:
    return len(_active)


async def _keep_active() -> None:
    url = os.getenv("AUTOPILOT_BRIDGE_URL", "").rstrip("/")
    if not url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        while _active:
            if any(time.monotonic() < deadline for deadline in _active.values()):
                try:
                    response = await client.get(url + "/health")
                    response.raise_for_status()
                except httpx.HTTPError:
                    logger.exception("Gateway active-work keepalive failed.")
            await asyncio.sleep(60)


def _begin() -> object:
    global _keepalive
    maximum = int(os.getenv("SCHEDULER_MAX_LOCK_RENEWAL_SECONDS", "1800"))
    if maximum <= 0:
        raise ValueError("The gateway active-work keepalive window must be positive.")
    token = object()
    _active[token] = time.monotonic() + maximum
    if _keepalive is None or _keepalive.done():
        _keepalive = asyncio.create_task(_keep_active())
    return token


def _end(token: object) -> None:
    global _keepalive
    del _active[token]
    if not _active and _keepalive is not None:
        _keepalive.cancel()
        _keepalive = None


@asynccontextmanager
async def active_work():
    token = _begin()
    try:
        yield
    finally:
        _end(token)


def start_active_work(coroutine: Any) -> asyncio.Task[Any]:
    # Reserve before scheduling so the parent cannot attest idle in between.
    token = _begin()
    try:
        task = asyncio.create_task(coroutine)
    except BaseException:
        _end(token)
        coroutine.close()
        raise

    def finished(done: asyncio.Task[Any]) -> None:
        _end(token)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("Gateway background turn failed.", exc_info=(type(error), error, error.__traceback__))

    task.add_done_callback(finished)
    return task


class ActiveWorkMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != "/api/messages":
            await self.app(scope, receive, send)
            return
        if Path("/app/.application-maintenance").exists():
            response = JSONResponse(
                {"error": "Application maintenance; the request was not admitted."},
                status_code=503, headers={"Retry-After": "10"},
            )
            await response(scope, receive, send)
            return
        async with active_work():
            await self.app(scope, receive, send)
