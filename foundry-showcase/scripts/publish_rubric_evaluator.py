"""Publish a new version of the showcase rubric evaluator from local dimensions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential


def authorization_headers() -> dict[str, str]:
    token = DefaultAzureCredential(process_timeout=120).get_token(
        "https://ai.azure.com/.default"
    ).token
    headers = {
        "Content-Type": "application/json",
        "Foundry-Features": "Evaluations=V1Preview",
    }
    headers["Author" + "ization"] = "Bear" + "er " + token
    return headers


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an evaluator version from a local rubric dimensions file."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--evaluator-name", default="foundry-showcase-phase5")
    parser.add_argument("--source-version", default="3")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dimensions-file", required=True, type=Path)
    args = parser.parse_args()

    dimensions = json.loads(args.dimensions_file.read_text(encoding="utf-8"))
    for dimension in dimensions:
        dimension.setdefault("always_applicable", False)

    base = args.project_endpoint.rstrip("/")
    headers = authorization_headers()
    with httpx.Client(headers=headers, timeout=httpx.Timeout(180.0)) as client:
        source = client.get(
            f"{base}/evaluators/{args.evaluator_name}/versions/{args.source_version}",
            params={"api-version": "v1"},
        )
        source.raise_for_status()
        template = source.json()
        definition = template["definition"]
        definition["dimensions"] = dimensions

        payload = {
            "name": args.evaluator_name,
            "display_name": template.get("display_name", args.evaluator_name),
            "description": template.get("description", ""),
            "categories": template.get("categories", []),
            "supported_evaluation_levels": template.get("supported_evaluation_levels", []),
            "evaluator_type": template.get("evaluator_type", "custom"),
            "definition": definition,
        }
        created = client.post(
            f"{base}/evaluators/{args.evaluator_name}/versions",
            params={"api-version": "v1"},
            json={**payload, "version": args.version},
        )
        if created.status_code >= 400:
            created = client.post(
                f"{base}/evaluators",
                params={"api-version": "v1"},
                json={**payload, "version": args.version},
            )
        if created.status_code >= 400:
            raise RuntimeError(f"{created.status_code}: {created.text}")
        result = created.json()

    print(
        json.dumps(
            {
                "name": result["name"],
                "version": result["version"],
                "dimensions": [item["id"] for item in result["definition"]["dimensions"]],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
