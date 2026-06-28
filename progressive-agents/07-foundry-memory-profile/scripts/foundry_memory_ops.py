from __future__ import annotations

import argparse
import json
import os
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MemorySearchOptions
from azure.identity import DefaultAzureCredential


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def project_client() -> AIProjectClient:
    return AIProjectClient(
        endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
    )


def model_dump(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return [model_dump(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: model_dump(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def print_json(value: Any) -> None:
    print(json.dumps(model_dump(value), indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "operation",
        choices=[
            "list-stores",
            "create-item",
            "get-item",
            "list-items",
            "delete-item",
            "update-from-text",
            "search",
        ],
    )
    parser.add_argument("--store", default=os.getenv("MEMORY_STORE_NAME", "step-07-memory-profile"))
    parser.add_argument("--scope", default=os.getenv("MEMORY_SCOPE", "local-user"))
    parser.add_argument("--content")
    parser.add_argument("--kind", default="user_profile")
    parser.add_argument("--memory-id")
    parser.add_argument("--query")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    client = project_client()
    memory_stores = client.beta.memory_stores

    if args.operation == "list-stores":
        print_json(list(memory_stores.list()))
    elif args.operation == "create-item":
        if not args.content:
            raise RuntimeError("--content is required.")
        print_json(
            memory_stores.create_memory(
                name=args.store,
                scope=args.scope,
                content=args.content,
                kind=args.kind,
            )
        )
    elif args.operation == "get-item":
        if not args.memory_id:
            raise RuntimeError("--memory-id is required.")
        print_json(memory_stores.get_memory(name=args.store, memory_id=args.memory_id))
    elif args.operation == "list-items":
        print_json(list(memory_stores.list_memories(name=args.store, scope=args.scope)))
    elif args.operation == "delete-item":
        if not args.memory_id:
            raise RuntimeError("--memory-id is required.")
        print_json(memory_stores.delete_memory(name=args.store, memory_id=args.memory_id))
    elif args.operation == "update-from-text":
        if not args.content:
            raise RuntimeError("--content is required.")
        poller = memory_stores.begin_update_memories(
            name=args.store,
            scope=args.scope,
            items=[{"role": "user", "content": args.content, "type": "message"}],
            update_delay=0,
        )
        print_json(poller.result())
    elif args.operation == "search":
        items = None
        if args.query:
            items = [{"role": "user", "content": args.query, "type": "message"}]
        print_json(
            memory_stores.search_memories(
                name=args.store,
                scope=args.scope,
                items=items,
                options=MemorySearchOptions(max_memories=args.limit),
            )
        )


if __name__ == "__main__":
    main()
