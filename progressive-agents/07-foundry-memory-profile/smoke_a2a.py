from __future__ import annotations

import asyncio
import os
from argparse import ArgumentParser
from typing import Any

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types.a2a_pb2 import Role, SendMessageRequest
from azure.identity import DefaultAzureCredential


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    root = getattr(value, "root", value)
    result = getattr(root, "result", root)
    result = getattr(result, "task", result)
    parts = getattr(result, "parts", None)
    if parts:
        text_parts: list[str] = []
        for part in parts:
            part_root = getattr(part, "root", part)
            text = getattr(part_root, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)
    artifacts = getattr(result, "artifacts", None)
    if artifacts:
        text_parts = []
        for artifact in artifacts:
            for part in getattr(artifact, "parts", []):
                part_root = getattr(part, "root", part)
                text = getattr(part_root, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
        if text_parts:
            return "\n".join(text_parts)
    return str(value)


async def run_smoke(a2a_url: str, message: str) -> str:
    token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(120.0),
    ) as httpx_client:
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=a2a_url,
            agent_card_path="agentCard/v1.0",
        )
        agent_card = await resolver.get_agent_card()
        client = await create_client(
            agent=agent_card,
            client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
        )
        try:
            request = SendMessageRequest(
                message=new_text_message(message, role=Role.ROLE_USER)
            )
            chunks = [extract_text(response) async for response in client.send_message(request)]
            return "\n".join(chunk for chunk in chunks if chunk)
        finally:
            await client.close()


def main() -> int:
    parser = ArgumentParser(description="Smoke test the Foundry A2A endpoint.")
    parser.add_argument(
        "--a2a-url",
        default=os.getenv("A2A_URL"),
        help="A2A base URL ending in /endpoint/protocols/a2a.",
    )
    parser.add_argument(
        "--message",
        default="Say exactly: deployed a2a ok",
        help="Text message to send through A2A.",
    )
    args = parser.parse_args()
    if not args.a2a_url:
        parser.error("--a2a-url or A2A_URL is required")

    print(asyncio.run(run_smoke(args.a2a_url.rstrip("/"), args.message)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
