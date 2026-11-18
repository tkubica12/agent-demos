from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import jwt
import requests
from fastapi import HTTPException, Request
from microsoft_agents.activity import Activity, ResourceResponse, load_configuration_from_env
from microsoft_agents.hosting.core import (
    AgentAuthConfiguration,
    ClaimsIdentity,
    JwtTokenValidator,
    RestChannelServiceClientFactory,
    TurnContext,
)
from microsoft_agents.hosting.core.connector.teams import TeamsConnectorClient
from microsoft_agents.hosting.fastapi import CloudAdapter
from msal import ConfidentialClientApplication
from pydantic import ValidationError

from bridge.simulator_ingress import SimulatorChannel


SERVICE_SETTINGS = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__"
logger = logging.getLogger(__name__)


def agent365_auth_configured() -> bool:
    return os.getenv("USE_AGENTIC_AUTH", "").strip().lower() in {"1", "true", "yes"}


def service_client_id() -> str:
    return os.getenv(f"{SERVICE_SETTINGS}CLIENTID", "").strip()


def tenant_id_from_environment() -> str:
    return (
        os.getenv("AGENT365_TENANT_ID", "").strip()
        or os.getenv("TENANT_ID", "").strip()
        or os.getenv(f"{SERVICE_SETTINGS}TENANTID", "").strip()
    )


def simulator_channel_configured() -> bool:
    return not agent365_auth_configured() and bool(
        service_client_id() or os.getenv("DEMO_CHANNEL_KEY", "").strip()
    )


