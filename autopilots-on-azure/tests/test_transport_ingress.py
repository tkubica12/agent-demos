from __future__ import annotations

import unittest

import httpx
from fastapi import FastAPI, Request

from bridge.transport_ingress import NativeTransportIngressMiddleware, require_native_callback


class TransportIngressTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_callback_cannot_be_reached_through_public_port_or_spoofed_headers(self):
        app = FastAPI()
        app.add_middleware(NativeTransportIngressMiddleware)

        @app.post("/internal/transport/callback")
        async def callback(request: Request):
            require_native_callback(request)
            return {"envelope": await request.json(), "acknowledged": False}

        @app.get("/internal/transport/status")
        async def status():
            return {"private": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway:8000",
        ) as public:
            response = await public.post(
                "/internal/transport/callback", json={"MessageId": "message"},
                headers={"X-Autopilot-Key": "old-key", "X-Forwarded-Port": "8001", "Host": "gateway:8001"},
            )
            self.assertEqual(response.status_code, 401)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gateway:8001",
        ) as native:
            envelope = {"MessageId": "message", "LockToken": "opaque"}
            response = await native.post("/internal/transport/callback", json=envelope)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"envelope": envelope, "acknowledged": False})
            self.assertEqual((await native.get("/internal/transport/status")).status_code, 404)
            self.assertEqual((await native.get("/internal/transport/callback")).status_code, 404)
