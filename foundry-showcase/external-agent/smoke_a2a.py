"""Smoke test: fetch the agent card and send one A2A message/send request."""

from __future__ import annotations

import json
import os
import sys
import uuid
from argparse import ArgumentParser
from urllib.request import Request, urlopen


def send_a2a(base_url: str, question: str) -> str:
    url = base_url.rstrip("/")
    with urlopen(f"{url}/.well-known/agent-card.json", timeout=20) as resp:
        card = json.loads(resp.read())
    assert card["name"] == "Support Triage Assistant", f"Unexpected agent name: {card['name']}"

    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": question}],
                }
            },
            "id": "smoke-1",
        }
    ).encode()

    req = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read())

    assert result.get("id") == "smoke-1", f"Unexpected response id: {result.get('id')}"
    assert "result" in result, f"Error response: {result}"
    artifacts = result["result"].get("artifacts", [])
    assert artifacts, "No artifacts in response"
    text = artifacts[0]["parts"][0]["text"]
    assert text.strip(), "Empty response text"
    return text


def main() -> int:
    parser = ArgumentParser(description="Smoke test the support triage A2A agent.")
    parser.add_argument("--url", default=os.getenv("EXTERNAL_AGENT_URL"))
    args = parser.parse_args()
    if not args.url:
        parser.error("--url or EXTERNAL_AGENT_URL required")

    question = "A customer with a billing issue has been waiting 4 hours. What priority should this case be and who should handle it?"
    answer = send_a2a(args.url, question)
    print(json.dumps({"question": question, "answer": answer}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
