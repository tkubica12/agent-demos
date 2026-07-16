from __future__ import annotations

import json
import os

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


EXPECTED_SKILLS = {
    "support-style",
    "escalation-policy",
    "profile-update-policy",
}
EXPECTED_TOOLS = {
    "case-read": {
        "allowed_tools": {"search_cases", "get_case", "propose_case_update"},
        "require_approval": "never",
    },
    "case-write": {
        "allowed_tools": {"apply_case_update"},
        "require_approval": "always",
    },
}


def main() -> None:
    endpoint = os.environ["FOUNDRY_PROJECT_ENDPOINT"]
    toolbox_name = os.getenv("TOOLBOX_NAME", "foundry-showcase-support")
    client = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    toolbox = client.toolboxes.get(toolbox_name)
    version_number = os.getenv("TOOLBOX_VERSION", str(toolbox.default_version))
    version = client.toolboxes.get_version(toolbox_name, version_number)

    skills = {skill.name: str(skill.version) for skill in version.skills}
    if skills.keys() != EXPECTED_SKILLS:
        raise AssertionError(f"Unexpected Toolbox skills: {skills}")

    tools = {tool.name: tool for tool in version.tools}
    if tools.keys() != EXPECTED_TOOLS.keys():
        raise AssertionError(f"Unexpected Toolbox tools: {sorted(tools)}")
    for name, expected in EXPECTED_TOOLS.items():
        tool = tools[name]
        if set(tool.allowed_tools) != expected["allowed_tools"]:
            raise AssertionError(f"{name} allowed_tools mismatch: {tool.allowed_tools}")
        if tool.require_approval != expected["require_approval"]:
            raise AssertionError(f"{name} require_approval mismatch: {tool.require_approval}")

    print(
        json.dumps(
            {
                "toolbox": toolbox_name,
                "defaultVersion": str(toolbox.default_version),
                "testedVersion": version_number,
                "tools": {
                    name: {
                        "allowedTools": sorted(tool.allowed_tools),
                        "requireApproval": tool.require_approval,
                    }
                    for name, tool in tools.items()
                },
                "skills": skills,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
