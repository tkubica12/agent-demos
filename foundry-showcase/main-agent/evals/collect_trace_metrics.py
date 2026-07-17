from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


QUERY = """
let start_time = datetime({start});
let end_time = datetime({end});
let model_calls = dependencies
| where timestamp between (start_time .. end_time)
| where name startswith "chat "
| extend input_tokens = tolong(customDimensions["gen_ai.usage.input_tokens"])
| extend output_tokens = tolong(customDimensions["gen_ai.usage.output_tokens"])
| extend cache_tokens = tolong(customDimensions["gen_ai.usage.cache_read.input_tokens"])
| project operation_Id, duration, success, input_tokens, output_tokens, cache_tokens;
let agent_requests = traces
| where timestamp between (start_time .. end_time)
| where message startswith "Inbound POST /responses completed"
| extend duration_ms = todouble(extract(@"status \\d+ in ([0-9.]+)ms", 1, message))
| project operation_Id, duration_ms;
let helper_calls = dependencies
| where timestamp between (start_time .. end_time)
| where name contains "foundry-showcase-policy-tools" or
        tostring(customDimensions["microsoft.foundry.toolbox.name"]) contains "policy"
| summarize helper_calls=count(), helper_operations=dcount(operation_Id);
union
(
    model_calls
    | summarize
        model_calls=count(),
        failed_model_calls=countif(success == false),
        input_tokens=sum(input_tokens),
        output_tokens=sum(output_tokens),
        cache_read_tokens=sum(cache_tokens),
        model_latency_p50_ms=percentile(duration, 50),
        model_latency_p95_ms=percentile(duration, 95)
),
(
    agent_requests
    | summarize
        agent_requests=count(),
        agent_latency_p50_ms=percentile(duration_ms, 50),
        agent_latency_p95_ms=percentile(duration_ms, 95)
),
(
    helper_calls
)
"""


def query_rows(app: str, resource_group: str, query: str) -> list[dict[str, Any]]:
    compact_query = " ".join(line.strip() for line in query.splitlines() if line.strip())
    command = [
        "az",
        "monitor",
        "app-insights",
        "query",
        "--app",
        app,
        "--resource-group",
        resource_group,
        "--analytics-query",
        compact_query,
        "--output",
        "json",
    ]
    executable = shutil.which(command[0])
    if executable is None:
        raise FileNotFoundError("Azure CLI was not found on PATH.")
    executable_path = Path(executable)
    if executable_path.suffix.lower() == ".cmd":
        cli_python = executable_path.parent.parent / "python.exe"
        process_command = [str(cli_python), "-IBm", "azure.cli", *command[1:]]
    else:
        process_command = [executable, *command[1:]]
    process = subprocess.run(
        process_command,
        check=False,
        text=True,
        capture_output=True,
    )
    if process.returncode:
        raise RuntimeError(process.stderr.strip() or process.stdout.strip())
    payload = json.loads(process.stdout)
    if not payload.get("tables"):
        raise RuntimeError(f"Application Insights returned no result table: {payload}")
    table = payload["tables"][0]
    columns = [column["name"] for column in table["columns"]]
    return [dict(zip(columns, row, strict=True)) for row in table["rows"]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure Hosted Agent latency, reliability, tokens, and helper use."
    )
    parser.add_argument("--app-insights", required=True)
    parser.add_argument("--resource-group", required=True)
    parser.add_argument("--start", required=True, help="UTC ISO 8601 timestamp.")
    parser.add_argument("--end", required=True, help="UTC ISO 8601 timestamp.")
    parser.add_argument("--input-usd-per-million", type=float)
    parser.add_argument("--output-usd-per-million", type=float)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = query_rows(
        args.app_insights,
        args.resource_group,
        QUERY.format(start=args.start, end=args.end),
    )
    metrics: dict[str, Any] = {}
    for row in rows:
        metrics.update({key: value for key, value in row.items() if value is not None})
    model_calls = metrics.get("model_calls") or 0
    metrics["model_failure_rate"] = (
        (metrics.get("failed_model_calls") or 0) / model_calls if model_calls else None
    )
    agent_requests = metrics.get("agent_requests") or 0
    metrics["helper_call_rate"] = (
        (metrics.get("helper_operations") or 0) / agent_requests
        if agent_requests
        else None
    )
    if (
        args.input_usd_per_million is not None
        and args.output_usd_per_million is not None
    ):
        metrics["estimated_model_cost_usd"] = (
            (metrics.get("input_tokens") or 0)
            * args.input_usd_per_million
            / 1_000_000
            + (metrics.get("output_tokens") or 0)
            * args.output_usd_per_million
            / 1_000_000
        )
    else:
        metrics["estimated_model_cost_usd"] = None
        metrics["cost_note"] = (
            "Pass the contracted regional input/output rates to calculate cost."
        )

    report = {
        "window": {"start": args.start, "end": args.end},
        "metrics": metrics,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