@dataclass(frozen=True)
class ChannelAuthentication:
    mode: str
    demo_key: str = field(default="", repr=False)
    demo_service_url: str = field(default="", repr=False)
    configuration: dict[str, Any] = field(default_factory=dict, repr=False)
    validator: JwtTokenValidator | None = field(default=None, repr=False)
    channel_client_id: str = ""
    simulator: SimulatorChannel | None = None

    @classmethod
    def from_environment(cls) -> "ChannelAuthentication":
        agentic = agent365_auth_configured()
        client_id = service_client_id()
        key = os.getenv("DEMO_CHANNEL_KEY", "")
        service_url = os.getenv("DEMO_SERVICE_URL", "")
        if key or service_url:
            if agentic or client_id:
                raise ValueError("DEMO channel cannot be combined with Agent 365 or a service-connection CLIENTID.")
            if len(key) < 32 or not key.isascii() or any(not (c.isalnum() or c in "-_") for c in key):
                raise ValueError("DEMO_CHANNEL_KEY must contain at least 32 URL-safe ASCII characters.")
            parsed = urlparse(service_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or "\\" in service_url
                or any(ord(c) <= 32 for c in service_url)
                or parsed.path != f"/demo/{key}"
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)
            ):
                raise ValueError("DEMO_SERVICE_URL must be the exact HTTPS URL https://<simulator>/demo/<DEMO_CHANNEL_KEY>.")
            logger.warning("DEMO CHANNEL ENABLED: no Entra authentication or agentic tools; TEST DATA ONLY.")
            return cls("demo", demo_key=key, demo_service_url=service_url)
        if agentic:
            return cls(
                "agent365",
                configuration=load_configuration_from_env(os.environ),
                simulator=SimulatorChannel.from_environment(),
            )
        if not client_id:
            return cls("anonymous")
        configuration = load_configuration_from_env(os.environ)
        settings = configuration["CONNECTIONS"]["SERVICE_CONNECTION"]["SETTINGS"]
        settings["CLIENTID"] = str(uuid.UUID(client_id))
        tenant = str(uuid.UUID(settings.get("TENANTID") or tenant_id_from_environment()))
        settings["TENANTID"] = tenant
        scopes = settings.get("SCOPES", {})
        scopes = list(scopes.values()) if isinstance(scopes, dict) else list(scopes)
        if len(scopes) != 1 or not scopes[0].endswith("/.default"):
            raise ValueError("Standard channel requires one service-connection scope: <channel app ID>/.default.")
        channel_id = str(uuid.UUID(scopes[0].removesuffix("/.default").removeprefix("api://")))
        settings["SCOPES"] = scopes
        if not configuration.get("CONNECTIONSMAP"):
            raise ValueError("Standard channel requires an explicit CONNECTIONSMAP service URL mapping.")
        return cls(
            "standard",
            configuration=configuration,
            validator=JwtTokenValidator(AgentAuthConfiguration(**settings)),
            channel_client_id=channel_id,
        )

    async def authenticate(self, request: Request, body: bytes, connection_manager: Any = None) -> None:
        if self.mode == "agent365" and self.simulator:
            self._authenticate_simulator(request, body)
            return
        if self.mode not in {"standard", "demo"}:
            return
        if self.mode == "demo":
            supplied = request.headers.get("x-demo-channel-key", "")
            if not secrets.compare_digest(supplied.encode("utf-8"), self.demo_key.encode("utf-8")):
                raise HTTPException(401, "A valid X-Demo-Channel-Key header is required.")
            identity = ClaimsIdentity({}, False, authentication_type="Anonymous")
        else:
            if self.validator is None:
                raise RuntimeError("Standard channel token validator is not configured.")
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not token or len(token) > 24 * 1024:
                raise HTTPException(401, "A channel application bearer token is required.")
            try:
                identity = await self.validator.validate_token(token)
            except jwt.PyJWKClientConnectionError as exc:
                raise HTTPException(503, "Entra signing keys are unavailable.") from exc
            except (jwt.PyJWTError, ValueError, KeyError, TypeError, AttributeError) as exc:
                raise HTTPException(401, "Invalid channel application token.") from exc
            claims = identity.claims
            tenant = self.validator.configuration.TENANT_ID
            if (
                claims.get("iss") != f"https://login.microsoftonline.com/{tenant}/v2.0"
                or claims.get("tid") != tenant
                or claims.get("azp") != self.channel_client_id
                or claims.get("ver") != "2.0"
                or claims.get("scp") is not None
                or claims.get("idtyp", "app") != "app"
                or any(type(claims.get(name)) is not int for name in ("iat", "nbf", "exp"))
                or claims["exp"] <= time.time()
            ):
                raise HTTPException(401, "Invalid channel issuer, tenant, caller or application claims.")
        try:
            activity = Activity.model_validate(json.loads(body))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(400, "A valid channel activity is required.") from exc
        if activity.is_agentic_request():
            raise HTTPException(403, "Agentic activities require Agent 365 mode.")
        if self.mode == "demo":
            if activity.service_url != self.demo_service_url:
                raise HTTPException(403, "Activity serviceUrl does not match DEMO_SERVICE_URL.")
        else:
            try:
                connection_manager.get_token_provider(identity, activity.service_url)
            except ValueError as exc:
                raise HTTPException(403, "Activity serviceUrl is not mapped to a configured connection.") from exc
        request.state.claims_identity = identity

    def _authenticate_simulator(self, request: Request, body: bytes) -> None:
        """Agent 365 traffic passes unchanged; activities for the simulator need its channel token."""
        try:
            activity = Activity.model_validate(json.loads(body))
        except (ValueError, UnicodeDecodeError, ValidationError) as exc:
            raise HTTPException(400, "A valid channel activity is required.") from exc
        if not self.simulator.owns(activity.service_url):
            return
        if activity.is_agentic_request():
            raise HTTPException(403, "Simulator activities cannot be agentic requests.")
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 24 * 1024:
            raise HTTPException(401, "A simulator channel bearer token is required.")
        try:
            claims = self.simulator.verify(token)
        except jwt.PyJWKClientConnectionError as exc:
            raise HTTPException(503, "Entra signing keys are unavailable.") from exc
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from exc
        request.state.claims_identity = ClaimsIdentity(claims, True, authentication_type="Bearer")


