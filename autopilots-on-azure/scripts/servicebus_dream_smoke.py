from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import PLATFORM_DIR, terraform_output
from scripts.user_schedule_smoke import (
    az_json,
    az_value,
    request_with_retry,
    wait_until,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one real queue-driven Dreaming occurrence and restore its schedule."
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--due-seconds", type=int, default=180)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.due_seconds < 120:
        raise ValueError("--due-seconds must be at least 120.")

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
    bridge_app = str(outputs["bridge_app_name"])
    job_id = ""
    gateway_url = ""

    def replica_count() -> int:
        return len([
            line
            for line in az_value(
                "containerapp",
                "replica",
                "list",
                "--resource-group",
                resource_group,
                "--name",
                bridge_app,
                "--query",
                "[].name",
            ).splitlines()
            if line.strip()
        ])

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
        job = next(
            item
            for item in diagnostics.json()["jobs"]
            if item.get("name") == "Platform Dreaming"
        )
        job_id = str(job["id"])
        wait_until(
            replica_count,
            lambda count: count == 0,
            timeout=max(120, args.due_seconds),
        )
        enqueued = client.post(
            f"{gateway_url}/internal/cron/system/run-now",
            headers=operator_headers,
            json={},
            timeout=120,
        )
        enqueued.raise_for_status()
        revision = str(enqueued.json()["revision"])
        occurrence_id = str(enqueued.json()["occurrenceId"])
        wait_until(
            replica_count,
            lambda count: count > 0,
            timeout=120,
        )

        def receipt_probe() -> dict[str, Any]:
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
            lambda value: value.get("state") == "completed",
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
            "bridgeScaledToZeroBeforeDue": True,
            "receipt": receipt,
        }

        queue_counts = az_json(
            "servicebus",
            "queue",
            "show",
            "--resource-group",
            resource_group,
            "--namespace-name",
            namespace,
            "--name",
            queue,
            "--query",
            "countDetails",
        )
        if int(queue_counts.get("deadLetterMessageCount") or 0):
            raise RuntimeError("Dreaming left a dead-letter message.")
        result["queueCounts"] = queue_counts
        result["productionScheduleUnchanged"] = str(
            tfvars.get("servicebus_dream_cron_expression") or "0 2 * * *"
        )
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
