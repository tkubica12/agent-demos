import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
DEMO_NAMESPACE = UUID("51f8c9cd-dcd4-4c33-9ef6-48b4c5919185")
TEST_PERSONAS = [
    {"id": "owner", "displayName": "Alex Novák", "upn": "alex.novak@simulator.test", "roles": ["User", "Admin"]},
    {"id": "member", "displayName": "Jana Svobodová", "upn": "jana.svobodova@simulator.test", "roles": ["User"]},
    {"id": "outsider", "displayName": "Petr Dvořák", "upn": "petr.dvorak@simulator.test", "roles": ["User"]},
    {"id": "admin", "displayName": "Simulátor Admin", "upn": "admin@simulator.test", "roles": ["User", "Admin"]},
]


@dataclass(frozen=True)
class Settings:
    auth_mode: str = "entra"
    demo_access_code: str = field(default="", repr=False)
    demo_expires_at: str = ""
    demo_acknowledge: str = ""
    agents_json: str = field(default="", repr=False)
    agents_file: str = ""
    tenant_id: str = ""
    client_id: str = ""
    scope: str = ""
    redirect_uri: str = "http://localhost:5173/"
    channel_id: str = "msteams"
    environment: str = "development"
    test_idp: bool = False
    test_issuer: str = "http://127.0.0.1:8001"
    database_url: str = field(
        default_factory=lambda: f"sqlite:///{(ROOT / 'data' / 'simulator.db').as_posix()}"
    )
    storage_path: Path = field(default_factory=lambda: ROOT / "data" / "files")
    allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
    max_file_bytes: int = 25 * 1024 * 1024
    channel_client_id: str = ""
    channel_client_secret: str = field(default="", repr=False)
    channel_managed_identity_client_id: str = ""
    service_url: str = "http://127.0.0.1:5173"

    def __post_init__(self):
        if self.auth_mode not in {"entra", "demo"}:
            raise ValueError("TSIM_AUTH_MODE must be entra or demo")
        if self.environment.lower() not in {"development", "production"}:
            raise ValueError("ENV must be development or production")
        if self.auth_mode == "demo":
            if self.test_idp or self.channel_client_secret or self.channel_managed_identity_client_id:
                raise ValueError("Demo mode must not use a test IdP or Entra channel credentials")
            if not 20 <= len(self.demo_access_code) <= 1024:
                raise ValueError("TSIM_DEMO_ACCESS_CODE must contain 20-1024 characters")
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)",
                                    self.demo_expires_at):
                    raise ValueError()
                expiry = datetime.fromisoformat(self.demo_expires_at.replace("Z", "+00:00"))
                if expiry.tzinfo is None or expiry.utcoffset() != timedelta(0) or "T" not in self.demo_expires_at:
                    raise ValueError()
            except ValueError:
                raise ValueError("TSIM_DEMO_EXPIRES_AT must be an RFC3339 UTC timestamp") from None
            now = datetime.now(timezone.utc)
            if not now < expiry <= now + timedelta(days=30):
                raise ValueError("TSIM_DEMO_EXPIRES_AT must be in the future and at most 30 days ahead")
            if self.environment.lower() == "production" and self.demo_acknowledge != "test-data-only":
                raise ValueError("Production demo requires TSIM_DEMO_ACKNOWLEDGE=test-data-only")
            if not self.tenant_id:
                object.__setattr__(self, "tenant_id", str(DEMO_NAMESPACE))
            validate_transport_url(self.service_url, self.environment, origin=True)
        if self.environment.lower() == "production" and self.test_idp:
            raise ValueError("Test IdP must not be enabled in production")
        identities = (("TSIM_TENANT_ID", self.tenant_id), ("TSIM_CLIENT_ID", self.client_id))
        for name, value in identities if self.auth_mode == "entra" else ():
            try:
                valid = UUID(value)
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"{name} must be an explicitly configured tenant/application UUID") from None
            if not valid.int or str(valid) != value:
                raise ValueError(f"{name} must be a nonzero, canonical UUID")
        if self.auth_mode == "entra" and self.scope != f"api://{self.client_id}/access_as_user":
            raise ValueError("TSIM_SCOPE must be api://<TSIM_CLIENT_ID>/access_as_user")
        validate_transport_url(self.redirect_uri, self.environment)
        if self.test_idp:
            parsed = urlparse(self.test_issuer)
            if (
                parsed.scheme != "http"
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username
                or parsed.password
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Test issuer must be an HTTP loopback origin")
        if self.environment.lower() == "production" and (
            not self.allowed_origins
            or any(not o.startswith("https://") for o in self.allowed_origins)
        ):
            raise ValueError("Production requires explicit HTTPS allowed origins")
        if self.environment.lower() == "production":
            for origin in self.allowed_origins:
                validate_transport_url(origin, self.environment, origin=True)
        if self.channel_client_id:
            UUID(self.channel_client_id)
            if self.channel_client_id == self.client_id:
                raise ValueError("Channel service must use a separate application from the public SPA")
            validate_transport_url(self.service_url, self.environment, origin=True)
        if self.channel_managed_identity_client_id:
            UUID(self.channel_managed_identity_client_id)
            if not self.channel_client_id or self.channel_client_secret:
                raise ValueError("Managed identity requires a channel client ID and no client secret")

    @property
    def issuer(self):
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self):
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @property
    def demo_expiry(self):
        return datetime.fromisoformat(self.demo_expires_at.replace("Z", "+00:00")).timestamp()

    @classmethod
    def from_env(cls):
        # This file contains public SPA configuration, never credentials.
        path = ROOT / "frontend" / "public" / "auth-config.json"
        public = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        values = {
            "auth_mode": os.getenv("TSIM_AUTH_MODE", "entra"),
            "demo_access_code": os.getenv("TSIM_DEMO_ACCESS_CODE", ""),
            "demo_expires_at": os.getenv("TSIM_DEMO_EXPIRES_AT", ""),
            "demo_acknowledge": os.getenv("TSIM_DEMO_ACKNOWLEDGE", ""),
            "agents_json": os.getenv("TSIM_AGENTS_JSON", ""),
            "agents_file": os.getenv("TSIM_AGENTS_FILE", ""),
            "tenant_id": os.getenv("TSIM_TENANT_ID", public.get("tenantId", cls.tenant_id)),
            "client_id": os.getenv("TSIM_CLIENT_ID", public.get("clientId", cls.client_id)),
            "scope": os.getenv("TSIM_SCOPE", public.get("scope", cls.scope)),
            "redirect_uri": os.getenv("TSIM_REDIRECT_URI", public.get("redirectUri", cls.redirect_uri)),
            "channel_id": os.getenv("TSIM_CHANNEL_ID", "msteams"),
            "environment": os.getenv("ENV", "development"),
            "test_idp": os.getenv("TSIM_TEST_IDP") == "1",
            "test_issuer": os.getenv("TSIM_TEST_ISSUER", "http://127.0.0.1:8001").rstrip("/"),
            "channel_client_id": os.getenv("TSIM_CHANNEL_CLIENT_ID", ""),
            "channel_client_secret": os.getenv("TSIM_CHANNEL_CLIENT_SECRET", ""),
            "channel_managed_identity_client_id": os.getenv("TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID", ""),
            "service_url": os.getenv("TSIM_SERVICE_URL", cls.service_url).rstrip("/"),
        }
        if os.getenv("TSIM_DATABASE_URL"):
            values["database_url"] = os.environ["TSIM_DATABASE_URL"]
        if os.getenv("TSIM_STORAGE_PATH"):
            values["storage_path"] = Path(os.environ["TSIM_STORAGE_PATH"]).resolve()
        if os.getenv("TSIM_ALLOWED_ORIGINS"):
            values["allowed_origins"] = tuple(
                o.strip().rstrip("/") for o in os.environ["TSIM_ALLOWED_ORIGINS"].split(",") if o.strip()
            )
        elif values["environment"].lower() == "production":
            values["allowed_origins"] = ()
        return cls(**values)


def validate_transport_url(value: str, environment: str, origin=False):
    """An admin registration is the endpoint allowlist; redirects never extend it."""
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid channel transport URL") from None
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
        or "\\" in value or any(ord(c) <= 32 for c in value)
        or (port is not None and not 1 <= port <= 65535)
        or (origin and parsed.path not in {"", "/"})
        or (parsed.scheme != "https" and not (
            environment.lower() == "development" and loopback and parsed.scheme == "http"
        ))
        or (environment.lower() == "production" and loopback)
    ):
        raise ValueError("Channel transport requires HTTPS, or controlled development HTTP loopback")
    return value.rstrip("/") if origin else value
