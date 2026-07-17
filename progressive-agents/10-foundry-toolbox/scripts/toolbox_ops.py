from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


AI_SCOPE = "https://ai.azure.com/.default"


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set.")
    return value


def create_versions(args: argparse.Namespace) -> dict[str, Any]:
    credential = DefaultAzureCredential()
    project = AIProjectClient(endpoint=required_env("FOUNDRY_PROJECT_ENDPOINT"), credential=credential)
    try:
        try:
            current = project.toolboxes.get(args.toolbox)
            existing = list(project.toolboxes.list_versions(args.toolbox))
        except ResourceNotFoundError:
            current = None
            existing = []
        created = current is None

        v1 = next(
            (version for version in existing if version.metadata.get("release") == "1"),
            None,
        )
        if v1 is None:
            if current is not None:
                raise RuntimeError(
                    f"Existing toolbox {args.toolbox} has no managed release-1 version."
                )
            v1 = project.toolboxes.create_version(
                name=args.toolbox,
                body={
                "description": "Contoso support policy toolbox release 1.",
                "tools": [
                    {
                        "type": "mcp",
                        "server_label": "contoso_support_policy",
                        "server_url": args.mcp_endpoint,
                        "project_connection_id": args.connection_id,
                        "allowed_tools": ["lookup_support_policy"],
                        "require_approval": "never",
                        "defer_loading": True,
                        "server_description": "Read-only Contoso Outdoors support policies.",
                    },
                    {"type": "toolbox_search_preview"},
                ],
                "skills": [
                    {
                        "type": "skill_reference",
                        "name": args.skill,
                        "version": args.skill_v1,
                    }
                ],
                "metadata": {"release": "1", "business_tools": "lookup_support_policy"},
                },
            )
            project.toolboxes.update(args.toolbox, default_version=v1.version)

        v2 = next(
            (version for version in existing if version.metadata.get("release") == "2"),
            None,
        )
        if v2 is None:
            v2 = project.toolboxes.create_version(
                name=args.toolbox,
                body={
                "description": "Contoso support policy toolbox release 2.",
                "tools": [
                    {
                        "type": "mcp",
                        "server_label": "contoso_support_policy",
                        "server_url": args.mcp_endpoint,
                        "project_connection_id": args.connection_id,
                        "allowed_tools": ["lookup_support_policy"],
                        "require_approval": "never",
                        "defer_loading": True,
                        "server_description": "Read-only Contoso Outdoors support policies.",
                    },
                    {"type": "toolbox_search_preview"},
                ],
                "skills": [
                    {
                        "type": "skill_reference",
                        "name": args.skill,
                        "version": args.skill_v2,
                    }
                ],
                "metadata": {
                    "release": "2",
                    "change": "prolonged-delivery escalation",
                    "business_tools": "lookup_support_policy",
                },
                },
            )
        for version, expected_skill_version in (
            (v1, args.skill_v1),
            (v2, args.skill_v2),
        ):
            references = [
                skill
                for skill in version.skills
                if skill.get("name") == args.skill
            ]
            if len(references) != 1 or references[0].get("version") != expected_skill_version:
                raise RuntimeError(
                    f"Toolbox version {version.version} does not pin "
                    f"{args.skill}@{expected_skill_version}."
                )
        current = project.toolboxes.get(args.toolbox)
        return {
            "v1": v1.version,
            "v2": v2.version,
            "default": current.default_version,
            "created": created,
        }
    finally:
        project.close()
        credential.close()


def text_content(result: Any) -> str:
    items = getattr(result, "content", None)
    if items is None:
        items = getattr(result, "contents", [])
    return "\n".join(
        str(getattr(item, "text", ""))
        for item in items
        if getattr(item, "text", None)
    )


async def inspect_skill_endpoint(url: str) -> dict[str, Any]:
    credential = DefaultAzureCredential()
    token = credential.get_token(AI_SCOPE).token
    try:
        async with streamablehttp_client(
            url,
            headers={"Authorization": f"Bearer {token}"},
            terminate_on_close=False,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                resources_result = await session.list_resources()
                skill_uri = next(
                    (
                        str(resource.uri)
                        for resource in resources_result.resources
                        if str(resource.uri).endswith("/SKILL.md")
                    ),
                    None,
                )
                if skill_uri is None:
                    raise RuntimeError("Toolbox SKILL.md resource is missing.")
                skill = await session.read_resource(skill_uri)
                skill_text = text_content(skill)
                canaries = [
                    value
                    for value in ("ESC-CANARY-7742", "ESC-CANARY-9921")
                    if value in skill_text
                ]
                if len(canaries) != 1:
                    raise RuntimeError("Toolbox skill must expose exactly one release canary.")
                return {
                    "endpoint": url,
                    "protocol_version": initialized.protocolVersion,
                    "resources": [str(resource.uri) for resource in resources_result.resources],
                    "skill_canary": canaries[0],
                }
    finally:
        credential.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create")
    create.add_argument("--toolbox", required=True)
    create.add_argument("--mcp-endpoint", required=True)
    create.add_argument("--connection-id", required=True)
    create.add_argument("--skill", required=True)
    create.add_argument("--skill-v1", required=True)
    create.add_argument("--skill-v2", required=True)

    inspect_skill_parser = subparsers.add_parser("inspect-skill")
    inspect_skill_parser.add_argument("--endpoint", required=True)

    args = parser.parse_args()
    if args.command == "create":
        result = create_versions(args)
    else:
        result = asyncio.run(inspect_skill_endpoint(args.endpoint))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
