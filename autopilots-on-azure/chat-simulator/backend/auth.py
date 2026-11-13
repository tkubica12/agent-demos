import asyncio
import ssl
import time
from dataclasses import dataclass
from uuid import UUID

import httpx
import jwt
from fastapi import HTTPException

from .config import Settings


@dataclass(frozen=True)
class Identity:
    source: str
    subject: str
    tenant_id: str
    display_name: str
    upn: str
    roles: tuple[str, ...]
    expires_at: float


class TokenVerifier:
    """Only configured issuers/URLs are used; untrusted jku/x5u are never fetched."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._keys: dict[str, tuple[float, dict]] = {}
        self._last_attempt: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=5, follow_redirects=False, trust_env=False,
                                        verify=ssl.create_default_context())

    async def close(self):
        await self._client.aclose()

    async def _key(self, issuer: str, url: str, kid: str):
        async with self._lock:
            now = time.monotonic()
            cached = self._keys.get(issuer)
            if cached and now - cached[0] < 3600 and kid in cached[1]:
                return cached[1][kid]
            # Unknown kids cannot force unbounded network calls, even while keys rotate.
            if now - self._last_attempt.get(issuer, -100) < 10:
                raise HTTPException(401, "Unknown signing key")
            self._last_attempt[issuer] = now
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                if len(response.content) > 256 * 1024:
                    raise ValueError("JWKS too large")
                document = response.json()
                keys = {
                    key["kid"]: jwt.PyJWK.from_dict(key, algorithm="RS256").key
                    for key in document["keys"][:32]
                    if key.get("kty") == "RSA"
                    and key.get("use", "sig") == "sig"
                    and key.get("alg", "RS256") == "RS256"
                    and isinstance(key.get("kid"), str)
                }
            except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError):
                raise HTTPException(401, "Signing keys unavailable") from None
            self._keys[issuer] = (now, keys)
            if kid not in keys:
                raise HTTPException(401, "Unknown signing key")
            return keys[kid]

    async def verify(self, token: str) -> Identity:
        if not isinstance(token, str) or not token or len(token) > 24 * 1024:
            raise HTTPException(401, "Invalid access token")
        try:
            header = jwt.get_unverified_header(token)
            untrusted = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            raise HTTPException(401, "Malformed access token") from None
        if header.get("alg") != "RS256":
            raise HTTPException(401, "RS256 access token required")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid or len(kid) > 200:
            raise HTTPException(401, "Signing key ID required")
        if any(type(untrusted.get(field)) is not int for field in ("exp", "iat", "nbf")):
            raise HTTPException(401, "Invalid token time claims")
        issuer = untrusted.get("iss")
        source = "entra"
        url = self.settings.jwks_url
        if issuer != self.settings.issuer:
            if not self.settings.test_idp or issuer != self.settings.test_issuer:
                raise HTTPException(401, "Invalid issuer")
            source = "test-oidc"
            url = f"{self.settings.test_issuer}/jwks"
        key = await self._key(issuer, url, kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.settings.client_id,
                issuer=issuer,
                options={
                    "require": ["exp", "iat", "nbf", "iss", "aud", "tid", "oid", "sub", "azp"],
                    "strict_aud": True,
                },
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(401, "Access token expired") from None
        except jwt.InvalidAudienceError:
            raise HTTPException(401, "Invalid audience") from None
        except jwt.InvalidSignatureError:
            raise HTTPException(401, "Invalid token signature") from None
        except jwt.PyJWTError:
            raise HTTPException(401, "Invalid access token claims") from None
        if any(type(claims[field]) is not int for field in ("exp", "iat", "nbf")):
            raise HTTPException(401, "Invalid token time claims")
        if claims.get("tid") != self.settings.tenant_id:
            raise HTTPException(401, "Invalid tenant")
        if claims.get("azp") != self.settings.client_id:
            raise HTTPException(401, "Invalid authorized client")
        scopes = claims.get("scp")
        if not isinstance(scopes, str) or "access_as_user" not in scopes.split():
            raise HTTPException(401, "Delegated access_as_user scope required")
        roles = claims.get("roles")
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise HTTPException(403, "User or Admin role assignment required")
        roles = tuple(role for role in roles if role in {"User", "Admin"})
        if not roles:
            raise HTTPException(403, "User or Admin role assignment required")
        try:
            subject = str(UUID(claims["oid"]))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(401, "Invalid object identity") from None
        upn = claims.get("preferred_username") or claims.get("upn")
        name = claims.get("name")
        if not isinstance(upn, str) or not 3 <= len(upn) <= 320 or "@" not in upn:
            raise HTTPException(401, "Signed user principal name required")
        if not isinstance(name, str) or not 1 <= len(name) <= 200:
            name = upn
        return Identity(source, subject, claims["tid"], name, upn.lower(), roles, claims["exp"])
