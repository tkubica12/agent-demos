from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from microsoft_agents.activity import Activity, Attachment

from bridge.generated_apps import APP_ID_PATTERN, CARD_RETENTION_SECONDS
from bridge.interactions import CARD_CONTENT_TYPE


GENERATED_APP_CARD_START = "<GENERATED_APP_CARD_REQUEST>"
GENERATED_APP_CARD_END = "</GENERATED_APP_CARD_REQUEST>"
GENERATED_APP_ACTION_TOKEN = "generatedAppActionToken"
GENERATED_APP_ACTION = "generatedAppAction"


def extract_generated_app_card_request(
    response: str,
) -> tuple[str, dict[str, str] | None]:
    starts = response.count(GENERATED_APP_CARD_START)
    ends = response.count(GENERATED_APP_CARD_END)
    if starts == 0 and ends == 0:
        return response, None
    if starts != 1 or ends != 1:
        visible = re.sub(
            re.escape(GENERATED_APP_CARD_START)
            + r".*?"
            + re.escape(GENERATED_APP_CARD_END),
            "",
            response,
            flags=re.DOTALL,
        )
        return visible.strip(), None
    start = response.index(GENERATED_APP_CARD_START)
    end = response.index(GENERATED_APP_CARD_END, start)
    raw = response[
        start + len(GENERATED_APP_CARD_START) : end
    ].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Generated app card request is invalid JSON."
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(
            "Generated app card request must be an object."
        )
    mode = str(payload.get("mode") or "").strip().lower()
    if mode not in {"app", "list"}:
        raise ValueError(
            "Generated app card mode must be app or list."
        )
    app_id = str(payload.get("appId") or "").strip().lower()
    if mode == "app" and not APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError(
            "Generated app card appId is invalid."
        )
    if mode == "list" and app_id:
        raise ValueError(
            "Generated app list cards cannot specify appId."
        )
    visible = (
        response[:start]
        + response[end + len(GENERATED_APP_CARD_END) :]
    ).strip()
    return visible, {"mode": mode, "appId": app_id}


def _cipher(secret: str | None = None) -> Fernet:
    value = (
        secret
        or os.getenv("API_SERVER_KEY", "")
    )
    if not value:
        raise RuntimeError(
            "Generated app action tokens require API_SERVER_KEY."
        )
    key = base64.urlsafe_b64encode(
        hashlib.sha256(value.encode("utf-8")).digest()
    )
    return Fernet(key)


