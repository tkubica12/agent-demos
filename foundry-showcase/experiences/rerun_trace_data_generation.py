from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    DataGenerationJob,
    DataGenerationJobInputs,
    DataGenerationJobOutputOptions,
    DataGenerationJobScenario,
    JobStatus,
    TracesDataGenerationJobOptions,
    TracesDataGenerationJobSource,
)
from azure.identity import DefaultAzureCredential


def dump(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--agent-name", default="foundry-showcase-main")
    parser.add_argument("--agent-id")
    parser.add_argument("--agent-version")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--max-samples", type=int, default=100)
    parser.add_argument("--timeout-minutes", type=int, default=20)
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")

    now = datetime.now(UTC)
    output_name = f"foundry-showcase-traces-{now:%Y%m%d-%H%M}"
    client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=DefaultAzureCredential(),
        allow_preview=True,
    )
    request = DataGenerationJob(
        inputs=DataGenerationJobInputs(
            name=f"foundry-showcase-traces-{now:%Y%m%d-%H%M}",
            scenario=DataGenerationJobScenario.EVALUATION,
            sources=[
                TracesDataGenerationJobSource(
                    start_time=now - timedelta(days=args.lookback_days),
                    description="Live Foundry Showcase Hosted Agent traces.",
                    **(
                        {"agent_id": args.agent_id}
                        if args.agent_id
                        else {"agent_name": args.agent_name}
                    ),
                    **(
                        {"agent_version": args.agent_version}
                        if args.agent_version
                        else {}
                    ),
                )
            ],
            options=TracesDataGenerationJobOptions(
                max_samples=args.max_samples,
            ),
            output_options=DataGenerationJobOutputOptions(name=output_name),
        )
    )
    job = client.beta.datasets.create_generation_job(job=request)
    deadline = time.monotonic() + args.timeout_minutes * 60
    terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
    while job.status not in terminal:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Data generation job {job.id} did not finish in time.")
        time.sleep(10)
        job = client.beta.datasets.get_generation_job(job_id=job.id)

    payload = dump(job)
    print(json.dumps(payload, indent=2, default=str))
    if job.status != JobStatus.SUCCEEDED:
        raise RuntimeError(f"Trace data generation ended with status {job.status}.")
    generated = getattr(getattr(job, "result", None), "generated_samples", 0)
    if not isinstance(generated, int) or generated < 1:
        raise RuntimeError("Trace data generation succeeded but produced no samples.")


if __name__ == "__main__":
    main()
