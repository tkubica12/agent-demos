from __future__ import annotations

import argparse
import json
import os
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MemorySearchOptions
from azure.identity import DefaultAzureCredential


PROFILE_MEMORIES = (
    "Tomas prefers concise progress reports that lead with the outcome.",
    "Tomas wants architecture, identity, evaluation, observability, and cost to remain easy to inspect.",
    "Tomas prefers repeatable scripts over portal-only instructions.",
)

MEMORY_OWNER = "tomas@tomasonline.net"

CONVERSATIONS = (
    (
        "We reviewed the Foundry showcase deployment. Tomas asked that every demo use "
        "real managed platform behavior and that unsupported preview limitations remain explicit."
    ),
    (
        "During the support operations review, Tomas chose a bounded primary agent with "
        "one focused policy helper instead of an unbounded group-chat design."
    ),
    (
        "For validation, Tomas requested concise evidence covering deployed behavior, "
        "identity, traces, evaluations, red-team findings, and cost-sensitive cleanup."
    ),
)


def dump(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, list):
        return [dump(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--store-name", default="foundry-showcase-main")
    parser.add_argument("--scope", default=os.getenv("MEMORY_SCOPE"))
    parser.add_argument("--owner", default=MEMORY_OWNER)
    parser.add_argument("--model", default="gpt-5.4-mini")
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")
    if not args.scope:
        parser.error(
            "--scope or MEMORY_SCOPE is required; use <tenant-id>_<object-id> "
            "to match the deployed AG-UI identity."
        )

    client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    existing = list(
        client.beta.memory_stores.list_memories(
            name=args.store_name,
            scope=args.scope,
        )
    )
    existing_content = {
        str(getattr(item, "content", "")).strip() for item in existing
    }
    has_chat_summaries = any(
        str(getattr(item, "kind", "")) == "chat_summary" for item in existing
    )

    created_profiles = []
    for content in PROFILE_MEMORIES:
        if content in existing_content:
            continue
        created_profiles.append(
            client.beta.memory_stores.create_memory(
                name=args.store_name,
                scope=args.scope,
                content=content,
                kind="user_profile",
            )
        )

    extraction_operations = []
    if not has_chat_summaries:
        for conversation in CONVERSATIONS:
            result = client.beta.memory_stores.begin_update_memories(
                name=args.store_name,
                scope=args.scope,
                items=conversation,
                update_delay=0,
            ).result()
            extraction_operations.extend(getattr(result, "memory_operations", []))

    procedural_prompts = (
        "Remember this procedure: always verify live deployment evidence before reporting a Foundry demo as complete.",
        "Remember this procedure: for governed writes, create a proposal first and require explicit human confirmation before applying it.",
    )
    response_commands = []
    if not has_chat_summaries:
        openai_client = client.get_openai_client()
        for prompt in procedural_prompts:
            response = openai_client.responses.create(
                model=args.model,
                tools=[
                    {
                        "type": "memory_search_preview",
                        "memory_store_name": args.store_name,
                        "scope": args.scope,
                        "update_delay": 0,
                    }
                ],
                input=prompt,
            )
            response_commands.extend(
                item
                for item in getattr(response, "output", [])
                if str(getattr(item, "type", "")).startswith("memory_command")
                and not str(getattr(item, "type", "")).endswith("_output")
            )

    listed = list(
        client.beta.memory_stores.list_memories(
            name=args.store_name,
            scope=args.scope,
        )
    )
    search = client.beta.memory_stores.search_memories(
        name=args.store_name,
        scope=args.scope,
        items="What reporting and governed-write procedures does Tomas prefer?",
        options=MemorySearchOptions(max_memories=10),
    )
    print(
        json.dumps(
            {
                "store": args.store_name,
                "scope": args.scope,
                "owner": args.owner,
                "createdProfiles": dump(created_profiles),
                "extractionOperations": dump(extraction_operations),
                "proceduralCommands": dump(response_commands),
                "memoryCount": len(listed),
                "kinds": sorted(
                    {
                        str(getattr(item, "kind", "unknown"))
                        for item in listed
                    }
                ),
                "search": dump(search),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