def create_generated_app_action_token(
    *,
    app_id: str,
    action: str,
    user_id: str,
    conversation_id: str,
    retention_seconds: int = 0,
    expires_at_unix: float | None = None,
    secret: str | None = None,
) -> str:
    payload = {
        "version": "1.0",
        "actionId": uuid.uuid4().hex,
        "appId": app_id,
        "action": action,
        "retentionSeconds": retention_seconds,
        "userId": user_id,
        "conversationId": conversation_id,
        "workerId": os.getenv("WORKER_ID", ""),
        "expiresAtUnix": (
            expires_at_unix
            if expires_at_unix is not None
            else time.time() + (7 * 24 * 60 * 60)
        ),
    }
    return _cipher(secret).encrypt(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")


def decode_generated_app_action_token(
    token: str,
    *,
    user_id: str,
    conversation_id: str,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(
            _cipher(secret).decrypt(token.encode("ascii"))
        )
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "The generated app action token is invalid."
        ) from exc
    if not isinstance(payload, dict) or payload.get("version") != "1.0":
        raise ValueError(
            "The generated app action token is invalid."
        )
    if float(payload.get("expiresAtUnix") or 0) <= time.time():
        raise ValueError("The generated app action has expired.")
    if payload.get("userId") != user_id:
        raise ValueError(
            "The generated app action belongs to another user."
        )
    if payload.get("conversationId") != conversation_id:
        raise ValueError(
            "The generated app action belongs to another conversation."
        )
    if payload.get("workerId") not in {
        "",
        os.getenv("WORKER_ID", ""),
    }:
        raise ValueError(
            "The generated app action belongs to another Worker."
        )
    app_id = str(payload.get("appId") or "")
    if not APP_ID_PATTERN.fullmatch(app_id):
        raise ValueError(
            "The generated app action appId is invalid."
        )
    action = str(payload.get("action") or "")
    retention_seconds = int(
        payload.get("retentionSeconds") or 0
    )
    if action == "renew":
        if retention_seconds not in CARD_RETENTION_SECONDS:
            raise ValueError(
                "The generated app retention choice is invalid."
            )
    elif action != "delete":
        raise ValueError(
            "The generated app action is invalid."
        )
    return payload


def generated_app_action_data(
    value: object,
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    action = value.get("action")
    if isinstance(action, dict) and isinstance(action.get("data"), dict):
        value = action["data"]
    token = value.get(GENERATED_APP_ACTION_TOKEN)
    name = value.get(GENERATED_APP_ACTION)
    if not isinstance(token, str) or not isinstance(name, str):
        return None
    return {"token": token, "action": name}


def _action(
    *,
    title: str,
    app_id: str,
    action: str,
    user_id: str,
    conversation_id: str,
    retention_seconds: int = 0,
    style: str | None = None,
) -> dict[str, Any]:
    token = create_generated_app_action_token(
        app_id=app_id,
        action=action,
        user_id=user_id,
        conversation_id=conversation_id,
        retention_seconds=retention_seconds,
    )
    data = {
        GENERATED_APP_ACTION_TOKEN: token,
        GENERATED_APP_ACTION: action,
    }
    result: dict[str, Any] = {
        "type": "Action.Execute",
        "title": title,
        "verb": "generatedAppAction",
        "data": data,
        "fallback": {
            "type": "Action.Submit",
            "title": title,
            "data": data,
        },
    }
    if style:
        result["style"] = style
        result["fallback"]["style"] = style
    return result


def _hours(seconds: int) -> str:
    hours = seconds // 3600
    return f"{hours} hour" if hours == 1 else f"{hours} hours"


def generated_apps_card(
    apps: list[dict[str, Any]],
    *,
    user_id: str,
    conversation_id: str,
    title: str = "Your generated sites",
) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "Sites auto-suspend after 5 minutes idle and wake "
                "OnDemand. Retention starts after suspension."
            ),
            "wrap": True,
            "isSubtle": True,
        },
        {
            "type": "TextBlock",
            "text": (
                "This card is a snapshot. After an action, ask Hermes "
                "for your running sites to refresh it."
            ),
            "wrap": True,
            "isSubtle": True,
        },
    ]
    if not apps:
        body.append(
            {
                "type": "TextBlock",
                "text": "You have no generated sites.",
                "wrap": True,
            }
        )
    if not apps:
        return {
            "type": "AdaptiveCard",
            "$schema": (
                "http://adaptivecards.io/schemas/adaptive-card.json"
            ),
            "version": "1.4",
            "body": body,
        }
    app = apps[0]
    app_id = str(app["appId"])
    state = str(app.get("state") or "Unknown")
    retention = int(app.get("retentionSeconds") or 0)
    body.extend(
        [
            {
                "type": "TextBlock",
                "text": str(app.get("name") or app_id),
                "weight": "Bolder",
                "wrap": True,
                "separator": True,
            },
            {
                "type": "FactSet",
                "facts": [
                    {"title": "State", "value": state},
                    {
                        "title": "Retention",
                        "value": (
                            _hours(retention)
                            + " after suspension"
                        ),
                    },
                ],
            },
        ]
    )
    if app.get("url"):
        body.append(
            {
                "type": "TextBlock",
                "text": f"[Open site]({app['url']})",
                "wrap": True,
            }
        )
    actions = [
        _action(
            title=label,
            app_id=app_id,
            action="renew",
            user_id=user_id,
            conversation_id=conversation_id,
            retention_seconds=seconds,
        )
        for label, seconds in (
            ("Keep 1h", 3600),
            ("Keep 6h", 21600),
            ("Keep 24h", 86400),
            ("Keep 72h", 259200),
        )
    ]
    actions.append(
        _action(
            title="Delete now",
            app_id=app_id,
            action="delete",
            user_id=user_id,
            conversation_id=conversation_id,
            style="destructive",
        )
    )
    return {
        "type": "AdaptiveCard",
        "$schema": (
            "http://adaptivecards.io/schemas/adaptive-card.json"
        ),
        "version": "1.4",
        "body": body,
        "actions": actions,
    }


def generated_apps_activity(
    apps: list[dict[str, Any]],
    *,
    user_id: str,
    conversation_id: str,
    title: str = "Your generated sites",
) -> Activity:
    cards = (
        [
            generated_apps_card(
                [app],
                user_id=user_id,
                conversation_id=conversation_id,
                title=title,
            )
            for app in apps
        ]
        if apps
        else [
            generated_apps_card(
                [],
                user_id=user_id,
                conversation_id=conversation_id,
                title=title,
            )
        ]
    )
    return Activity(
        type="message",
        attachments=[
            Attachment(
                content_type=CARD_CONTENT_TYPE,
                content=card,
            )
            for card in cards
        ],
    )


def generated_app_action_completed_card(
    message: str,
) -> dict[str, Any]:
    return {
        "type": "AdaptiveCard",
        "$schema": (
            "http://adaptivecards.io/schemas/adaptive-card.json"
        ),
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Site action completed",
                "weight": "Bolder",
                "size": "Medium",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": message,
                "color": "Good",
                "wrap": True,
            },
            {
                "type": "TextBlock",
                "text": (
                    "Ask Hermes for your running sites to get "
                    "fresh controls."
                ),
                "isSubtle": True,
                "wrap": True,
            },
        ],
    }
