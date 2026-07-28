from __future__ import annotations

import argparse
import json

import httpx

from bridge.proactive_delivery import delivery_reference_key
from scripts.setup_app_tfvars import (
    runtime_app_tfvars_path,
    runtime_outputs_path,
)
from scripts.user_schedule_smoke import request_with_retry


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate document retry Service Bus transport and send a "
            "non-actionable suggested-action preview to an existing Teams chat."
        )
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--conversation-id", required=True)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    tfvars = json.loads(
        runtime_app_tfvars_path(
            "hermes",
            args.state_name,
        ).read_text(encoding="utf-8")
    )
    outputs = json.loads(
        runtime_outputs_path(
            "hermes",
            args.state_name,
        ).read_text(encoding="utf-8")
    )
    worker_id = str(
        tfvars.get("autopilot_name")
        or tfvars.get("worker_id")
        or args.state_name
    )
    reference_key = delivery_reference_key(
        worker_id=worker_id,
        conversation_id=args.conversation_id,
        boundary="one_to_one",
    )
    summary = {
        "execute": args.execute,
        "workerId": worker_id,
        "referenceKey": reference_key,
        "transport": "not-run",
        "card": "not-run",
    }
    if not args.execute:
        summary["next"] = (
            "Rerun with --execute to schedule/cancel a future retry "
            "message and send a non-actionable card preview."
        )
        print(json.dumps(summary, indent=2))
        return

    bridge_url = str(outputs["bridge_url"]).rstrip("/")
    api_key = str(tfvars["api_server_key"])
    headers = {"X-Autopilot-Key": api_key}
    with httpx.Client(timeout=args.timeout) as client:
        runtime = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/internal/runtime/ensure",
            headers=headers,
            timeout=min(args.timeout, 900),
            attempts=8,
        )
        runtime.raise_for_status()
        transport = request_with_retry(
            client,
            "POST",
            (
                f"{bridge_url}/internal/document-retry/"
                "transport-smoke"
            ),
            headers=headers,
            timeout=120,
            attempts=3,
        )
        transport.raise_for_status()
        card = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/internal/document-card/preview",
            headers=headers,
            json={
                "referenceKey": reference_key,
                "fileName": "Suggested action validation.docx",
            },
            timeout=args.timeout,
            attempts=3,
        )
        card.raise_for_status()
    summary["transport"] = transport.json()
    summary["card"] = card.json()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
