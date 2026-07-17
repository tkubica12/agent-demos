from __future__ import annotations

import argparse
import json
import uuid

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test the deployed AG-UI BFF.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    events = []
    with httpx.stream(
        "POST",
        f"{args.url.rstrip('/')}/agui",
        headers={
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {args.token}",
            "x-correlation-id": str(uuid.uuid4()),
        },
        json={
            "threadId": thread_id,
            "runId": run_id,
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": "Reply with exactly: AG-UI showcase ready",
                }
            ],
        },
        timeout=240,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))

    errors = [event for event in events if event["type"] == "RUN_ERROR"]
    text = "".join(
        event.get("delta", "")
        for event in events
        if event["type"] == "TEXT_MESSAGE_CONTENT"
    )
    if errors:
        raise RuntimeError(json.dumps(errors, indent=2))
    if "AG-UI showcase ready" not in text:
        raise RuntimeError(f"Unexpected AG-UI response: {text!r}")
    if not any(event["type"] == "RUN_FINISHED" for event in events):
        raise RuntimeError("AG-UI stream did not finish.")
    print(json.dumps({"threadId": thread_id, "response": text}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
