from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import httpx


class AGUIConversation:
    def __init__(self, url: str) -> None:
        self.url = url
        self.thread_id = str(uuid.uuid4())
        self.last_run_id: str | None = None
        self.messages: list[dict] = []

    def reset(self) -> None:
        self.thread_id = str(uuid.uuid4())
        self.last_run_id = None
        self.messages.clear()

    def run(self, message: str, timeout: float = 120.0) -> Iterator[dict]:
        run_id = str(uuid.uuid4())
        self.messages.append(
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": message,
            }
        )

        assistant_text = ""
        with httpx.stream(
            "POST",
            self.url,
            headers={"Accept": "text/event-stream"},
            json={
                "threadId": self.thread_id,
                "runId": run_id,
                "parentRunId": self.last_run_id,
                "state": {},
                "messages": self.messages,
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue

                event = json.loads(line.removeprefix("data:").strip())
                if event["type"] == "RUN_STARTED":
                    self.thread_id = event.get("threadId", self.thread_id)
                    self.last_run_id = event.get("runId", run_id)
                elif event["type"] == "TEXT_MESSAGE_CONTENT":
                    assistant_text += event.get("delta", "")

                yield event

        if assistant_text:
            self.messages.append(
                {
                    "id": str(uuid.uuid4()),
                    "role": "assistant",
                    "content": assistant_text,
                }
            )
