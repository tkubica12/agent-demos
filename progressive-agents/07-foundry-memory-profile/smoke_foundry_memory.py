from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    MemorySearchOptions,
    MemoryStoreDefaultDefinition,
    MemoryStoreDefaultOptions,
)
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


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


def memory_id_of(item: Any) -> str:
    memory_id = getattr(item, "memory_id", None) or getattr(item, "id", None)
    if not isinstance(memory_id, str) or not memory_id:
        raise RuntimeError(f"Memory item did not include memory_id/id: {model_dump(item)}")
    return memory_id


def retry_foundry_operation(operation, *, attempts: int = 6, delay_seconds: int = 20):
    for attempt in range(attempts):
        try:
            return operation()
        except HttpResponseError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_seconds)
    raise RuntimeError("Foundry operation did not return a result.")


def ensure_memory_store(client: AIProjectClient, name: str) -> dict[str, Any]:
    for memory_store in client.beta.memory_stores.list():
        if getattr(memory_store, "name", None) == name:
            return model_dump(memory_store)

    definition = MemoryStoreDefaultDefinition(
        chat_model=required_env("MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME"),
        embedding_model=required_env("MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME"),
        options=MemoryStoreDefaultOptions(
            chat_summary_enabled=True,
            user_profile_enabled=True,
            procedural_memory_enabled=True,
            default_ttl_seconds=30 * 24 * 60 * 60,
            user_profile_details=(
                "Store stable assistant preferences and project context. "
                "Avoid credentials, financials, precise location, and critical PII."
            ),
        ),
    )
    created = client.beta.memory_stores.create(
        name=name,
        definition=definition,
        description="Step 07 user profile, summaries, and procedural memory.",
    )
    return model_dump(created)


def main() -> None:
    memory_store_name = os.getenv("MEMORY_STORE_NAME", "step-07-memory-profile")
    scope = os.getenv("MEMORY_SCOPE_TEST", f"step07-smoke-{uuid.uuid4()}")
    chat_model = required_env("MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME")
    marker = f"step07-foundry-memory-{uuid.uuid4()}"

    client = AIProjectClient(
        endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"),
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    store_info = ensure_memory_store(client, memory_store_name)

    memory_item = retry_foundry_operation(
        lambda: client.beta.memory_stores.create_memory(
                name=memory_store_name,
                scope=scope,
                content=f"User profile marker: {marker}. User prefers concise progress reports.",
                kind="user_profile",
        )
    )
    created_item_id = memory_id_of(memory_item)
    listed = []
    for _ in range(6):
        listed = list(
            client.beta.memory_stores.list_memories(
                name=memory_store_name,
                scope=scope,
                kind="user_profile",
            )
        )
        if any(memory_id_of(item) == created_item_id for item in listed):
            break
        time.sleep(2)
    if not any(memory_id_of(item) == created_item_id for item in listed):
        raise RuntimeError("Created Foundry Memory item was not returned by list_memories.")

    updated_content = f"Updated user profile marker: {marker}. User prefers direct status."
    updated = retry_foundry_operation(
        lambda: client.beta.memory_stores.update_memory(
            name=memory_store_name,
            memory_id=created_item_id,
            content=updated_content,
        )
    )

    update_poller = retry_foundry_operation(
        lambda: client.beta.memory_stores.begin_update_memories(
            name=memory_store_name,
            scope=scope,
            items=f"In project {marker}, remember that the preferred codename is greenfield.",
            update_delay=0,
        )
    )
    update_result = update_poller.result()

    search = retry_foundry_operation(
        lambda: client.beta.memory_stores.search_memories(
            name=memory_store_name,
            scope=scope,
            items=f"What is the codename for {marker}?",
            options=MemorySearchOptions(max_memories=5),
        )
    )
    search_payload = model_dump(search)
    if marker not in json.dumps(search_payload, default=str):
        raise RuntimeError("Foundry Memory search did not return the smoke marker.")

    openai_client = client.get_openai_client()
    tools = [
        {
            "type": "memory_search_preview",
            "memory_store_name": memory_store_name,
            "scope": scope,
            "update_delay": 0,
        }
    ]
    command_items = []
    remember_response_payload: dict[str, Any] = {}
    for prompt in (
        f"Remember that my Step 07 smoke marker is {marker}.",
        f"Use the memory tool to remember exactly this fact: my Step 07 smoke marker is {marker}.",
        f"Please store this in memory now: Step 07 smoke marker = {marker}.",
    ):
        remember_response = retry_foundry_operation(
            lambda: openai_client.responses.create(
                model=chat_model,
                tools=tools,
                input=prompt,
            )
        )
        remember_response_payload = model_dump(remember_response)
        command_items = [
            item
            for item in getattr(remember_response, "output", [])
            if isinstance(getattr(item, "type", None), str)
            and getattr(item, "type").startswith("memory_command")
            and not getattr(item, "type").endswith("_output")
        ]
        if command_items:
            break
    if not command_items:
        raise RuntimeError(
            "Direct remember did not produce a memory command call. "
            f"Response output: {json.dumps(remember_response_payload, default=str)}"
        )

    time.sleep(1)
    client.beta.memory_stores.delete_memory(
        name=memory_store_name,
        memory_id=created_item_id,
    )
    deleted = False
    for _ in range(6):
        try:
            client.beta.memory_stores.get_memory(
                name=memory_store_name,
                memory_id=created_item_id,
            )
            time.sleep(2)
        except ResourceNotFoundError:
            deleted = True
            break
    if not deleted:
        raise RuntimeError("Deleted Foundry Memory item is still readable.")

    print(
        json.dumps(
            {
                "memoryStore": store_info.get("name", memory_store_name),
                "scope": scope,
                "createdItemId": created_item_id,
                "updatedItemContent": getattr(updated, "content", updated_content),
                "updateOperations": model_dump(getattr(update_result, "memory_operations", [])),
                "search": search_payload,
                "directRememberCommands": [model_dump(item) for item in command_items],
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
