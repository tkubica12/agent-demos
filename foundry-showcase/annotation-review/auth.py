"""Entra sign-in for the reviewer app.

The app is a public client using authorization code + PKCE, so it needs no client secret.
Tokens for Log Analytics and Azure Monitor are acquired for the signed-in reviewer, which
means Azure RBAC is enforced per business user rather than through an app identity.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import msal

logger = logging.getLogger(__name__)

READ_SCOPES = ["https://api.loganalytics.io/Data.Read"]
INGEST_SCOPES = ["https://monitor.azure.com/AMA.Ingest"]

# Entra issues an access token for a single resource per authorization request, so the
# reviewer consents to Log Analytics and Azure Monitor in two consecutive legs. Only the
# first leg shows a sign-in prompt; the second is a silent single sign-on redirect once
# the reviewer has consented.
LOGIN_LEGS = [READ_SCOPES, INGEST_SCOPES]

SESSION_COOKIE = "annotation_review_session"
SESSION_TTL_SECONDS = 8 * 60 * 60


class AuthError(RuntimeError):
    """Raised when sign-in or token acquisition fails."""


@dataclass
class Session:
    """Server-side state for one signed-in reviewer."""

    session_id: str
    cache: msal.SerializableTokenCache
    created_at: float
    username: str | None = None
    name: str | None = None
    flow: dict[str, Any] | None = None
    pending_legs: list[list[str]] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SECONDS


class SessionStore:
    """In-memory session store. The app is a local, single-operator demo tool."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        session = Session(
            session_id=secrets.token_urlsafe(32),
            cache=msal.SerializableTokenCache(),
            created_at=time.time(),
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.expired:
            self._sessions.pop(session_id, None)
            return None
        return session

    def drop(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)


class Authenticator:
    """Wraps MSAL so routes can stay small."""

    def __init__(self, tenant_id: str, client_id: str, redirect_uri: str) -> None:
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.authority = f"https://login.microsoftonline.com/{tenant_id}"

    def _app(self, session: Session) -> msal.PublicClientApplication:
        return msal.PublicClientApplication(
            self.client_id, authority=self.authority, token_cache=session.cache
        )

    def start_login(self, session: Session) -> str:
        session.pending_legs = [list(scopes) for scopes in LOGIN_LEGS]
        return self._start_leg(session)

    def _start_leg(self, session: Session) -> str:
        scopes = session.pending_legs[0]
        flow = self._app(session).initiate_auth_code_flow(
            scopes=scopes, redirect_uri=self.redirect_uri
        )
        if "auth_uri" not in flow:
            raise AuthError(f"Could not start sign-in: {flow}")
        session.flow = flow
        return flow["auth_uri"]

    def complete_login(self, session: Session, query: dict[str, Any]) -> str | None:
        """Complete the current consent leg. Returns the next redirect, or None when done."""
        if not session.flow:
            raise AuthError("No sign-in is in progress for this session.")
        result = self._app(session).acquire_token_by_auth_code_flow(
            session.flow, query
        )
        session.flow = None
        if "access_token" not in result:
            raise AuthError(
                result.get("error_description")
                or result.get("error")
                or "Sign-in failed."
            )
        claims = result.get("id_token_claims") or {}
        session.username = (
            claims.get("preferred_username") or claims.get("upn") or session.username
        )
        session.name = claims.get("name") or session.name

        if session.pending_legs:
            session.pending_legs.pop(0)
        if session.pending_legs:
            return self._start_leg(session)
        return None

    def token_for(self, session: Session, scopes: list[str]) -> str:
        app = self._app(session)
        accounts = app.get_accounts()
        if not accounts:
            raise AuthError("Signed-out session. Sign in again.")
        result = app.acquire_token_silent_with_error(scopes, account=accounts[0])
        if not result or "access_token" not in result:
            logger.warning("Silent token request for %s returned %s", scopes, result)
            detail = (result or {}).get("error_description") or "No cached grant found."
            raise AuthError(
                f"Could not get a token for {scopes[0]}. "
                f"Sign out and sign in again to grant consent. {detail}"[:400]
            )
        return result["access_token"]

    def read_token(self, session: Session) -> str:
        return self.token_for(session, READ_SCOPES)

    def ingest_token(self, session: Session) -> str:
        return self.token_for(session, INGEST_SCOPES)
