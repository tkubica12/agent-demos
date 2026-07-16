from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def _operator_request(
    path: str,
    *,
    method: str,
    body: dict[str, Any] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    outputs = _load_json(runtime_outputs_path("hermes"))
    tfvars = _load_json(runtime_app_tfvars_path("hermes"))
    bridge_url = str(outputs.get("bridge_url") or "").rstrip("/")
    api_key = str(tfvars.get("api_server_key") or "")
    if not bridge_url or not api_key:
        raise RuntimeError("Hermes bridge_url and api_server_key must be configured.")
    request = urllib.request.Request(
        f"{bridge_url}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={
            "Content-Type": "application/json",
            "X-Autopilot-Key": api_key,
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Collective Learning Review request failed ({exc.code}): {detail}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Collective Learning Review endpoint returned a non-object response.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate Hermes Collective Learning Review.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Prepare a fail-closed Learning Packet summary.")
    prepare.add_argument("--timeout", type=int, default=600)
    approve = subparsers.add_parser("approve", help="Approve exactly one prepared Learning Packet digest.")
    approve.add_argument("--packet-digest", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--timeout", type=int, default=600)
    export = subparsers.add_parser("export", help="Download the approved Learning Packet.")
    export.add_argument("--output", type=Path, required=True)
    export.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    if args.command == "prepare":
        result = _operator_request(
            "/internal/collective-learning/prepare",
            method="POST",
            body={},
            timeout=args.timeout,
        )
    elif args.command == "approve":
        result = _operator_request(
            "/internal/collective-learning/approve",
            method="POST",
            body={
                "packetDigest": args.packet_digest,
                "approvedBy": args.approved_by,
            },
            timeout=args.timeout,
        )
    else:
        result = _operator_request(
            "/internal/collective-learning/export",
            method="GET",
            timeout=args.timeout,
        )
        worker_id = str((result.get("packet") or {}).get("worker", {}).get("workerId") or "")
        tfvars = _load_json(runtime_app_tfvars_path("hermes"))
        public_key = str(tfvars.get("collective_learning_approval_public_key") or "")
        if not worker_id or not public_key:
            raise RuntimeError("Worker ID and Collective Learning approval public key are required.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        public_keys_path = args.output.with_suffix(".worker-public-keys.json")
        public_keys_path.write_text(
            json.dumps({worker_id: public_key}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = {
            "output": str(args.output),
            "workerPublicKeys": str(public_keys_path),
            "packetVersion": (result.get("packet") or {}).get("packetVersion"),
            "improvementCount": len((result.get("packet") or {}).get("improvements") or []),
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
