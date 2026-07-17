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

PROCEDURAL_MEMORIES = (
    {
        "applicable_to": "Reporting whether a Foundry showcase deployment is complete.",
        "instruction": (
            "Verify live deployment evidence before reporting the demo as complete, "
            "and state unsupported preview behavior explicitly."
        ),
    },
    {
        "applicable_to": "Applying a governed support-case write.",
        "instruction": (
            "Create a noncommitted proposal first and require explicit human "
            "confirmation before applying the change."
        ),
    },
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
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")
    if not args.scope:
        parser.error(
            "--scope or MEMORY_SCOPE is required; use <tenant-id>_<object-id> "
            "to match the deployed AG-UI identity."
        )

    credential = DefaultAzureCredential(process_timeout=60)
    client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
        allow_preview=True,
    )
    if args.replace:
        client.beta.memory_stores.delete_scope(
            name=args.store_name,
            scope=args.scope,
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

    created_procedures = []
    for procedure in PROCEDURAL_MEMORIES:
        content = json.dumps(procedure, separators=(",", ":"))
        if content in existing_content:
            continue
        created_procedures.append(
            client.beta.memory_stores.create_memory(
                name=args.store_name,
                scope=args.scope,
                content=content,
                kind="procedural",
            )
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
                "createdProcedures": dump(created_procedures),
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
    client.close()
    credential.close()


if __name__ == "__main__":
    main()
