import asyncio
import copy
import hashlib
import hmac
import json
import ssl
import secrets
import time
from uuid import UUID

import httpx
import jwt
from azure.core.exceptions import AzureError
from azure.identity import ClientAssertionCredential, ManagedIdentityCredential
from fastapi import HTTPException
from sqlalchemy import delete, select

from . import db, domain as d
from .config import validate_transport_url


ADAPTIVE_CARD = "application/vnd.microsoft.card.adaptive"
FILE_DOWNLOAD = "application/vnd.microsoft.teams.file.download.info"
REACTIONS = {
    "like": "👍", "heart": "❤️", "laugh": "😆", "surprised": "😮",
    "sad": "😢", "angry": "😠", "eyes": "👀", "checkmark": "✅",
}


async def bounded_json(request, limit=1024 * 1024):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise HTTPException(413, "Activity payload exceeds the channel limit")
        raw.extend(chunk)
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "A JSON object is required") from None
    if not isinstance(value, dict):
        raise HTTPException(422, "A JSON object is required")
    return value


def account(user, session=None):
    value = {"id": user.id, "name": user.display_name}
    if user.source == "agent" and session is not None:
        agent = session.scalar(select(db.Agent).where(db.Agent.user_id == user.id))
        value["id"] = f"28:{agent.client_id}"
    if user.source != "agent" and user.subject:
        value["aadObjectId"] = user.subject
    return value


def agent_json(session, agent):
    return {"id": agent.id, "userId": agent.user_id,
            "name": session.get(db.User, agent.user_id).display_name,
            "clientId": agent.client_id, "endpointUrl": agent.endpoint_url, "enabled": agent.enabled}


def require_agent(session, agent_id, enabled=False):
    agent = session.get(db.Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    if enabled and not agent.enabled:
        raise HTTPException(403, "Agent is disabled")
    return agent


def resolve_conversation(session, conversation_id, user_id):
    base, separator, root_id = conversation_id.partition(";messageid=")
    conversation = d.require_conversation(session, base, user_id)
    if separator:
        root = d.require_message(session, root_id, user_id)
        if conversation.kind != "channel" or root.conversation_id != base or root.reply_to_id:
            raise HTTPException(422, "Invalid channel thread identity")
    return conversation, root_id or None


def delivery_json(delivery):
    return {"id": delivery.id, "agentId": delivery.agent_id,
            "conversationId": delivery.conversation_id, "activityId": delivery.activity["id"],
            "activityType": delivery.activity["type"], "status": delivery.status,
            "attempts": delivery.attempts, "error": delivery.error, "createdAt": delivery.created_at}


class ChannelAuth:
    """Application tokens have a different audience/caller policy from delegated user tokens."""

    def __init__(self, settings, verifier):
        self.settings = settings
        self.verifier = verifier
        self.client = httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False,
                                       verify=ssl.create_default_context())
        self._tokens = {}
        self._lock = asyncio.Lock()
        self._managed_identity = None
        self._federated = None
        if settings.channel_managed_identity_client_id:
            identity = ManagedIdentityCredential(
                client_id=settings.channel_managed_identity_client_id,
                connection_timeout=10, read_timeout=10, retry_total=1,
            )
            self._managed_identity = identity
            self._federated = ClientAssertionCredential(
                settings.tenant_id, settings.channel_client_id,
                lambda: identity.get_token("api://AzureADTokenExchange/.default").token,
                connection_timeout=10, read_timeout=10, retry_total=1,
            )

    async def close(self):
        self._tokens.clear()
        await self.client.aclose()
        if self._federated is not None:
            await asyncio.to_thread(self._federated.close)
        if self._managed_identity is not None:
            await asyncio.to_thread(self._managed_identity.close)

    async def verify(self, token, audience, caller=None):
        if not audience:
            raise HTTPException(503, "TSIM_CHANNEL_CLIENT_ID is not configured")
        if not isinstance(token, str) or not token or len(token) > 24 * 1024:
            raise HTTPException(401, "Invalid channel access token")
        try:
            header = jwt.get_unverified_header(token)
            untrusted = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            raise HTTPException(401, "Malformed channel access token") from None
        if header.get("alg") != "RS256":
            raise HTTPException(401, "RS256 channel access token required")
        kid = header.get("kid")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 200:
            raise HTTPException(401, "Signing key ID required")
        if any(type(untrusted.get(field)) is not int for field in ("exp", "iat", "nbf")):
            raise HTTPException(401, "Invalid token time claims")
        issuer = untrusted.get("iss")
        url = self.settings.jwks_url
        if issuer != self.settings.issuer:
            if not self.settings.test_idp or issuer != self.settings.test_issuer:
                raise HTTPException(401, "Invalid channel issuer")
            url = f"{self.settings.test_issuer}/jwks"
        key = await self.verifier._key(issuer, url, kid)
        try:
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=issuer,
                                options={"require": ["exp", "iat", "nbf", "iss", "aud", "tid", "azp"],
                                         "strict_aud": True})
        except jwt.PyJWTError:
            raise HTTPException(401, "Invalid channel signature, expiry or audience") from None
        if claims["tid"] != self.settings.tenant_id:
            raise HTTPException(401, "Invalid channel tenant")
        if claims.get("scp") is not None or claims.get("idtyp", "app") != "app":
            raise HTTPException(401, "Client-credentials application token required")
        try:
            if str(UUID(claims["azp"])) != claims["azp"]:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(401, "Invalid channel caller") from None
        if caller is not None and claims["azp"] != caller:
            raise HTTPException(401, "Invalid channel caller")
        return claims

    async def token(self, agent_client_id):
        if not self.settings.channel_client_id or (
            not self.settings.channel_client_secret and self._federated is None
        ):
            raise HTTPException(503, "A channel client ID and a client secret or managed identity are required for delivery")
        async with self._lock:
            cached = self._tokens.get(agent_client_id)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
            url = f"https://login.microsoftonline.com/{self.settings.tenant_id}/oauth2/v2.0/token"
            try:
                if self._federated is not None:
                    acquired = await asyncio.to_thread(self._federated.get_token, f"{agent_client_id}/.default")
                    token = acquired.token
                else:
                    async with self.client.stream("POST", url, data={
                        "grant_type": "client_credentials",
                        "client_id": self.settings.channel_client_id,
                        "client_secret": self.settings.channel_client_secret,
                        "scope": f"{agent_client_id}/.default",
                    }) as response:
                        if response.status_code != 200:
                            raise HTTPException(502, f"Channel token acquisition failed (HTTP {response.status_code})")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 64 * 1024:
                                raise HTTPException(502, "Channel token response is too large")
                            raw.extend(chunk)
                    document = json.loads(raw)
                    token = document["access_token"]
                    if document.get("token_type", "").lower() != "bearer":
                        raise ValueError()
                claims = await self.verify(token, agent_client_id, self.settings.channel_client_id)
            except HTTPException as error:
                if error.status_code == 401:
                    raise HTTPException(502, "Channel token response failed application identity validation") from None
                raise
            except (AzureError, httpx.HTTPError, ValueError, KeyError, TypeError):
                raise HTTPException(502, "Channel token acquisition failed") from None
            self._tokens[agent_client_id] = (token, claims["exp"])
            return token


