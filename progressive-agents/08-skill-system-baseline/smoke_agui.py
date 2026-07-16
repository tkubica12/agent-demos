from __future__ import annotations

import argparse
import json

from agui_client import AGUIConversation, DEFAULT_AGUI_URL


def run_turn(
    conversation: AGUIConversation,
    message: str,
    expected_text: str,
) -> tuple[str, str]:
    events = list(conversation.run(message))
    started = next(event for event in events if event["type"] == "RUN_STARTED")
    text = "".join(
        event.get("delta", "")
        for event in events
        if event["type"] == "TEXT_MESSAGE_CONTENT"
    )
    if not text:
        raise RuntimeError(f"No streamed text returned. Events: {events}")
    if expected_text.casefold() not in text.casefold():
        raise RuntimeError(f"Expected {expected_text!r} in streamed text: {text}")
    if started["threadId"] != conversation.thread_id:
        raise RuntimeError(
            f"Thread context changed: {started['threadId']} != {conversation.thread_id}"
        )
    return text, started["runId"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_AGUI_URL)
    parser.add_argument("--bearer-token")
    args = parser.parse_args()

    headers = {}
    if args.bearer_token:
        headers["Authorization"] = f"Bearer {args.bearer_token}"

    conversation = AGUIConversation(args.url, headers=headers)
    first, first_run = run_turn(conversation, "Say exactly: agui first ok", "agui first ok")
    second, second_run = run_turn(conversation, "Say exactly: agui second ok", "agui second ok")

    print(
        json.dumps(
            {
                "threadId": conversation.thread_id,
                "runs": [first_run, second_run],
                "responses": [first, second],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
