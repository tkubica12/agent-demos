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


INTERACTION_START = "<ADAPTIVE_CARD_REQUEST>"
INTERACTION_END = "</ADAPTIVE_CARD_REQUEST>"
ACTION_TOKEN_FIELD = "interactionActionToken"
ACTION_CHOICE_FIELD = "interactionChoice"
CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"
MAX_REQUEST_BYTES = 16_384
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
LOCALE_PATTERN = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"
)


def _bounded_text(
    value: object,
    *,
    field: str,
    maximum: int,
    required: bool = False,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"{field} is required.")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters.")
    return text


def _bounded_list(
    value: object,
    *,
    field: str,
    maximum: int,
) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array.")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} items.")
    return value


def validate_interaction_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Adaptive Card request must be an object.")
    kind = str(value.get("kind") or "").strip().lower()
    if kind not in {"display", "choice", "confirm"}:
        raise ValueError("kind must be display, choice, or confirm.")
    spec: dict[str, Any] = {
        "kind": kind,
        "title": _bounded_text(
            value.get("title"),
            field="title",
            maximum=80,
            required=True,
        ),
        "summary": _bounded_text(
            value.get("summary"),
            field="summary",
            maximum=800,
        ),
        "locale": _bounded_text(
            value.get("locale") or "en-US",
            field="locale",
            maximum=20,
        ),
        "sections": [],
        "facts": [],
        "table": None,
        "status": None,
        "choices": [],
    }
    if not LOCALE_PATTERN.fullmatch(spec["locale"]):
        raise ValueError("locale must be a BCP-47 language tag.")
    for section in _bounded_list(
        value.get("sections"),
        field="sections",
        maximum=5,
    ):
        if not isinstance(section, dict):
            raise ValueError("Each section must be an object.")
        spec["sections"].append(
            {
                "heading": _bounded_text(
                    section.get("heading"),
                    field="section heading",
                    maximum=60,
                ),
                "text": _bounded_text(
                    section.get("text"),
                    field="section text",
                    maximum=800,
                    required=True,
                ),
            }
        )
    for fact in _bounded_list(
        value.get("facts"),
        field="facts",
        maximum=10,
    ):
        if not isinstance(fact, dict):
            raise ValueError("Each fact must be an object.")
        spec["facts"].append(
            {
                "label": _bounded_text(
                    fact.get("label"),
                    field="fact label",
                    maximum=40,
                    required=True,
                ),
                "value": _bounded_text(
                    fact.get("value"),
                    field="fact value",
                    maximum=160,
                    required=True,
                ),
            }
        )
    status = value.get("status")
    if status is not None:
        if not isinstance(status, dict):
            raise ValueError("status must be an object.")
        tone = str(status.get("tone") or "neutral").strip().lower()
        if tone not in {"neutral", "good", "warning", "attention"}:
            raise ValueError(
                "status tone must be neutral, good, warning, or attention."
            )
        spec["status"] = {
            "label": _bounded_text(
                status.get("label"),
                field="status label",
                maximum=100,
                required=True,
            ),
            "tone": tone,
        }
    table = value.get("table")
    if table is not None:
        if not isinstance(table, dict):
            raise ValueError("table must be an object.")
        columns = [
            _bounded_text(
                item,
                field="table column",
                maximum=40,
                required=True,
            )
            for item in _bounded_list(
                table.get("columns"),
                field="table columns",
                maximum=5,
            )
        ]
        if not columns:
            raise ValueError("table columns are required.")
        rows = []
        for row in _bounded_list(
            table.get("rows"),
            field="table rows",
            maximum=10,
        ):
            if not isinstance(row, list) or len(row) != len(columns):
                raise ValueError(
                    "Each table row must match the column count."
                )
            rows.append(
                [
                    _bounded_text(
                        cell,
                        field="table cell",
                        maximum=100,
                    )
                    for cell in row
                ]
            )
        spec["table"] = {"columns": columns, "rows": rows}
    for choice in _bounded_list(
        value.get("choices"),
        field="choices",
        maximum=5,
    ):
        if not isinstance(choice, dict):
            raise ValueError("Each choice must be an object.")
        identifier = str(choice.get("id") or "").strip().lower()
        if not IDENTIFIER_PATTERN.fullmatch(identifier):
            raise ValueError("Choice id is invalid.")
        spec["choices"].append(
            {
                "id": identifier,
                "label": _bounded_text(
                    choice.get("label"),
                    field="choice label",
                    maximum=40,
                    required=True,
                ),
            }
        )
    if kind == "confirm" and spec["choices"]:
        raise ValueError(
            "Confirm cards use bridge-owned Confirm and Cancel choices."
        )
    if kind == "confirm":
        spec["choices"] = [
            {
                "id": "confirm",
                "label": "Confirm",
            },
            {
                "id": "cancel",
                "label": "Cancel",
            },
        ]
    if kind in {"choice", "confirm"} and len(spec["choices"]) < 2:
        raise ValueError("Interactive cards require at least two choices.")
    if kind == "display" and spec["choices"]:
        raise ValueError("Display cards cannot contain choices.")
    return spec


