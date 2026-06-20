from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from agui_client import AGUIConversation, DEFAULT_INVOCATIONS_URL, azure_ai_auth_headers


def load_cases(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_INVOCATIONS_URL)
    parser.add_argument("--cases", default="evals/personality_cases.jsonl")
    parser.add_argument("--out", default="evals/generated_conversations.jsonl")
    parser.add_argument("--bearer-token")
    args = parser.parse_args()

    headers = {}
    if args.bearer_token:
        headers["Authorization"] = f"Bearer {args.bearer_token}"
    elif args.url.startswith("https://"):
        headers.update(azure_ai_auth_headers())

    cases = load_cases(Path(args.cases))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as output:
        for case in cases:
            conversation = AGUIConversation(args.url, headers=headers)
            response_text = "".join(
                event.get("delta", "")
                for event in conversation.run(case["query"])
                if event["type"] == "TEXT_MESSAGE_CONTENT"
            )
            row = {
                **case,
                "response": response_text,
                "thread_id": conversation.thread_id,
                "run_id": conversation.last_run_id,
                "correlation_id": conversation.last_correlation_id,
                "conversation": {
                    "messages": [
                        {"role": "user", "content": case["query"]},
                        {"role": "assistant", "content": response_text},
                    ]
                },
            }
            output.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Wrote {len(cases)} conversations to {out_path}")


if __name__ == "__main__":
    main()
