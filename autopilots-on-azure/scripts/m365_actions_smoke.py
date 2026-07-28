from __future__ import annotations

import argparse
import json
import re
import uuid

import httpx

from scripts.setup_app_tfvars import runtime_outputs_path
from scripts.user_schedule_smoke import request_with_retry


TEAMS_RESULT_PREFIX = "A14_TEAMS_RESULT="


def proactive_message(marker: str) -> str:
    return (
        "Hi, digital worker here. I am validating proactive project "
        f"follow-up. Is this on track? [{marker}]"
    )


def create_chat_prompt(recipient: str, marker: str) -> str:
    message = proactive_message(marker)
    return (
        "Use only workiq-teams. Create or reuse a one-on-one chat between "
        f"the Agent User and {recipient}, then send exactly this plain-text "
        f"message: {json.dumps(message)}. Do not send email. Do not claim "
        "success unless both tools succeed. Finish with one line: "
        f'{TEAMS_RESULT_PREFIX}{{"chatId":"<id>","messageId":"<id>"}}'
    )


def parse_teams_result(response_text: str) -> dict[str, str]:
    line = next(
        (
            item.strip()
            for item in reversed(response_text.splitlines())
            if item.strip().startswith(TEAMS_RESULT_PREFIX)
        ),
        "",
    )
    if not line:
        raise ValueError(
            f"Hermes response omitted {TEAMS_RESULT_PREFIX}."
        )
    payload = json.loads(line.removeprefix(TEAMS_RESULT_PREFIX))
    if not isinstance(payload, dict):
        raise ValueError("Hermes returned a non-object Teams result.")
    result = {
        "chatId": str(payload.get("chatId") or ""),
        "messageId": str(payload.get("messageId") or ""),
    }
    if not all(result.values()):
        raise ValueError("Hermes returned incomplete Teams identifiers.")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create and independently verify a real proactive Agent User "
            "one-to-one Teams message."
        )
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send the real Teams message. Omit for a dry run.",
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", args.recipient):
        raise ValueError("--recipient must be a valid email or UPN.")

    marker = f"A14-TEAMS-{uuid.uuid4().hex[:16]}"
    if not args.execute:
        print(
            json.dumps(
                {
                    "execute": False,
                    "recipient": args.recipient,
                    "message": proactive_message(marker),
                },
                indent=2,
            )
        )
        return

    outputs = json.loads(
        runtime_outputs_path("hermes", args.state_name).read_text(
            encoding="utf-8"
        )
    )
    bridge_url = str(outputs["bridge_url"]).rstrip("/")
    with httpx.Client(timeout=args.timeout) as client:
        created = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/invoke",
            json={
                "conversationId": (
                    f"hermes-a14-teams-create-{uuid.uuid4().hex}"
                ),
                "message": create_chat_prompt(
                    args.recipient,
                    marker,
                ),
                "persistenceDisabled": True,
            },
            timeout=args.timeout,
            attempts=8,
        )
        created.raise_for_status()
        identifiers = parse_teams_result(
            str(created.json().get("response") or "")
        )
        verified = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/invoke",
            json={
                "conversationId": (
                    f"hermes-a14-teams-verify-{uuid.uuid4().hex}"
                ),
                "message": (
                    "Use only workiq-teams GetChatMessage to read message "
                    f"id {identifiers['messageId']} from chat id "
                    f"{identifiers['chatId']}. Return its exact plain-text "
                    "body. Do not create or modify anything."
                ),
                "persistenceDisabled": True,
            },
            timeout=args.timeout,
            attempts=8,
        )
        verified.raise_for_status()
    verified_text = str(verified.json().get("response") or "")
    if marker not in verified_text:
        raise RuntimeError(
            "The independent Teams read-back omitted the hidden marker."
        )
    print(
        json.dumps(
            {
                "ok": True,
                "recipient": args.recipient,
                "marker": marker,
                **identifiers,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
