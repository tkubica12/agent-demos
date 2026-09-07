from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

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
    state_name: str = "",
) -> dict[str, Any]:
    outputs = _load_json(runtime_outputs_path("hermes", state_name))
    tfvars = _load_json(runtime_app_tfvars_path("hermes", state_name))
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


def attested_envelope(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    packet = result.get("packet")
    receipt = result.get("receipt")
    if not isinstance(packet, dict) or not isinstance(receipt, dict):
        raise RuntimeError("Collective Learning export did not return an attested packet and receipt.")
    return {
        "packet": packet,
        "receipt": receipt,
    }


def verify_rejection_receipt(
    receipt: dict[str, Any], *, public_key: str, digest: str, rejected_by: str, reason: str,
) -> None:
    if set(receipt) != {
        "disposition", "rejectedAt", "rejectedBy", "reason", "workerId",
        "roleReleaseCommit", "governedStateHash", "dispositionDigest", "signature",
    } or not all(isinstance(value, str) and value.strip() for value in receipt.values()):
        raise RuntimeError("The bridge did not return a signed rejection receipt.")
    if (
        receipt["disposition"] != "reject_and_refresh"
        or receipt["dispositionDigest"] != digest
        or receipt["rejectedBy"] != rejected_by
        or receipt["reason"] != reason
    ):
        raise RuntimeError("Rejection receipt does not match the requested disposition.")
    signed = {key: value for key, value in receipt.items() if key != "signature"}
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True)).verify(
            base64.b64decode(receipt["signature"], validate=True),
            json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"),
        )
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise RuntimeError("Rejection receipt signature is invalid.") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate Hermes Collective Learning Review.")
    parser.add_argument("--state-name", default="hermes", help="Local Worker state directory under .local.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Prepare a fail-closed Learning Packet summary.")
    prepare.add_argument("--timeout", type=int, default=600)
    inspect = subparsers.add_parser("inspect", help="Inspect the full pending packet including agent-proposed scenarios.")
    inspect.add_argument("--timeout", type=int, default=600)
    rejection = subparsers.add_parser("prepare-rejection", help="Prepare a local reject-and-refresh disposition, not an export.")
    rejection.add_argument("--timeout", type=int, default=600)
    reject = subparsers.add_parser("reject", help="Reject local governed changes and authorize a normal future refresh.")
    reject.add_argument("--disposition-digest", required=True)
    reject.add_argument("--rejected-by", required=True)
    reject.add_argument("--reason", required=True)
    reject.add_argument("--timeout", type=int, default=600)
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
            state_name=args.state_name,
        )
    elif args.command == "inspect":
        result = _operator_request(
            "/internal/collective-learning/pending", method="GET",
            timeout=args.timeout, state_name=args.state_name,
        )
    elif args.command == "prepare-rejection":
        result = _operator_request(
            "/internal/collective-learning/prepare-rejection", method="POST", body={},
            timeout=args.timeout, state_name=args.state_name,
        )
    elif args.command == "reject":
        if not re.fullmatch(r"[0-9a-f]{64}", args.disposition_digest):
            parser.error("--disposition-digest must be a lowercase SHA-256 digest.")
        if not args.rejected_by.strip() or not args.reason.strip():
            parser.error("--rejected-by and --reason cannot be blank.")
        args.rejected_by, args.reason = args.rejected_by.strip(), args.reason.strip()
        tfvars = _load_json(runtime_app_tfvars_path("hermes", args.state_name))
        public_key = str(tfvars.get("collective_learning_approval_public_key") or "")
        if not public_key:
            raise RuntimeError("The existing Collective Learning approval public key is required.")
        result = _operator_request(
            "/internal/collective-learning/reject", method="POST",
            body={"dispositionDigest": args.disposition_digest, "rejectedBy": args.rejected_by, "reason": args.reason},
            timeout=args.timeout, state_name=args.state_name,
        )
        result = {
            key: value for key, value in result.items()
            if key not in {"sandboxId", "gatewayUrl", "reusedExistingSandbox"}
        }
        verify_rejection_receipt(
            result, public_key=public_key, digest=args.disposition_digest,
            rejected_by=args.rejected_by, reason=args.reason,
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
            state_name=args.state_name,
        )
    else:
        result = _operator_request(
            "/internal/collective-learning/export",
            method="GET",
            timeout=args.timeout,
            state_name=args.state_name,
        )
        envelope = attested_envelope(result)
        worker_id = str(envelope["packet"].get("worker", {}).get("workerId") or "")
        tfvars = _load_json(runtime_app_tfvars_path("hermes", args.state_name))
        public_key = str(tfvars.get("collective_learning_approval_public_key") or "")
        if not worker_id or not public_key:
            raise RuntimeError("Worker ID and Collective Learning approval public key are required.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        public_keys_path = args.output.with_suffix(".worker-public-keys.json")
        public_keys_path.write_text(
            json.dumps({worker_id: public_key}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = {
            "output": str(args.output),
            "workerPublicKeys": str(public_keys_path),
            "packetVersion": envelope["packet"].get("packetVersion"),
            "improvementCount": len(envelope["packet"].get("improvements") or []),
        }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
