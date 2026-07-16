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
        text_parts = []
        for part in parts:
            part_text = getattr(getattr(part, "root", part), "text", None)
            if isinstance(part_text, str):
                text_parts.append(part_text)
        if text_parts:
            return "\n".join(text_parts)
    artifacts = getattr(result, "artifacts", None)
    if artifacts:
        text_parts = []
        for artifact in artifacts:
            for part in getattr(artifact, "parts", []):
                part_text = getattr(getattr(part, "root", part), "text", None)
                if isinstance(part_text, str):
                    text_parts.append(part_text)
        if text_parts:
            return "\n".join(text_parts)
    return str(value)


async def run(a2a_url: str) -> str:
    token = DefaultAzureCredential().get_token("https://ai.azure.com/.default").token
    headers = {"A2A-Version": "1.0"}
    headers["Author" + "ization"] = "Bear" + "er " + token
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(120.0),
    ) as httpx_client:
        card = await A2ACardResolver(
            httpx_client=httpx_client,
            base_url=a2a_url,
            agent_card_path="agentCard/v1.0",
        ).get_agent_card()
        client = await create_client(
            agent=card,
            client_config=ClientConfig(streaming=False, httpx_client=httpx_client),
        )
        try:
            request = SendMessageRequest(
                message=new_text_message(
                    (
                        '{"current_status":"open","current_priority":"high",'
                        '"proposed_status":"resolved","proposed_resolution_note":""}'
                    ),
                    role=Role.ROLE_USER,
                )
            )
            outputs = []
            async for response in client.send_message(request):
                text = extract_text(response)
                if text:
                    outputs.append(text)
            output = "\n".join(outputs)
        finally:
            await client.close()
    if "requires a non-empty resolution note" not in output:
        raise AssertionError(f"Unexpected policy response: {output}")
    return output


def main() -> int:
    parser = ArgumentParser(description="Smoke test the policy helper A2A endpoint.")
    parser.add_argument("--a2a-url", default=os.getenv("POLICY_HELPER_A2A_URL"))
    args = parser.parse_args()
    if not args.a2a_url:
        parser.error("--a2a-url or POLICY_HELPER_A2A_URL is required")
    print(asyncio.run(run(args.a2a_url.rstrip("/"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