class Agent365SimulatorClientFactory(RestChannelServiceClientFactory):
    """Replies to the simulator use an Agent Identity token for the simulator channel app."""

    def __init__(self, connection_manager: Any, simulator: SimulatorChannel) -> None:
        super().__init__(connection_manager)
        self.simulator = simulator
        self._reply_token = ""
        self._reply_token_expires = 0.0

    async def _agent_identity_token(self) -> str:
        if self._reply_token and self._reply_token_expires > time.time() + 300:
            return self._reply_token
        for attempt in range(3):
            try:
                return await self._acquire_agent_identity_token()
            except requests.exceptions.ConnectionError:
                # Connections to Entra get reset after the gateway sat idle; the next attempt opens a new one.
                if attempt == 2:
                    raise

    async def _acquire_agent_identity_token(self) -> str:
        blueprint = self._connection_manager.get_default_connection()
        assertion = await blueprint.get_agentic_application_token(
            self.simulator.tenant_id, self.simulator.agent_identity_client_id,
        )
        if not assertion:
            raise RuntimeError("The blueprint could not get an Agent Identity exchange token.")
        application = ConfidentialClientApplication(
            self.simulator.agent_identity_client_id,
            authority=f"https://login.microsoftonline.com/{self.simulator.tenant_id}",
            client_credential={"client_assertion": assertion},
        )
        result = await asyncio.to_thread(application.acquire_token_for_client, self.simulator.reply_scopes)
        if "access_token" not in result:
            raise RuntimeError(f"Agent Identity reply token failed: {result.get('error')}")
        self._reply_token = result["access_token"]
        self._reply_token_expires = time.time() + int(result.get("expires_in", 0))
        return self._reply_token

    def _is_simulator(self, context) -> bool:
        activity = getattr(context, "activity", None)
        return bool(activity) and not activity.is_agentic_request() and self.simulator.owns(activity.service_url)

    async def create_user_token_client(self, context, claims_identity, use_anonymous=False):
        return await super().create_user_token_client(
            context, claims_identity, use_anonymous=use_anonymous or self._is_simulator(context),
        )

    async def create_connector_client(
        self, context, claims_identity, service_url, audience, scopes=None, use_anonymous=False,
    ):
        if self.simulator.owns(service_url) and not (context and context.activity.is_agentic_request()):
            return TeamsConnectorClient(endpoint=service_url, token=await self._agent_identity_token())
        return await super().create_connector_client(
            context, claims_identity, service_url, audience, scopes, use_anonymous=use_anonymous,
        )


class ChannelServiceClientFactory(RestChannelServiceClientFactory):
    """Standard channels have no Bot Framework user-token/OBO service."""

    def __init__(self, connection_manager: Any, *, demo_service_url: str = "") -> None:
        super().__init__(connection_manager)
        self.demo_service_url = demo_service_url

    async def create_user_token_client(self, context, claims_identity, use_anonymous=False):
        return await super().create_user_token_client(context, claims_identity, use_anonymous=True)

    async def create_connector_client(
        self, context, claims_identity, service_url, audience, scopes=None, use_anonymous=False,
    ):
        if self.demo_service_url and service_url != self.demo_service_url:
            raise PermissionError("Proactive serviceUrl does not match DEMO_SERVICE_URL.")
        if not self.demo_service_url:
            scopes = self._connection_manager.get_default_connection_configuration().SCOPES
        return await super().create_connector_client(
            context, claims_identity, service_url, audience, scopes,
            use_anonymous=bool(self.demo_service_url) or use_anonymous,
        )


class ChannelCloudAdapter(CloudAdapter):
    async def update_activity(self, context: TurnContext, activity: Activity) -> ResourceResponse:
        if not activity.id or not activity.conversation or not activity.conversation.id:
            raise ValueError("Activity update requires conversation and activity IDs.")
        connector = context.turn_state[self._AGENT_CONNECTOR_CLIENT_KEY]
        conversation_id = quote(activity.conversation.id, safe="")
        activity_id = quote(activity.id, safe="")
        async with connector.client.put(
            f"v3/conversations/{conversation_id}/activities/{activity_id}",
            json=activity.model_dump(by_alias=True, exclude_none=True, exclude_unset=True, mode="json"),
        ) as response:
            response.raise_for_status()
            return ResourceResponse.model_validate(await response.json())