def extract_interaction_request(
    response: str,
) -> tuple[str, dict[str, Any] | None]:
    starts = response.count(INTERACTION_START)
    ends = response.count(INTERACTION_END)
    if starts == 0 and ends == 0:
        return response, None
    if starts != 1 or ends != 1:
        visible = re.sub(
            re.escape(INTERACTION_START)
            + r".*?"
            + re.escape(INTERACTION_END),
            "",
            response,
            flags=re.DOTALL,
        )
        if INTERACTION_START in visible:
            visible = visible.split(INTERACTION_START, 1)[0]
        visible = visible.replace(INTERACTION_END, "")
        return visible.strip(), None
    start = response.index(INTERACTION_START)
    end = response.index(INTERACTION_END, start)
    raw = response[start + len(INTERACTION_START) : end].strip()
    if len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
        raise ValueError("Adaptive Card request is too large.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Adaptive Card request is invalid JSON.") from exc
    visible = (
        response[:start] + response[end + len(INTERACTION_END) :]
    ).strip()
    return visible, validate_interaction_spec(payload)


def _cipher(secret: str | None = None) -> Fernet:
    value = (
        secret
        or os.getenv("API_SERVER_KEY", "")
        or os.getenv("HERMES_API_SERVER_KEY", "")
    )
    if not value:
        raise RuntimeError(
            "Adaptive Card action tokens require API_SERVER_KEY."
        )
    key = base64.urlsafe_b64encode(
        hashlib.sha256(value.encode("utf-8")).digest()
    )
    return Fernet(key)


