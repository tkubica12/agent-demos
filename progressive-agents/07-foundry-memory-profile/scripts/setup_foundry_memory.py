from __future__ import annotations

import argparse
import json
import os
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MemoryStoreDefaultDefinition, MemoryStoreDefaultOptions
from azure.identity import DefaultAzureCredential


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {
        key: item
        for key, item in vars(value).items()
        if not key.startswith("_")
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default=os.getenv("MEMORY_STORE_NAME", "step-07-memory-profile"))
    parser.add_argument("--ttl-days", type=int, default=30)
    args = parser.parse_args()

    project_client = AIProjectClient(
        endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    existing = [
        store
        for store in project_client.beta.memory_stores.list()
        if getattr(store, "name", None) == args.name
    ]
    if existing:
        print(json.dumps(model_dump(existing[0]), indent=2, default=str))
        return

    options = MemoryStoreDefaultOptions(
        chat_summary_enabled=True,
        user_profile_enabled=True,
        procedural_memory_enabled=True,
        default_ttl_seconds=args.ttl_days * 24 * 60 * 60,
        user_profile_details=(
            "Store stable assistant preferences and project context. "
            "Avoid credentials, financials, precise location, and critical PII."
        ),
    )
    definition = MemoryStoreDefaultDefinition(
        chat_model=required_env("MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME"),
        embedding_model=required_env("MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME"),
        options=options,
    )
    memory_store = project_client.beta.memory_stores.create(
        name=args.name,
        definition=definition,
        description="Step 07 user profile, summaries, direct memory, and procedural memory.",
    )
    print(json.dumps(model_dump(memory_store), indent=2, default=str))


if __name__ == "__main__":
    main()
