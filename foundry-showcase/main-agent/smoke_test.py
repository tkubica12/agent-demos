from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def extract_response_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text

    output = payload.get("output")
    if not isinstance(output, list):
        return ""

    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, dict):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
    return "\n".join(text_parts)


def main() -> int:
    parser = ArgumentParser(description="Smoke test the local Responses endpoint.")
    parser.add_argument(
        "--url",
        default="http://localhost:8088/responses",
        help="Responses endpoint URL.",
    )
    parser.add_argument(
        "--input",
        default="Reply with exactly: foundry showcase responses ok",
        help="Prompt to send to the agent.",
    )
    args = parser.parse_args()

    request = Request(
        args.url,
        data=json.dumps({"input": args.input}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8")
    except HTTPError as error:
        print(error.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except URLError as error:
        print(f"Could not reach {args.url}: {error}", file=sys.stderr)
        return 1

    payload = json.loads(body)
    text = extract_response_text(payload)
    if not text:
        print(f"Response did not include response text: {body}", file=sys.stderr)
        return 1

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
