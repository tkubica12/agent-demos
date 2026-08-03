"""Turn human-flagged agent runs into a Foundry evaluation dataset.

This closes the improvement loop. Reviewers mark bad answers in the reviewer app or end
users press thumbs down in the chat client; both write the same
``gen_ai.evaluation.result`` event onto the trace. This script reads those events back,
rebuilds the question and the rejected answer from the same traces, and writes an
evaluation dataset plus a pinned ``eval.yaml`` for ``azd ai agent eval run``.

Nothing is synthesised. Every case is a real conversation a human rejected.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

import httpx
from azure.identity import DefaultAzureCredential

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from telemetry import (  # noqa: E402
    TelemetryClient,
    build_evaluation_dataset,
    dataset_to_jsonl,
)

LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/.default"
SHOWCASE_DIR = Path(__file__).resolve().parents[2]
MAIN_AGENT_DIR = SHOWCASE_DIR / "main-agent"

EVAL_CONFIG = """name: {name}
agent:
    name: {agent_name}
    kind: hosted
    version: "{agent_version}"
    model: {model}
    config: .agent_configs\\baseline\\metadata.yaml
dataset:
    local_uri: datasets\\{name}\\{name}_dg.jsonl
evaluators:
    - builtin.task_adherence
    - builtin.intent_resolution
options:
    eval_model: {model}
max_samples: {max_samples}
"""


def az(*args: str) -> str:
    result = subprocess.run(
        ["az", *args], capture_output=True, text=True, shell=(sys.platform == "win32")
    )
    if result.returncode != 0:
        raise SystemExit(f"az {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def resolve_app_insights(resource_group: str, name: str) -> tuple[str, str]:
    resource_id = az(
        "resource",
        "show",
        "--resource-group",
        resource_group,
        "--name",
        name,
        "--resource-type",
        "Microsoft.Insights/components",
        "--query",
        "id",
        "-o",
        "tsv",
    )
    connection_string = az(
        "resource",
        "show",
        "--resource-group",
        resource_group,
        "--name",
        name,
        "--resource-type",
        "Microsoft.Insights/components",
        "--query",
        "properties.ConnectionString",
        "-o",
        "tsv",
    )
    return resource_id, connection_string


async def collect_cases(
    resource_id: str, connection_string: str, token: str, days: int, limit: int
) -> list[dict]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http:
        client = TelemetryClient(resource_id, connection_string, http)
        runs = await client.list_runs(token, days=days, limit=limit)
        annotations = await client.list_annotations(token, days=days)
    return build_evaluation_dataset(runs, annotations)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource-group", default="rg-foundry-showcase-si4ons")
    parser.add_argument("--app-insights-name", default="appi-foundry-showcase-vz5kj8")
    parser.add_argument("--name", default="flagged-review")
    parser.add_argument("--agent-name", default="foundry-showcase-main")
    parser.add_argument("--agent-version", default="")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    resource_id, connection_string = resolve_app_insights(
        args.resource_group, args.app_insights_name
    )
    credential = DefaultAzureCredential()
    try:
        token = credential.get_token(LOG_ANALYTICS_SCOPE).token
    finally:
        credential.close()

    cases = asyncio.run(
        collect_cases(resource_id, connection_string, token, args.days, args.limit)
    )
    if not cases:
        raise SystemExit(
            f"No answers were flagged as failing in the last {args.days} days. "
            f"Reject at least one answer in the reviewer app first."
        )

    agent_version = args.agent_version or next(
        (case["agent_version"] for case in cases if case.get("agent_version")), ""
    )
    if not agent_version:
        raise SystemExit(
            "Could not determine the agent version from the flagged traces. "
            "Pass --agent-version explicitly."
        )

    dataset_dir = MAIN_AGENT_DIR / "datasets" / args.name
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = dataset_dir / f"{args.name}_dg.jsonl"
    dataset_path.write_text(dataset_to_jsonl(cases) + "\n", encoding="utf-8")

    config_path = MAIN_AGENT_DIR / f"eval-{args.name}.yaml"
    config_path.write_text(
        EVAL_CONFIG.format(
            name=args.name,
            agent_name=args.agent_name,
            agent_version=agent_version,
            model=args.model,
            max_samples=len(cases),
        ),
        encoding="utf-8",
    )

    reviewers = sorted({case["reviewer"] for case in cases if case.get("reviewer")})
    from_users = sum(1 for case in cases if case.get("feedback_source") == "end_user")
    print(f"Flagged answers      : {len(cases)}")
    print(f"  from end users     : {from_users}")
    print(f"  from reviewers     : {len(cases) - from_users}")
    print(f"Reviewers            : {', '.join(reviewers) or 'unattributed'}")
    print(f"Agent version under test: {agent_version}")
    print(f"Dataset              : {dataset_path.relative_to(SHOWCASE_DIR)}")
    print(f"Evaluation config    : {config_path.relative_to(SHOWCASE_DIR)}")
    print()
    print("Run the evaluation with:")
    print(
        f"  azd ai agent eval run --config {config_path.name} "
        f"--name {args.name}-v{agent_version} --no-prompt -C foundry-showcase\\main-agent"
    )


if __name__ == "__main__":
    main()
