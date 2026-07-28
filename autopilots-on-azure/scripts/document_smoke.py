from __future__ import annotations

import argparse
import json
import time
import uuid
from urllib.parse import urlparse

import httpx

from scripts.setup_app_tfvars import runtime_app_tfvars_path, runtime_outputs_path
from scripts.user_schedule_smoke import request_with_retry


DOCUMENT_URL_PREFIX = "A13_WORD_DOCUMENT_URL="


def create_prompt(
    marker: str,
    comment: str,
    reply: str,
    document_name: str,
) -> str:
    return (
        "Use only the workiq-word MCP server. "
        f"Create a Word document named {json.dumps(document_name)} whose body contains "
        f"the exact marker {json.dumps(marker)} on its own line. "
        f"Add a comment with exact text {json.dumps(comment)} and "
        f"reply to it with exact text {json.dumps(reply)}. "
        "Do not report an operation as successful unless its MCP tool returned success. "
        "Do not persist any validation content into memory or skills. "
        "Finish with exactly one machine-readable line containing the created document's "
        f"sharing URL and no Markdown on that line: {DOCUMENT_URL_PREFIX}<sharing URL>"
    )


def readback_prompt(document_url: str) -> str:
    return (
        "Use only workiq-word GetDocumentContent to read the Word document at this URL: "
        f"{document_url}\n"
        "Return the complete extracted body text, comments, and comment replies verbatim. "
        "Do not create or modify anything. This is a fresh verification session; do not rely "
        "on any prior conversation or stored memory."
    )


def parse_document_url(response_text: str) -> str:
    url_line = next(
        (
            line.strip()
            for line in reversed(response_text.splitlines())
            if line.strip().startswith(DOCUMENT_URL_PREFIX)
        ),
        "",
    )
    if not url_line:
        raise ValueError(f"Hermes response omitted {DOCUMENT_URL_PREFIX}.")
    document_url = url_line.removeprefix(DOCUMENT_URL_PREFIX).strip()
    parsed = urlparse(document_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname.endswith(".sharepoint.com"):
        raise ValueError("Work IQ Word did not return a Microsoft 365 SharePoint sharing URL.")
    return document_url


def verify_readback(
    response_text: str,
    *,
    marker: str,
    comment: str,
    reply: str,
) -> None:
    missing = [
        name
        for name, value in (
            ("document marker", marker),
            ("comment", comment),
            ("comment reply", reply),
        )
        if value not in response_text
    ]
    if missing:
        raise ValueError(
            "Independent Work IQ Word read-back omitted: "
            + ", ".join(missing)
            + "."
        )


def private_invocation(message: str, session_kind: str) -> dict[str, object]:
    return {
        "conversationId": f"hermes-a13-word-{session_kind}-{uuid.uuid4().hex}",
        "message": message,
        "persistenceDisabled": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create, read, comment on, and reply in a real Word document through Hermes."
    )
    parser.add_argument("--state-name", default="hermes2")
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args()

    tfvars = json.loads(
        runtime_app_tfvars_path("hermes", args.state_name).read_text(encoding="utf-8")
    )
    outputs = json.loads(
        runtime_outputs_path("hermes", args.state_name).read_text(encoding="utf-8")
    )
    bridge_url = str(outputs["bridge_url"]).rstrip("/")
    api_key = str(tfvars["api_server_key"])
    marker = f"A13-WORD-{uuid.uuid4().hex[:16]}"
    comment = f"A13-COMMENT-{uuid.uuid4().hex[:16]}"
    reply = f"A13-REPLY-{uuid.uuid4().hex[:16]}"
    document_name = f"A13 Automated Validation {int(time.time())}"

    with httpx.Client(timeout=args.timeout) as client:
        runtime = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/internal/runtime/ensure",
            headers={"X-Autopilot-Key": api_key},
            timeout=min(args.timeout, 900),
            attempts=8,
        )
        runtime.raise_for_status()
        created = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/invoke",
            json=private_invocation(
                create_prompt(
                    marker,
                    comment,
                    reply,
                    document_name,
                ),
                "create",
            ),
            timeout=args.timeout,
            attempts=8,
        )
        created.raise_for_status()
        created_body = created.json()
        document_url = parse_document_url(
            str(created_body.get("response") or "")
        )
        readback = request_with_retry(
            client,
            "POST",
            f"{bridge_url}/invoke",
            json=private_invocation(
                readback_prompt(document_url),
                "readback",
            ),
            timeout=args.timeout,
            attempts=8,
        )
        readback.raise_for_status()

    readback_body = readback.json()
    verify_readback(
        str(readback_body.get("response") or ""),
        marker=marker,
        comment=comment,
        reply=reply,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "workerId": str(outputs.get("worker_id") or args.state_name),
                "sandboxId": str(readback_body.get("sandboxId") or ""),
                "documentUrl": document_url,
                "marker": marker,
                "create": True,
                "read": True,
                "comment": True,
                "reply": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
