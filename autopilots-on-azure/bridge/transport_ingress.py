from __future__ import annotations

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse


CALLBACK_PORT = 8001
CALLBACK_PATH = "/internal/transport/callback"


def require_native_callback(request: Request) -> None:
    server = request.scope.get("server")
    if not server or server[1] != CALLBACK_PORT:
        raise HTTPException(status_code=401, detail="Scheduling callbacks require the authenticated native ingress.")


class NativeTransportIngressMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            server = scope.get("server")
            native = bool(server and server[1] == CALLBACK_PORT)
            callback = scope.get("path") == CALLBACK_PATH
            if native and (not callback or scope["method"] != "POST"):
                await JSONResponse({"detail": "Not found."}, status_code=404)(scope, receive, send)
                return
            if callback and not native:
                await JSONResponse(
                    {"detail": "Scheduling callbacks require the authenticated native ingress."}, status_code=401,
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)
