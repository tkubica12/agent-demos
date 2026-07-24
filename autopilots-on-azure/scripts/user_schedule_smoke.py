from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import httpx

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.tf_helpers import PLATFORM_DIR, terraform_output


def wait_until(
    probe: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    timeout: int,
    interval: int = 5,
) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = probe()
        if predicate(last):
            return last
        time.sleep(interval)
    raise TimeoutError(f"Timed out after {timeout}s; last observation: {last!r}")


def az_value(*arguments: str) -> str:
    executable = shutil.which("az") or shutil.which("az.cmd")
    if not executable:
        raise RuntimeError("Azure CLI was not found.")
    result = subprocess.run(
        [executable, *arguments, "-o", "tsv"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def az_json(*arguments: str) -> Any:
    executable = shutil.which("az") or shutil.which("az.cmd")
    if not executable:
        raise RuntimeError("Azure CLI was not found.")
    result = subprocess.run(
        [executable, *arguments, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 4,
    **kwargs: Any,
) -> httpx.Response:
    response: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code not in {408, 429, 502, 503, 504}:
                return response
        except httpx.TransportError:
            if attempt == attempts - 1:
                raise
        time.sleep(min(5 + attempt * 5, 30))
    if response is None:
        raise RuntimeError(f"No response from {url}.")
    return response


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one real Hermes cron job through Service Bus and verify scale-to-zero."
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--due-seconds", type=int, default=180)
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()
    if args.due_seconds < 120:
        raise ValueError("--due-seconds must be at least 120 to observe scale-to-zero.")

    tfvars = json.loads(
        runtime_app_tfvars_path("hermes", args.state_name).read_text(encoding="utf-8")
    )
    outputs = json.loads(
        runtime_outputs_path("hermes", args.state_name).read_text(encoding="utf-8")
    )
    platform = terraform_output(PLATFORM_DIR)
    if not outputs.get("user_scheduling_enabled"):
        raise RuntimeError("User scheduling is not enabled for this Worker.")

    bridge_url = str(outputs["bridge_url"]).rstrip("/")
    api_key = str(tfvars["api_server_key"])
    bridge_headers = {"X-Autopilot-Key": api_key}
    gateway_headers = {"Authorization": f"Bearer {api_key}"}
    internal_headers = {"X-Autopilot-Key": api_key}
    marker = f"A12-SCHEDULE-{uuid.uuid4().hex[:12]}"
    due_at: datetime | None = None
    job_id = ""
    gateway_url = ""

    with httpx.Client(timeout=900) as client:
        invoke = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/internal/runtime/ensure",
            headers=bridge_headers,
            timeout=300,
        )
        invoke.raise_for_status()
        gateway_url = str(invoke.json()["gatewayUrl"]).rstrip("/")
        due_at = datetime.now(UTC) + timedelta(seconds=args.due_seconds)
        try:
            created = request_with_retry(
                client,
                "POST",
                f"{gateway_url}/api/jobs",
                headers=gateway_headers,
                json={
                    "name": f"A12 smoke {marker}",
                    "schedule": due_at.isoformat().replace("+00:00", "Z"),
                    "prompt": f"Reply with exactly this text and nothing else: {marker}",
                    "deliver": "local",
                    "repeat": 1,
                },
            )
            created.raise_for_status()
            job_id = str(created.json()["job"]["id"])
            bound = client.post(
                f"{gateway_url}/internal/cron/bind-local",
                headers=internal_headers,
                json={"jobIds": [job_id]},
            )
            bound.raise_for_status()
            reconciled = client.post(
                f"{gateway_url}/internal/cron/reconcile",
                headers=internal_headers,
                json={},
            )
            reconciled.raise_for_status()
            if reconciled.json().get("status") != "ok":
                raise RuntimeError(
                    f"Hermes cron reconciliation failed: {reconciled.json()!r}"
                )
            jobs = client.get(
                f"{gateway_url}/internal/cron/jobs",
                headers=internal_headers,
            )
            jobs.raise_for_status()
            job = next(
                item for item in jobs.json()["jobs"] if item.get("id") == job_id
            )
            if not job.get("externallyScheduled"):
                raise RuntimeError("Hermes cron job was not armed in Service Bus.")
            revision = str(job["revision"])

            namespace = str(platform["scheduler_servicebus_namespace_name"])
            resource_group = str(platform["resource_group_name"])
            queue = str(outputs["scheduler_servicebus_queue_name"])
            bridge_app = str(outputs["bridge_app_name"])

            def replica_count() -> int:
                return len([
                    line for line in az_value(
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

            wait_until(
                lambda: int(az_value(
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
                    "countDetails.scheduledMessageCount",
                ) or "0"),
                lambda count: count >= 1,
                timeout=60,
            )
            wait_until(
                replica_count,
                lambda count: count == 0,
                timeout=max(120, args.due_seconds - 30),
            )
            seconds_until_due = (due_at - datetime.now(UTC)).total_seconds()
            if seconds_until_due > 0:
                time.sleep(seconds_until_due + 30)
            if replica_count() < 1:
                raise RuntimeError("KEDA did not wake the bridge after the message became due.")

            def receipt_probe() -> dict[str, Any]:
                nonlocal gateway_url
                try:
                    ensured = request_with_retry(
                        client,
                        "POST",
                        f"{bridge_url}/internal/runtime/ensure",
                        headers=bridge_headers,
                        attempts=2,
                        timeout=120,
                    )
                    ensured.raise_for_status()
                    gateway_url = str(ensured.json()["gatewayUrl"]).rstrip("/")
                    receipt_url = (
                        f"{gateway_url}/internal/cron/delivery-receipt/"
                        f"{job_id}/{revision}"
                    )
                    response = client.get(
                        receipt_url,
                        headers=internal_headers,
                        timeout=60,
                    )
                except httpx.TransportError as exc:
                    return {
                        "status": "runtime_unreachable",
                        "errorType": exc.__class__.__name__,
                    }
                response.raise_for_status()
                receipt = response.json()
                if receipt.get("status") == "pending":
                    jobs_response = client.get(
                        f"{gateway_url}/internal/cron/jobs",
                        headers=internal_headers,
                        timeout=60,
                    )
                    jobs_response.raise_for_status()
                    current_job = next(
                        (
                            item
                            for item in jobs_response.json().get("jobs", [])
                            if item.get("id") == job_id
                        ),
                        None,
                    )
                    receipt["currentJob"] = current_job
                    receipt["expectedRevision"] = revision
                return receipt

            receipt = wait_until(
                receipt_probe,
                lambda value: value.get("status") == "delivered",
                timeout=args.timeout,
                interval=10,
            )
            expected_digest = hashlib.sha256(marker.encode("utf-8")).hexdigest()
            if receipt.get("outputSha256") != expected_digest:
                raise RuntimeError(
                    "Scheduled execution output did not match the deterministic marker."
                )
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
            print(json.dumps({
                "ok": True,
                "workerId": outputs["worker_id"],
                "jobId": job_id,
                "dueAt": due_at.isoformat(),
                "bridgeScaledToZeroBeforeDue": True,
                "executionStatus": receipt["status"],
                "outputSha256": receipt["outputSha256"],
                "queueCounts": queue_counts,
            }, indent=2), flush=True)
        finally:
            if gateway_url and job_id:
                try:
                    request_with_retry(
                        client,
                        "DELETE",
                        f"{gateway_url}/api/jobs/{job_id}",
                        headers=gateway_headers,
                        attempts=2,
                        timeout=60,
                    )
                except httpx.HTTPError:
                    pass


if __name__ == "__main__":
    main()
