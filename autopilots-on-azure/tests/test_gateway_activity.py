import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

from bridge import work_activity


class GatewayActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_keepalive_does_not_falsely_attest_quiescence(self):
        requests = []
        client_class = httpx.AsyncClient

        async def one_tick(_seconds):
            raise asyncio.CancelledError

        for remaining in (-1, 60):
            requests.clear()
            with (
                patch.dict("os.environ", {"AUTOPILOT_BRIDGE_URL": "https://gateway"}),
                patch.dict(work_activity._active, {object(): time.monotonic() + remaining}, clear=True),
                patch.object(work_activity.asyncio, "sleep", side_effect=one_tick),
                patch.object(work_activity.httpx, "AsyncClient", side_effect=lambda **kwargs: client_class(
                    transport=httpx.MockTransport(lambda request: (
                        requests.append(request) or httpx.Response(200)
                    )), **kwargs,
                )),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await work_activity._keep_active()
                self.assertEqual(len(requests), int(remaining > 0))
                self.assertEqual(work_activity.active_work_count(), 1)

    async def test_background_reserves_work_before_parent_exits_and_releases_on_cancel(self):
        async def continuation():
            await asyncio.Event().wait()

        with patch.dict("os.environ", {"AUTOPILOT_BRIDGE_URL": ""}):
            async with work_activity.active_work():
                task = work_activity.start_active_work(continuation())
                self.assertEqual(work_activity.active_work_count(), 2)
            self.assertEqual(work_activity.active_work_count(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0)
            self.assertEqual(work_activity.active_work_count(), 0)

    async def test_exception_does_not_leave_gateway_busy(self):
        with self.assertRaises(ValueError):
            async with work_activity.active_work():
                raise ValueError("failed turn")
        self.assertEqual(work_activity.active_work_count(), 0)

    async def test_http_scope_includes_attachment_processing_and_final_response_send(self):
        observations = []

        async def app(scope, receive, send):
            observations.append(work_activity.active_work_count())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"done"})

        async def send(message):
            observations.append(work_activity.active_work_count())

        middleware = work_activity.ActiveWorkMiddleware(app)
        await middleware({"type": "http", "path": "/api/messages"}, None, send)
        self.assertEqual(observations, [1, 1, 1])
        self.assertEqual(work_activity.active_work_count(), 0)

    async def test_maintenance_rejects_before_admission(self):
        messages = []

        async def app(*args):
            self.fail("A turn was admitted during maintenance.")

        async def send(message):
            messages.append(message)

        with patch.object(work_activity.Path, "exists", return_value=True):
            await work_activity.ActiveWorkMiddleware(app)(
                {"type": "http", "path": "/api/messages"}, None, send,
            )
        self.assertEqual(messages[0]["status"], 503)
        self.assertEqual(work_activity.active_work_count(), 0)
