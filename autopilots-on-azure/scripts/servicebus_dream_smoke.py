from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import PLATFORM_DIR, terraform_output
from scripts.user_schedule_smoke import (
    az_json,
    require_awake_gateway,
    request_with_retry,
    runtime_state,
    wait_until,
)


def system_job(diagnostics: dict[str, Any]) -> dict[str, Any]:
    jobs = [job for job in diagnostics["jobs"] if job.get("name") == "Platform Dreaming"]
    if len(jobs) != 1:
        raise RuntimeError(f"Expected exactly one Platform Dreaming job; found {len(jobs)}.")
    return jobs[0]


def schedule_definition(job: dict[str, Any]) -> dict[str, Any]:
    required = ("id", "schedule")
    if any(not job.get(key) for key in required):
        raise ValueError("Platform Dreaming diagnostics are missing the live schedule.")
    definition = {key: job.get(key) for key in (*required, "enabled", "paused", "systemType")}
    repeat = job.get("repeat")
    if repeat is not None and not isinstance(repeat, dict):
        raise ValueError("Platform Dreaming diagnostics contain an invalid repeat policy.")
    definition["repeat"] = {"times": repeat.get("times")} if repeat is not None else None
    return definition


def completed_receipt(receipt: dict[str, Any]) -> bool:
    if receipt.get("state") == "failed" or (receipt.get("state") == "completed" and receipt.get("success") is not True):
        raise RuntimeError(f"Queue-driven Dreaming failed: {receipt!r}")
    return receipt.get("state") == "completed"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run an ad-hoc queue-driven Dreaming occurrence without modifying its production schedule."
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()

    tfvars = json.loads(
        runtime_app_tfvars_path("hermes", args.state_name).read_text(
            encoding="utf-8"
        )
    )
    outputs = json.loads(
        runtime_outputs_path("hermes", args.state_name).read_text(
            encoding="utf-8"
        )
    )
    platform = terraform_output(PLATFORM_DIR)
    if not outputs.get("servicebus_dream_enabled"):
        raise RuntimeError("Queue-driven Dreaming is not enabled.")

    key = str(tfvars["api_server_key"])
    bridge_url = str(outputs["bridge_url"]).rstrip("/")
    operator_headers = {"X-Autopilot-Key": key}
    resource_group = str(platform["resource_group_name"])
    namespace = str(platform["scheduler_servicebus_namespace_name"])
    queue = str(outputs["scheduler_servicebus_queue_name"])
    gateway_before = require_awake_gateway(outputs)
    job_id = ""
    gateway_url = ""

    def queue_counts() -> dict[str, Any]:
        return az_json(
            "servicebus", "queue", "show", "--resource-group", resource_group,
            "--namespace-name", namespace, "--name", queue, "--query", "countDetails",
        )

    counts_before = queue_counts()

    with httpx.Client(timeout=900) as client:
        runtime = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/internal/runtime/ensure",
            headers=operator_headers,
            timeout=300,
        )
        runtime.raise_for_status()
        gateway_url = str(runtime.json()["gatewayUrl"]).rstrip("/")
        diagnostics = client.get(
            f"{gateway_url}/internal/cron/diagnostics",
            headers=operator_headers,
            timeout=120,
        )
        diagnostics.raise_for_status()
        job = system_job(diagnostics.json())
        schedule_before = schedule_definition(job)
        job_id = str(job["id"])
        enqueued = client.post(
            f"{gateway_url}/internal/cron/system/run-now",
            headers=operator_headers,
            json={},
            timeout=120,
        )
        enqueued.raise_for_status()
        revision = str(enqueued.json()["revision"])
        occurrence_id = str(enqueued.json()["occurrenceId"])

        def receipt_probe() -> dict[str, Any]:
            nonlocal gateway_url
            runtime_response = request_with_retry(
                client,
                "POST",
                f"{bridge_url}/internal/runtime/ensure",
                headers=operator_headers,
                attempts=2,
                timeout=120,
            )
            runtime_response.raise_for_status()
            current_gateway = str(
                runtime_response.json()["gatewayUrl"]
            ).rstrip("/")
            gateway_url = current_gateway
            response = client.get(
                f"{current_gateway}/internal/cron/diagnostics",
                headers=operator_headers,
                timeout=120,
            )
            response.raise_for_status()
            return next(
                (
                    receipt
                    for receipt in response.json()["systemReceipts"]
                    if receipt.get("jobId") == job_id
                    and receipt.get("revision") == revision
                    and receipt.get("occurrenceId") == occurrence_id
                ),
                {"state": "pending"},
            )

        receipt = wait_until(
            receipt_probe,
            completed_receipt,
            timeout=args.timeout,
            interval=15,
        )
        if receipt.get("success") is not True:
            raise RuntimeError(
                f"Queue-driven Dreaming failed: {receipt!r}"
            )
        result = {
            "ok": True,
            "workerId": outputs["worker_id"],
            "jobId": job_id,
            "gatewayBefore": gateway_before,
            "gatewayAfter": require_awake_gateway(outputs),
            "runtimeStateAfter": runtime_state(outputs),
            "receipt": receipt,
        }

        counts_after = queue_counts()
        if int(counts_after.get("deadLetterMessageCount") or 0) > int(counts_before.get("deadLetterMessageCount") or 0):
            raise RuntimeError("A new dead-letter message appeared during the Dreaming smoke.")
        result["queueCounts"] = counts_after
        observed = client.get(
            f"{gateway_url}/internal/cron/diagnostics", headers=operator_headers, timeout=120,
        )
        observed.raise_for_status()
        schedule_after = schedule_definition(system_job(observed.json()))
        if schedule_before != schedule_after:
            raise RuntimeError("The live production Dreaming schedule changed during the smoke; no schedule has been overwritten.")
        result["productionScheduleUnchanged"] = True
        result["productionSchedule"] = schedule_after
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
