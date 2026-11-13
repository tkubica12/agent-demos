import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from uuid import uuid5

import jwt
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .auth import Identity
from .config import DEMO_NAMESPACE


class DemoLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    accessCode: str = Field(min_length=1, max_length=1024, repr=False)
    displayName: str = Field(min_length=1, max_length=200)


class DemoAuth:
    def __init__(self, settings):
        self.settings = settings
        path = settings.storage_path.parent / "demo-session.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self.key = path.read_bytes()
        else:
            self.key = secrets.token_bytes(32)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self.key)
        if len(self.key) != 32:
            raise ValueError("Invalid persisted demo session signing key")
        self.attempts = defaultdict(deque)
        self.total_attempts = deque()

    def require_active(self):
        if time.time() >= self.settings.demo_expiry:
            raise HTTPException(410, "Demo expired; access is disabled")

    def login(self, body, address):
        self.require_active()
        now = time.monotonic()
        for key in list(self.attempts):
            while self.attempts[key] and self.attempts[key][0] <= now - 60:
                self.attempts[key].popleft()
            if not self.attempts[key]:
                del self.attempts[key]
        while self.total_attempts and self.total_attempts[0] <= now - 60:
            self.total_attempts.popleft()
        attempts = self.attempts[address]
        if len(attempts) >= 5 or len(self.total_attempts) >= 100:
            raise HTTPException(429, "Too many demo login attempts; retry in one minute",
                                headers={"Retry-After": "60"})
        attempts.append(now)
        self.total_attempts.append(now)
        if not hmac.compare_digest(body.accessCode.encode(), self.settings.demo_access_code.encode()):
            raise HTTPException(401, "Invalid demo access code")
        name = body.displayName.strip()
        if not name or any(ord(char) < 32 for char in name):
            raise HTTPException(422, "A nonblank display name without control characters is required")
        subject = str(uuid5(DEMO_NAMESPACE, name.lower()))
        expiry = min(int(time.time()) + 3600, int(self.settings.demo_expiry))
        token = jwt.encode({"iss": "agent-chat-simulator-demo", "aud": "simulator",
                            "sub": subject, "name": name, "iat": int(time.time()), "exp": expiry},
                           self.key, algorithm="HS256")
        return {"accessToken": token, "expiresAt": expiry}

    async def verify(self, token):
        self.require_active()
        if not isinstance(token, str) or not 1 <= len(token) <= 4096:
            raise HTTPException(401, "Invalid demo session")
        try:
            claims = jwt.decode(token, self.key, algorithms=["HS256"], audience="simulator",
                                issuer="agent-chat-simulator-demo",
                                options={"require": ["sub", "name", "iat", "exp"]})
            subject = str(uuid5(DEMO_NAMESPACE, claims["name"].strip().lower()))
            if claims["sub"] != subject or claims["exp"] > self.settings.demo_expiry:
                raise ValueError()
        except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
            raise HTTPException(401, "Invalid or expired demo session") from None
        return Identity("demo", subject, self.settings.tenant_id, claims["name"],
                        f"{subject}@demo.invalid", ("User",), claims["exp"])

    async def close(self):
        pass


class DemoExpiry:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and scope["path"] == "/api/config":
            return await self.app(scope, receive, send)
        if time.time() >= self.settings.demo_expiry:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": "Demo expired"})
            elif scope["path"].startswith(("/api/", "/v3", "/demo/")):
                await JSONResponse({"detail": "Demo expired; access is disabled"}, status_code=410)(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)
