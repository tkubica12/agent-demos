from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from fastapi import FastAPI, Request
from microsoft_agents.activity import load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    Authorization,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.fastapi import (
    CloudAdapter,
    jwt_authorization_decorator,
    start_agent_process,
)

from private_bot_proxy.config import get_settings
from private_bot_proxy.evidence import build_control_evidence, build_evidence

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("private-bot-proxy")


def create_agent() -> tuple[
    AgentApplication[TurnState],
    CloudAdapter,
    MsalConnectionManager,
]:
    configuration = load_configuration_from_env(os.environ)
    storage = MemoryStorage()
    connection_manager = MsalConnectionManager(**configuration)
    adapter = CloudAdapter(connection_manager=connection_manager)
    authorization = Authorization(storage, connection_manager, **configuration)
    agent = AgentApplication[TurnState](
        storage=storage,
        adapter=adapter,
        authorization=authorization,
        start_typing_timer=False,
        **configuration,
    )
    return agent, adapter, connection_manager


AGENT_APP, ADAPTER, CONNECTION_MANAGER = create_agent()
app = FastAPI(title="Private Bot Proxy")
app.state.agent_configuration = CONNECTION_MANAGER.get_default_connection_configuration()


def turn_identity(context: TurnContext) -> Any:
    if context.identity is not None:
        return context.identity
    for key in ("identity", "user", "http_auth"):
        value = context.turn_state.get(key)
        if value is not None:
            return value
    return None


async def graph_me(token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            params={"$select": "id,displayName,userPrincipalName"},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.json()


@AGENT_APP.message(re.compile(r"^(?!SSO-).*", re.IGNORECASE))
async def control_response(context: TurnContext, _state: TurnState) -> None:
    settings = get_settings()
    evidence = build_control_evidence(
        channel_id=context.activity.channel_id or "",
        connector_identity=turn_identity(context),
    )
    correlation = (context.activity.text or "").strip()
    LOGGER.info(
        json.dumps(
            {
                "event": "authenticated-control-turn",
                "correlation": correlation[:128],
                "evidence": evidence,
            },
            sort_keys=True,
        )
    )
    await context.send_activity(
        f"{settings.fixed_response}\n\nAuthentication evidence:\n"
        f"```json\n{json.dumps(evidence, sort_keys=True)}\n```"
    )


@AGENT_APP.message(re.compile(r"^SSO-.*", re.IGNORECASE), auth_handlers=["GRAPH"])
async def sso_response(context: TurnContext, _state: TurnState) -> None:
    settings = get_settings()
    token_a_response = await AGENT_APP.auth.get_token(context, "GRAPH")
    token_b_response = await AGENT_APP.auth.exchange_token(
        context,
        [settings.graph_scope],
        "GRAPH",
    )
    me = await graph_me(token_b_response.token)
    evidence = build_evidence(
        channel_id=context.activity.channel_id or "",
        connector_identity=turn_identity(context),
        token_a=token_a_response.token,
        token_b=token_b_response.token,
        graph_me=me,
        expected_audience=f"api://botid-{settings.app_id}",
        expected_tenant=settings.tenant_id,
    )
    correlation = (context.activity.text or "").strip()
    LOGGER.info(
        json.dumps(
            {
                "event": "authenticated-turn",
                "correlation": correlation[:128],
                "evidence": evidence,
            },
            sort_keys=True,
        )
    )
    await context.send_activity(
        f"{settings.fixed_response}\n\nAuthentication evidence:\n"
        f"```json\n{json.dumps(evidence, sort_keys=True)}\n```"
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/messages")
@jwt_authorization_decorator
async def messages(request: Request):
    return await start_agent_process(request, AGENT_APP, ADAPTER)