class AgentChannel:
    def __init__(self, settings, sessions, verifier, hub, storage):
        self.settings = settings
        self.sessions = sessions
        self.auth = ChannelAuth(settings, verifier)
        self.hub = hub
        self.storage = storage
        self.client = httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False,
                                       verify=ssl.create_default_context())

    async def close(self):
        await self.auth.close()
        await self.client.aclose()

    def agents(self, session, conversation):
        members = d.conversation_users(session, conversation)
        return list(session.scalars(select(db.Agent).where(db.Agent.enabled.is_(True),
                                                          db.Agent.user_id.in_(members))))

    def require_active_demo(self):
        if self.settings.auth_mode == "demo" and time.time() >= self.settings.demo_expiry:
            raise HTTPException(410, "Demo expired; access is disabled")

    def service_url(self, agent):
        if self.settings.auth_mode == "demo":
            if not agent.demo_key:
                raise HTTPException(503, "Demo agent has no configured demoKey")
            return f"{self.settings.service_url}/demo/{agent.demo_key}"
        return self.settings.service_url

    def demo_agent(self, session, key):
        self.require_active_demo()
        if self.settings.auth_mode != "demo" or not isinstance(key, str) or not 43 <= len(key) <= 128:
            raise HTTPException(401, "Invalid demo channel key")
        matched = None
        for agent in session.scalars(select(db.Agent).where(db.Agent.enabled.is_(True))):
            if agent.demo_key and hmac.compare_digest(agent.demo_key.encode(), key.encode()):
                matched = agent
        if matched is None:
            raise HTTPException(401, "Invalid demo channel key")
        return matched

    def envelope(self, session, conversation, author, agent, activity):
        value = copy.deepcopy(activity)
        value["serviceUrl"] = self.service_url(agent)
        value["channelId"] = self.settings.channel_id
        value["locale"] = value.get("locale", "en-US")
        value["from"] = account(author, session)
        value["recipient"] = account(session.get(db.User, agent.user_id), session)
        # This flag means targeted-private delivery, not personal addressing or an @mention.
        value["recipient"]["isTargeted"] = (
            activity.get("recipient", {}).get("id") == f"28:{agent.client_id}"
            and activity.get("recipient", {}).get("isTargeted") is True
        )
        for entity in value.get("entities", []):
            if entity.get("type") == "mention":
                mentioned = session.get(db.User, entity.get("mentioned", {}).get("id"))
                if mentioned is not None and mentioned.source == "agent":
                    entity["mentioned"] = account(mentioned, session)
        value["conversation"]["isGroup"] = conversation.kind != "personal"
        # Roots establish a Teams channel thread too, not only subsequent replies.
        if conversation.kind == "channel" and ";messageid=" not in value["conversation"]["id"]:
            if value["type"] in {"message", "invoke", "messageReaction"}:
                value["conversation"]["id"] += f";messageid={value.get('replyToId') or value['id']}"
        return d.validated_activity(value)

    def queue(self, session, conversation, author, activity, agents=None):
        deliveries = []
        for agent in self.agents(session, conversation) if agents is None else agents:
            delivery = db.AgentDelivery(
                agent_id=agent.id, conversation_id=conversation.id, created_at=d.now(),
                activity=self.envelope(session, conversation, author, agent, activity),
            )
            session.add(delivery)
            deliveries.append(delivery)
        session.commit()
        return [delivery.id for delivery in deliveries]

    def file_capabilities(self, session, agent, activity):
        session.execute(delete(db.AgentFileCapability).where(db.AgentFileCapability.expires_at <= int(time.time())))
        value = copy.deepcopy(activity)
        for attachment in value.get("attachments", []):
            if attachment.get("contentType") != FILE_DOWNLOAD:
                continue
            file_id = attachment.get("content", {}).get("uniqueId")
            file = d.require_file(session, file_id, agent.user_id)
            if file.conversation_id != activity["conversation"]["id"].split(";messageid=")[0]:
                raise HTTPException(403, "Attachment belongs to another conversation")
            token = secrets.token_urlsafe(32)
            session.add(db.AgentFileCapability(
                digest=hashlib.sha256(token.encode()).hexdigest(), file_id=file.id,
                agent_id=agent.id, expires_at=int(time.time()) + 900,
            ))
            attachment["content"]["downloadUrl"] = (
                f"{self.settings.service_url}/api/agent-files/{file.id}/content?capability={token}"
            )
        session.commit()
        return value

    async def deliver(self, session, delivery_id):
        self.require_active_demo()
        delivery = session.get(db.AgentDelivery, delivery_id)
        if delivery.status == "delivered":
            raise HTTPException(409, "Activity was already delivered")
        delivery.attempts += 1
        delivery.status = "pending"
        delivery.error = None
        session.commit()
        try:
            agent = require_agent(session, delivery.agent_id, enabled=True)
            self.authorize_delivery(session, delivery, agent)
            endpoint = validate_transport_url(agent.endpoint_url, self.settings.environment)
            client_id, agent_id = agent.client_id, agent.id
            activity = self.file_capabilities(session, agent, delivery.activity)
            session.rollback()  # Agent handlers can call Connector without a held SQLite transaction.
            if self.settings.auth_mode == "demo":
                headers = {"X-Demo-Channel-Key": agent.demo_key}
                if not agent.demo_key:
                    raise HTTPException(503, "Demo agent has no configured demoKey")
            else:
                token = await self.auth.token(client_id)
                headers = {"Authorization": f"Bearer {token}"}
            agent = require_agent(session, agent_id, enabled=True)
            if agent.endpoint_url != endpoint:
                raise HTTPException(409, "Agent endpoint changed while acquiring its channel token; retry delivery")
            self.authorize_delivery(session, delivery, agent)
            self.require_active_demo()
            session.rollback()
            async with self.client.stream("POST", endpoint, json=activity,
                                          headers=headers) as response:
                if response.status_code not in {200, 201, 202, 204}:
                    raise HTTPException(502, f"Agent delivery failed (HTTP {response.status_code})")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > 1024 * 1024:
                        raise HTTPException(502, "Agent response is too large")
                    raw.extend(chunk)
            invoke_response = None
            if activity["type"] == "invoke":
                try:
                    invoke_response = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise HTTPException(502, "Agent did not return a synchronous invoke response") from None
                if not isinstance(invoke_response, dict) or type(invoke_response.get("statusCode")) is not int:
                    raise HTTPException(502, "Invalid adaptiveCard/action invoke response")
                status = invoke_response["statusCode"]
                if status != 200:
                    raise HTTPException(502, f"Agent action failed (statusCode {status})")
                if invoke_response.get("type") == ADAPTIVE_CARD:
                    card = invoke_response.get("value")
                    if not isinstance(card, dict) or card.get("type") != "AdaptiveCard":
                        raise HTTPException(502, "Invalid Adaptive Card invoke response")
                elif invoke_response.get("type") != "application/vnd.microsoft.activity.message" or not isinstance(
                    invoke_response.get("value"), str
                ):
                    raise HTTPException(502, "Unsupported adaptiveCard/action response type")
            delivery = session.get(db.AgentDelivery, delivery_id)
            delivery.status = "delivered"
            session.commit()
            return invoke_response
        except (HTTPException, httpx.HTTPError, ValueError) as error:
            session.rollback()
            delivery = session.get(db.AgentDelivery, delivery_id)
            delivery.status = "failed"
            delivery.error = error.detail if isinstance(error, HTTPException) else (
                "Agent transport failed" if isinstance(error, httpx.HTTPError) else "Invalid agent endpoint configuration"
            )
            session.commit()
            raise HTTPException(503 if isinstance(error, HTTPException) and error.status_code == 503 else 502,
                                {"code": "agent_delivery_failed", "delivery": delivery_json(delivery)}) from None

    def authorize_delivery(self, session, delivery, agent):
        conversation = session.get(db.Conversation, delivery.conversation_id)
        if conversation is None:
            raise HTTPException(404, "Conversation not found")
        if agent.user_id in d.conversation_users(session, conversation):
            return
        # A removed agent gets its final uninstall notice, never renewed Connector/file access.
        activity = delivery.activity
        if activity["type"] == "conversationUpdate" and any(
            member.get("id") == f"28:{agent.client_id}" for member in activity.get("membersRemoved", [])
        ) and activity.get("recipient", {}).get("id") == f"28:{agent.client_id}":
            return
        raise HTTPException(403, "Conversation membership required")

    async def flush(self, session, ids, resource_id=None):
        failures = []
        unavailable = False
        for delivery_id in ids:
            try:
                await self.deliver(session, delivery_id)
            except HTTPException as error:
                failures.append(error.detail)
                unavailable |= error.status_code == 503
        if failures:
            raise HTTPException(503 if unavailable else 502, {
                "code": "agent_delivery_failed", "resourceId": resource_id, "failures": failures,
                "resourceCommitted": True,
            })

    async def message(self, session, conversation, author, activity):
        if author.source == "agent":
            return
        ids = self.queue(session, conversation, author, activity)
        await self.flush(session, ids, activity["id"])

    async def installation(self, session, conversation, author, members, removed=False, removed_agents=(),
                           resource_id=None):
        agents = self.agents(session, conversation)
        if removed:
            agents += [a for a in removed_agents if a.enabled and a.id not in {existing.id for existing in agents}]
        if not agents:
            return
        activity = d.make_activity(session, self.settings, conversation, author, db.new_id(), "", "plain")
        activity["type"] = "conversationUpdate"
        activity.pop("text", None)
        activity["membersRemoved" if removed else "membersAdded"] = [account(u, session) for u in members]
        if conversation.team_id:
            activity["channelData"]["eventType"] = "teamMemberRemoved" if removed else "teamMemberAdded"
        ids = self.queue(session, conversation, author, activity, agents)
        await self.flush(session, ids, resource_id or conversation.id)

    async def reaction(self, session, message, author, emoji, removed):
        if author.source == "agent":
            return
        agent = session.scalar(select(db.Agent).where(db.Agent.user_id == message.author_id,
                                                     db.Agent.enabled.is_(True)))
        if not agent:
            return
        conversation = session.get(db.Conversation, message.conversation_id)
        if agent.user_id not in d.conversation_users(session, conversation):
            return
        activity = d.change_activity(message, "messageReaction", author, emoji, removed)
        reaction_type = next((name for name, value in REACTIONS.items() if value == emoji), emoji)
        activity["reactionsRemoved" if removed else "reactionsAdded"] = [{"type": reaction_type}]
        # The reaction targets the message ID while the conversation targets its root thread.
        activity["conversation"]["id"] = conversation.id
        if conversation.kind == "channel":
            activity["conversation"]["id"] += f";messageid={message.reply_to_id or message.id}"
        ids = self.queue(session, conversation, author, activity, [agent])
        await self.flush(session, ids, message.id)
