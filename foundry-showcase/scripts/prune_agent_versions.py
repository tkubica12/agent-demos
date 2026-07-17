from __future__ import annotations

import argparse
import json
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported SDK result type: {type(value).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete superseded Hosted Agent versions and their sessions."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", action="append", required=True)
    args = parser.parse_args()

    client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=DefaultAzureCredential(),
    )
    result: dict[str, Any] = {}
    for agent_name in args.agent_name:
        versions = list(
            client.agents.list_versions(agent_name, limit=100, order="desc")
        )
        if not versions:
            raise RuntimeError(f"Agent {agent_name} has no deployed versions.")
        current = versions[0].version
        deleted = []
        for version in versions[1:]:
            response = client.agents.delete_version(
                agent_name,
                version.version,
                force=True,
            )
            deleted.append(as_dict(response))
        result[agent_name] = {
            "currentVersion": current,
            "deletedVersions": deleted,
        }

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
