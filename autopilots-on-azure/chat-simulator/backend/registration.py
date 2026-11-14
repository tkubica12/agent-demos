import base64
import json
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from . import db
from .config import validate_transport_url


def valid_demo_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value):
        return False
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    return len(decoded) >= 32 and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == value


def registrations(settings):
    if settings.agents_json and settings.agents_file:
        raise ValueError("Set only one of TSIM_AGENTS_JSON or TSIM_AGENTS_FILE")
    raw = settings.agents_json
    if settings.agents_file:
        raw = Path(settings.agents_file).read_text(encoding="utf-8")
    if not raw and not settings.agents_file:
        return []
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError()
        names, clients, keys = set(), set(), set()
        for value in values:
            if not isinstance(value, dict) or set(value) - {
                "name", "displayName", "clientId", "messagingEndpoint", "demoKey",
            } or not {"name", "displayName", "clientId", "messagingEndpoint"} <= set(value):
                raise ValueError()
            if not isinstance(value["name"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", value["name"]):
                raise ValueError()
            display = value["displayName"]
            if not isinstance(display, str) or not 1 <= len(display) <= 200 or display != display.strip() or any(
                ord(char) < 32 for char in display
            ):
                raise ValueError()
            client = str(UUID(value["clientId"]))
            if not UUID(client).int or client != value["clientId"] or client in {
                settings.client_id, settings.channel_client_id,
            }:
                raise ValueError()
            validate_transport_url(value["messagingEndpoint"], settings.environment)
            key = value.get("demoKey")
            if settings.auth_mode == "demo":
                if not valid_demo_key(key) or key in keys:
                    raise ValueError()
                keys.add(key)
            elif "demoKey" in value:
                raise ValueError()
            if value["name"] in names or client in clients:
                raise ValueError()
            names.add(value["name"])
            clients.add(client)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid agent registrations: require unique names/client UUIDs, secure endpoints, "
                         "and unique 32-byte URL-safe demo keys only in demo mode") from None
    return values


def seed_agents(sessions, values, settings):
    with sessions.begin() as session:
        for value in values:
            agent = session.scalar(select(db.Agent).where(db.Agent.registration_name == value["name"]))
            by_client = session.scalar(select(db.Agent).where(db.Agent.client_id == value["clientId"]))
            if agent is not None and by_client is not None and agent.id != by_client.id:
                raise ValueError("Agent registration name and clientId refer to different agents")
            agent = agent or by_client
            key = value.get("demoKey")
            if key:
                by_key = session.scalar(select(db.Agent).where(db.Agent.demo_key == key))
                if by_key is not None and (agent is None or by_key.id != agent.id):
                    raise ValueError("A demo key is already assigned to a different registered agent")
            if agent is None:
                agent_id = db.new_id()
                user = db.User(source="agent", subject=agent_id, tenant_id=settings.tenant_id,
                               upn=f"{agent_id}@agents.simulator", display_name=value["displayName"], roles=[])
                session.add(user)
                session.flush()
                agent = db.Agent(id=agent_id, user_id=user.id)
                session.add(agent)
            session.get(db.User, agent.user_id).display_name = value["displayName"]
            agent.registration_name = value["name"]
            agent.client_id = value["clientId"]
            agent.endpoint_url = value["messagingEndpoint"]
            agent.demo_key = value.get("demoKey")
            agent.enabled = True
