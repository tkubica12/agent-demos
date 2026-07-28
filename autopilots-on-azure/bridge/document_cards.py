from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


ACTION_TOKEN_FIELD = "documentActionToken"
ACTION_CHOICE_FIELD = "documentActionChoice"
ACTION_CHOICES = {"background", "copy"}


def office_operation_scope(
    conversation_id: str,
    user_id: str,
) -> str:
    return hashlib.sha256(
        (
            f"office-publish:{user_id}:{conversation_id}"
        ).encode("utf-8")
    ).hexdigest()[:32]


def token_cipher(secret: str | None = None) -> Fernet:
    value = (
        secret
        or os.getenv("API_SERVER_KEY", "")
        or os.getenv("HERMES_API_SERVER_KEY", "")
    )
    if not value:
        raise RuntimeError(
            "Document action tokens require API_SERVER_KEY."
        )
    key = base64.urlsafe_b64encode(
        hashlib.sha256(value.encode("utf-8")).digest()
    )
    return Fernet(key)


def create_action_token(
    *,
    operation_id: str,
    operation_scope: str,
    user_id: str,
    conversation_id: str,
    expires_at_unix: float,
    secret: str | None = None,
) -> str:
    payload = {
        "version": "1.0",
        "operationId": operation_id,
        "operationScope": operation_scope,
        "userId": user_id,
        "conversationId": conversation_id,
        "workerId": os.getenv("WORKER_ID", ""),
        "expiresAtUnix": expires_at_unix,
    }
    return token_cipher(secret).encrypt(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).decode("ascii")


def decode_action_token(
    token: str,
    *,
    user_id: str,
    conversation_id: str,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        payload = json.loads(
            token_cipher(secret).decrypt(
                token.encode("ascii")
            )
        )
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "The document action token is invalid."
        ) from exc
    if not isinstance(payload, dict) or payload.get("version") != "1.0":
        raise ValueError("The document action token is invalid.")
    if float(payload.get("expiresAtUnix") or 0) <= time.time():
        raise ValueError("The document action has expired.")
    if payload.get("userId") != user_id:
        raise ValueError(
            "The document action belongs to another user."
        )
    if payload.get("conversationId") != conversation_id:
        raise ValueError(
            "The document action belongs to another conversation."
        )
    worker_id = os.getenv("WORKER_ID", "")
    if payload.get("workerId") not in {"", worker_id}:
        raise ValueError(
            "The document action belongs to another Worker."
        )
    return payload


def action_data(activity_value: object) -> dict[str, str] | None:
    if not isinstance(activity_value, dict):
        return None
    value = activity_value
    action = value.get("action")
    if isinstance(action, dict) and isinstance(
        action.get("data"),
        dict,
    ):
        value = action["data"]
    token = value.get(ACTION_TOKEN_FIELD)
    choice = value.get(ACTION_CHOICE_FIELD)
    if not isinstance(token, str) or choice not in ACTION_CHOICES:
        return None
    return {"token": token, "choice": str(choice)}
