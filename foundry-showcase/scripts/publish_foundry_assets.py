from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPToolboxTool, ToolboxSkillReference
from azure.identity import DefaultAzureCredential


SHOWCASE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SHOWCASE_ROOT / "main-agent" / "skills" / "base"
SKILL_NAMES = (
    "support-style",
    "escalation-policy",
    "profile-update-policy",
)


def run(command: list[str]) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=SHOWCASE_ROOT, check=True)


def publish_skills(
    client: AIProjectClient,
    project_endpoint: str,
    update_existing: bool,
) -> dict[str, str]:
    existing = {skill.name: skill for skill in client.beta.skills.list()}
    versions: dict[str, str] = {}
    for name in SKILL_NAMES:
        skill_path = SKILLS_ROOT / name
        if name not in existing:
            action = "create"
        elif update_existing:
            action = "update"
        else:
            action = ""
        if action:
            run(
                [
                    "azd",
                    "ai",
                    "skill",
                    action,
                    name,
                    "--file",
                    str(skill_path),
                    "--project-endpoint",
                    project_endpoint,
                    "--output",
                    "json",
                    "--no-prompt",
                ]
            )
        details = client.beta.skills.get(name=name)
        versions[name] = str(details.default_version)
    return versions


def create_toolbox_version(
    client: AIProjectClient,
    toolbox_name: str,
    mcp_endpoint: str,
    connection_name: str,
    skill_versions: dict[str, str],
):
    return client.toolboxes.create_version(
        name=toolbox_name,
        description=(
            "Governed support-case MCP tools and immutable support skills for "
            "the Foundry Showcase."
        ),
        tools=[
            MCPToolboxTool(
                name="case-read",
                server_label="case-read",
                server_url=mcp_endpoint,
                server_description="Read support cases and create noncommitted update proposals.",
                allowed_tools=["search_cases", "get_case", "propose_case_update"],
                require_approval="never",
                project_connection_id=connection_name,
            ),
            MCPToolboxTool(
                name="case-write",
                server_label="case-write",
                server_url=mcp_endpoint,
                server_description="Apply one explicitly confirmed support-case update proposal.",
                allowed_tools=["apply_case_update"],
                require_approval="always",
                project_connection_id=connection_name,
            ),
        ],
        skills=[
            ToolboxSkillReference(name=name, version=version)
            for name, version in skill_versions.items()
        ],
        metadata={
            "scenario": "foundry-showcase",
            "governance": "read-write-split",
        },
    )


def model_dump(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--mcp-endpoint")
    parser.add_argument("--connection-name", default="foundry-showcase-case-mcp")
    parser.add_argument("--toolbox-name", default="foundry-showcase-support")
    parser.add_argument("--toolbox-version")
    parser.add_argument("--update-skills", action="store_true")
    parser.add_argument("--new-toolbox-version", action="store_true")
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--output-file", type=Path)
    args = parser.parse_args()

    client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    skill_versions = publish_skills(client, args.project_endpoint, args.update_skills)
    existing_toolboxes = {toolbox.name: toolbox for toolbox in client.toolboxes.list()}
    existing = existing_toolboxes.get(args.toolbox_name)
    if args.toolbox_version and args.new_toolbox_version:
        parser.error("--toolbox-version and --new-toolbox-version cannot be combined.")
    if existing is None or args.new_toolbox_version:
        if not args.mcp_endpoint:
            parser.error("--mcp-endpoint is required when creating a Toolbox version.")
        version = create_toolbox_version(
            client,
            args.toolbox_name,
            args.mcp_endpoint,
            args.connection_name,
            skill_versions,
        )
        version_id = str(version.version)
    elif args.toolbox_version:
        version_id = args.toolbox_version
        version = client.toolboxes.get_version(args.toolbox_name, version_id)
    else:
        version_id = str(existing.default_version)
        version = client.toolboxes.get_version(args.toolbox_name, version_id)

    if args.promote:
        toolbox = client.toolboxes.update(
            name=args.toolbox_name,
            default_version=version_id,
        )
    else:
        toolbox = client.toolboxes.get(args.toolbox_name)

    endpoint = args.project_endpoint.rstrip("/")
    payload = {
        "toolbox": args.toolbox_name,
        "createdOrSelectedVersion": version_id,
        "defaultVersion": str(toolbox.default_version),
        "versionedEndpoint": (
            f"{endpoint}/toolboxes/{args.toolbox_name}/versions/{version_id}/mcp?api-version=v1"
        ),
        "defaultEndpoint": (
            f"{endpoint}/toolboxes/{args.toolbox_name}/mcp?api-version=v1"
        ),
        "skillVersions": skill_versions,
        "version": model_dump(version),
    }
    rendered = json.dumps(payload, indent=2, default=str)
    if args.output_file:
        args.output_file.parent.mkdir(parents=True, exist_ok=True)
        args.output_file.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
