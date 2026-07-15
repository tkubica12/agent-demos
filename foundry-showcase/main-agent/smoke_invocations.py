from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = ArgumentParser(description="Smoke test the Invocations endpoint.")
    parser.add_argument(
        "--url",
        default="http://localhost:8088/invocations",
        help="Invocations endpoint URL.",
    )
    parser.add_argument(
        "--message",
        default="Reply with exactly: foundry showcase invocations ok",
        help="Message to send to the agent.",
    )
    args = parser.parse_args()

    request = Request(
        args.url,
        data=json.dumps({"message": args.message, "stream": False}).encode("utf-8"),
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
    text = payload.get("response")
    if not isinstance(text, str) or not text:
        print(f"Response did not include response text: {body}", file=sys.stderr)
        return 1

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