def create_interaction_token(
    *,
    interaction_id: str,
    choice: dict[str, str],
    session_key: str,
    user_id: str,
    conversation_id: str,
    expires_at_unix: float,
    secret: str | None = None,
) -> str:
    payload = {
        "version": "1.0",
        "interactionId": interaction_id,
        "choiceId": choice["id"],
        "choiceMessage": f"I selected: {choice['label']}.",
        "sessionKey": session_key,
        "userId": user_id,
        "conversationId": conversation_id,
        "workerId": os.getenv("WORKER_ID", ""),
        "expiresAtUnix": expires_at_unix,
    }
    return _cipher(secret).encrypt(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")


def decode_interaction_token(
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
            "The Adaptive Card action token is invalid."
        ) from exc
    if not isinstance(payload, dict) or payload.get("version") != "1.0":
        raise ValueError("The Adaptive Card action token is invalid.")
    if float(payload.get("expiresAtUnix") or 0) <= time.time():
        raise ValueError("The Adaptive Card action has expired.")
    if payload.get("userId") != user_id:
        raise ValueError(
            "The Adaptive Card action belongs to another user."
        )
    if payload.get("conversationId") != conversation_id:
        raise ValueError(
            "The Adaptive Card action belongs to another conversation."
        )
    if payload.get("workerId") not in {
        "",
        os.getenv("WORKER_ID", ""),
    }:
        raise ValueError(
            "The Adaptive Card action belongs to another Worker."
        )
    return payload


def interaction_action_data(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    action = value.get("action")
    if isinstance(action, dict) and isinstance(action.get("data"), dict):
        value = action["data"]
    token = value.get(ACTION_TOKEN_FIELD)
    choice = value.get(ACTION_CHOICE_FIELD)
    if not isinstance(token, str) or not isinstance(choice, str):
        return None
    return {"token": token, "choice": choice}


def _text_block(
    text: str,
    *,
    weight: str | None = None,
    size: str | None = None,
    color: str | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "TextBlock",
        "text": text,
        "wrap": True,
    }
    if weight:
        block["weight"] = weight
    if size:
        block["size"] = size
    if color:
        block["color"] = color
    return block


def render_card(
    spec: dict[str, Any],
    *,
    session_key: str,
    user_id: str,
    conversation_id: str,
    expires_at_unix: float | None = None,
) -> tuple[Activity, str]:
    interaction_id = uuid.uuid4().hex
    body = [
        _text_block(
            spec["title"],
            weight="Bolder",
            size="Medium",
        )
    ]
    if spec["summary"]:
        body.append(_text_block(spec["summary"]))
    if spec["status"]:
        color = {
            "neutral": "Default",
            "good": "Good",
            "warning": "Warning",
            "attention": "Attention",
        }[spec["status"]["tone"]]
        body.append(
            _text_block(
                spec["status"]["label"],
                weight="Bolder",
                color=color,
            )
        )
    for section in spec["sections"]:
        if section["heading"]:
            body.append(
                _text_block(section["heading"], weight="Bolder")
            )
        body.append(_text_block(section["text"]))
    if spec["facts"]:
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {
                        "title": fact["label"],
                        "value": fact["value"],
                    }
                    for fact in spec["facts"]
                ],
            }
        )
    if spec["table"]:
        columns = spec["table"]["columns"]
        body.append(
            {
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [
                            _text_block(column, weight="Bolder")
                        ],
                    }
                    for column in columns
                ],
            }
        )
        for row in spec["table"]["rows"]:
            body.append(
                {
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [_text_block(cell)],
                        }
                        for cell in row
                    ],
                    "separator": True,
                }
            )
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "lang": spec["locale"],
        "body": body,
    }
    if spec["choices"]:
        expiry = expires_at_unix or (time.time() + 15 * 60)
        card["actions"] = []
        for choice in spec["choices"]:
            token = create_interaction_token(
                interaction_id=interaction_id,
                choice=choice,
                session_key=session_key,
                user_id=user_id,
                conversation_id=conversation_id,
                expires_at_unix=expiry,
            )
            data = {
                ACTION_TOKEN_FIELD: token,
                ACTION_CHOICE_FIELD: choice["id"],
            }
            card["actions"].append(
                {
                    "type": "Action.Execute",
                    "title": choice["label"],
                    "verb": "autopilot.interaction.choice",
                    "data": data,
                    "fallback": {
                        "type": "Action.Submit",
                        "title": choice["label"],
                        "data": data,
                    },
                }
            )
    fallback = spec["summary"] or spec["title"]
    activity = Activity(
        type="message",
        text=fallback,
        attachments=[
            Attachment(
                contentType=CARD_CONTENT_TYPE,
                content=card,
            )
        ],
    )
    return activity, interaction_id


def completed_card(
    *,
    choice_label: str,
) -> dict[str, Any]:
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            _text_block("Response received", weight="Bolder"),
            _text_block(choice_label, color="Good"),
        ],
    }
