from __future__ import annotations

import argparse
import os

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18080/v1")
    parser.add_argument("--model", default=os.getenv("OPENCLAW_MODEL_ID", "gpt-5-4-mini"))
    args = parser.parse_args()

    response = httpx.post(
        f"{args.url.rstrip('/')}/chat/completions",
        headers={"Authorization": "Bearer ignored-by-proxy"},
        json={
            "model": args.model,
            "messages": [{"role": "user", "content": "Say exactly: foundry entra proxy ok"}],
            "temperature": 0,
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    print(data["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
