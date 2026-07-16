from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import dataclass
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


def endpoint(project: str, name: str, version: str | None = None) -> str:
    base = f"{project.rstrip('/')}/toolboxes/{name}"
    if version:
        base += f"/versions/{version}"
    return f"{base}/mcp?api-version=v1"


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


@dataclass
class ToolboxEvidence:
    endpoint: str
    protocol_version: str
    tools: list[str]
    resources: list[str]
    policy_canary: str
    skill_canary: str


def text_content(result: Any) -> str:
    items = getattr(result, "content", None)
    if items is None:
        items = getattr(result, "contents", [])
    return "\n".join(
        str(getattr(item, "text", ""))
        for item in items
        if getattr(item, "text", None)
    )


async def call_policy(
    session: ClientSession,
    tools: list[Any],
) -> str:
    direct = next((tool for tool in tools if tool.name == "lookup_support_policy"), None)
    if direct:
        return text_content(
            await session.call_tool(
                direct.name,
                {"topic": "returns"},
            )
        )

    search = next(
        (
            tool
            for tool in tools
            if "search" in tool.name.lower()
            and "tool" in tool.name.lower()
            and "query" in tool.inputSchema.get("properties", {})
        ),
        None,
    )
    caller = next(
        (
            tool
            for tool in tools
            if "call" in tool.name.lower() and "tool" in tool.name.lower()
        ),
        None,
    )
    if search is None or caller is None:
        raise RuntimeError("Toolbox did not expose direct policy access or discovery meta-tools.")

    search_result = await session.call_tool(
        search.name,
        {"query": "lookup_support_policy read Contoso Outdoors support policy"},
    )
    search_text = text_content(search_result)
    if "lookup_support_policy" not in search_text:
        search_result = await session.call_tool(
            search.name,
            {"query": "lookup_support_policy"},
        )
        search_text = text_content(search_result)
    payload = json.loads(search_text)
    candidates = payload if isinstance(payload, list) else payload.get("tools", payload.get("results", []))
    discovered = next(
        item
        for item in candidates
        if "lookup_support_policy" in str(item.get("name", item.get("tool_name", "")))
    )
    opaque_name = discovered.get("name") or discovered.get("tool_name")
    properties = caller.inputSchema.get("properties", {})
    name_key = next(key for key in properties if "name" in key.lower())
    args_key = next(key for key in properties if key.lower() in {"arguments", "args", "input"})
    return text_content(
        await session.call_tool(
            caller.name,
            {name_key: opaque_name, args_key: {"topic": "returns"}},
        )
    )


async def inspect_endpoint(url: str) -> ToolboxEvidence:
    credential = DefaultAzureCredential()
    token = credential.get_token(AI_SCOPE).token
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with streamablehttp_client(
            url,
            headers=headers,
            terminate_on_close=False,
        ) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools_result = await session.list_tools()
                resources_result = await session.list_resources()
                resources = [str(resource.uri) for resource in resources_result.resources]
                policy = await call_policy(session, tools_result.tools)
                if "RET-CANARY-9031" not in policy:
                    raise RuntimeError("Policy call did not return the checked-in return-policy canary.")
                index = await session.read_resource("skill://index.json")
                index_text = text_content(index)
                if "escalation-policy" not in index_text:
                    raise RuntimeError("Pinned escalation skill is missing from skill://index.json.")
                matrix_uri = next(
                    (
                        uri
                        for uri in resources
                        if uri.endswith("/references/escalation-matrix.md")
                    ),
                    None,
                )
                if matrix_uri is None:
                    raise RuntimeError("Pinned escalation matrix resource is missing.")
                matrix = await session.read_resource(matrix_uri)
                matrix_text = text_content(matrix)
                canaries = [
                    value
                    for value in ("ESC-CANARY-7742", "ESC-CANARY-9921")
                    if value in matrix_text
                ]
                if len(canaries) != 1:
                    raise RuntimeError(
                        "Escalation matrix must expose exactly one release canary."
                    )
                return ToolboxEvidence(
                    endpoint=url,
                    protocol_version=initialized.protocolVersion,
                    tools=[tool.name for tool in tools_result.tools],
                    resources=resources,
                    policy_canary="RET-CANARY-9031",
                    skill_canary=canaries[0],
                )
    finally:
        credential.close()


def publish(args: argparse.Namespace) -> dict[str, Any]:
    credential = DefaultAzureCredential()
    project_endpoint = required_env("FOUNDRY_PROJECT_ENDPOINT")
    project = AIProjectClient(endpoint=project_endpoint, credential=credential)
    previous = project.toolboxes.get(args.toolbox).default_version
    candidate_url = endpoint(project_endpoint, args.toolbox, args.version)
    try:
        candidate = asyncio.run(inspect_endpoint(candidate_url))
        if candidate.skill_canary != args.expected_canary:
            raise RuntimeError(
                f"Candidate returned {candidate.skill_canary}, expected {args.expected_canary}."
            )
        project.toolboxes.update(args.toolbox, default_version=args.version)
        consumer = asyncio.run(inspect_endpoint(endpoint(project_endpoint, args.toolbox)))
        if consumer.skill_canary != args.expected_canary:
            raise RuntimeError("Unversioned consumer endpoint did not resolve the promoted version.")
        return {
            "previous": previous,
            "published": args.version,
            "candidate": candidate.__dict__,
            "consumer": consumer.__dict__,
        }
    except Exception:
        project.toolboxes.update(args.toolbox, default_version=previous)
        raise
    finally:
        project.close()
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

    promote = subparsers.add_parser("promote")
    promote.add_argument("--toolbox", required=True)
    promote.add_argument("--version", required=True)
    promote.add_argument("--expected-canary", required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--endpoint", required=True)

    args = parser.parse_args()
    if args.command == "create":
        result = create_versions(args)
    elif args.command == "promote":
        result = publish(args)
    else:
        result = asyncio.run(inspect_endpoint(args.endpoint)).__dict__
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
