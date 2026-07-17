from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


TERMINAL_STATES = {"cancelled", "failed", "succeeded"}
FILE_READY_STATES = {"processed"}
FILE_FAILED_STATES = {"cancelled", "deleted", "error", "failed"}
DEFAULT_DATA_DIR = Path(__file__).with_name("data")


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def wait_for_file(openai_client: Any, file_id: str) -> Any:
    deadline = time.monotonic() + 600
    while True:
        uploaded_file = openai_client.files.retrieve(file_id)
        if uploaded_file.status in FILE_READY_STATES:
            return uploaded_file
        if uploaded_file.status in FILE_FAILED_STATES:
            raise RuntimeError(
                f"Training file {file_id} ended with status {uploaded_file.status}."
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Training file {file_id} was not processed in time.")
        time.sleep(5)


def upload_jsonl(openai_client: Any, path: Path) -> Any:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 5:
        raise ValueError(f"{path} needs at least five examples.")
    for line in lines:
        payload = json.loads(line)
        if not isinstance(payload.get("messages"), list):
            raise ValueError(f"{path} contains an invalid conversational example.")
    content = ("\n".join(lines) + "\n").encode("utf-8-sig")
    existing_files = [
        item
        for item in openai_client.files.list(purpose="fine-tune", limit=100).data
        if item.filename == path.name and item.status in FILE_READY_STATES
    ]
    for existing_file in sorted(
        existing_files,
        key=lambda item: item.created_at,
        reverse=True,
    ):
        existing_content = openai_client.files.content(existing_file.id).read()
        if existing_content.decode("utf-8-sig").splitlines() == lines:
            return existing_file
    uploaded_file = openai_client.files.create(
        file=(path.name, content, "application/jsonl"),
        purpose="fine-tune",
    )
    return wait_for_file(openai_client, uploaded_file.id)


def find_reusable_job(
    openai_client: Any,
    model: str,
    suffix: str,
    training_file_id: str,
    validation_file_id: str,
) -> Any | None:
    jobs = list(openai_client.fine_tuning.jobs.list(limit=100).data)
    matches = [
        job
        for job in jobs
        if job.model.casefold() == model.casefold()
        and getattr(job, "suffix", None) == suffix
        and job.training_file == training_file_id
        and job.validation_file == validation_file_id
        and job.status not in {"cancelled", "failed"}
    ]
    return max(matches, key=lambda job: job.created_at) if matches else None


def wait_for_job(
    openai_client: Any,
    job_id: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while True:
        job = openai_client.fine_tuning.jobs.retrieve(job_id)
        if job.status in TERMINAL_STATES:
            return job
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Fine-tuning job {job_id} did not finish in time.")
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run or reuse the bounded Qwen support-style SFT demonstration."
    )
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument(
        "--training-file",
        type=Path,
        default=DEFAULT_DATA_DIR / "qwen_support_style_train.jsonl",
    )
    parser.add_argument(
        "--validation-file",
        type=Path,
        default=DEFAULT_DATA_DIR / "qwen_support_style_validation.jsonl",
    )
    parser.add_argument("--model", default="qwen3-32b")
    parser.add_argument("--suffix", default="foundry-showcase")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--force-new", action="store_true")
    args = parser.parse_args()

    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")

    credential = DefaultAzureCredential(process_timeout=60)
    project_client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=credential,
    )
    openai_client = project_client.get_openai_client()
    training_file = upload_jsonl(openai_client, args.training_file)
    validation_file = upload_jsonl(openai_client, args.validation_file)
    uploaded_files = {
        "training": training_file.id,
        "validation": validation_file.id,
    }
    job = None if args.force_new else find_reusable_job(
        openai_client,
        args.model,
        args.suffix,
        training_file.id,
        validation_file.id,
    )
    if job is None:
        job = openai_client.fine_tuning.jobs.create(
            training_file=training_file.id,
            validation_file=validation_file.id,
            model=args.model,
            suffix=args.suffix,
            seed=42,
            method={
                "type": "supervised",
                "supervised": {"hyperparameters": {"n_epochs": 1}},
            },
            extra_body={"trainingType": "GlobalStandard"},
        )

    job = wait_for_job(
        openai_client,
        job.id,
        args.poll_seconds,
        args.timeout_seconds,
    )
    events = list(
        openai_client.fine_tuning.jobs.list_events(
            fine_tuning_job_id=job.id,
            limit=20,
        ).data
    )
    result = {
        "job": json_value(job),
        "uploadedFiles": uploaded_files,
        "recentEvents": [json_value(event) for event in events],
        "hostingDeploymentCreated": False,
        "hostingCost": "none",
    }

    if job.status == "succeeded" and job.result_files and args.result_file:
        args.result_file.parent.mkdir(parents=True, exist_ok=True)
        args.result_file.write_bytes(
            openai_client.files.content(job.result_files[0]).read()
        )
        result["resultFile"] = str(args.result_file)

    print(json.dumps(result, indent=2, default=str))
    project_client.close()
    credential.close()

    if job.status != "succeeded":
        raise RuntimeError(f"Fine-tuning job {job.id} ended with status {job.status}.")


if __name__ == "__main__":
    main()
