from __future__ import annotations

import base64
import hashlib
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from azure.identity import ManagedIdentityCredential


def settlement(credential, config: dict, envelope: dict, operation: str) -> int:
    token = credential.get_token("https://management.azure.com/.default").token
    request = urllib.request.Request(
        f"https://management.azure.com{config['connection_id']}/dynamicInvoke?api-version=2026-05-01-preview",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"request": {
            "method": "DELETE" if operation == "complete" else "POST",
            "path": f"/{config['queue']}/messages/{operation}",
            "queries": {"lockToken": envelope["LockToken"], "sessionId": "", "queueType": "Main"},
        }}).encode(),
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read(4096)).get("error", {})
        except (ValueError, AttributeError):
            detail = {}
        raise RuntimeError(
            f"Native {operation} HTTP {error.code}: {detail.get('code', '')} {detail.get('message', '')[:1200]}"
        ) from error
    status = result.get("response", {}).get("statusCode")
    return {"OK": 200, "NoContent": 204, "Unauthorized": 401, "Forbidden": 403}.get(status, status)


def validate_envelope(envelope: dict, config: dict) -> str:
    if not isinstance(envelope, dict) or not envelope.get("LockToken"):
        raise ValueError("The trigger did not preserve its PeekLock envelope.")
    if envelope.get("MessageId") != config["message_id"]:
        raise ValueError("The callback message is not this isolated proof.")
    body = json.loads(base64.b64decode(envelope["ContentData"], validate=True))
    if body != {"proof": config["message_id"]}:
        raise ValueError("The trigger changed the message body.")
    return hashlib.sha256(json.dumps(envelope, sort_keys=True).encode()).hexdigest()


def run_lease_proof(config: dict, envelope: dict, save) -> None:
    with ManagedIdentityCredential(client_id=config["gateway_client_id"]) as credential:
        token = credential.get_token("https://management.azure.com/.default").token
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        if claims.get("oid") != config["gateway_principal_id"] or claims.get("tid") != config["tenant_id"]:
            raise ValueError("The settling identity is not the captured gateway principal.")
        save(settling_principal=claims["oid"])
        started = time.monotonic()
        status = settlement(credential, config, envelope, "renewlock")
        if status not in (200, 204):
            raise RuntimeError(f"Delegated renewal failed: {status}.")
        renewals = 1
        save(phase="renewing", renewals=renewals, settling_principal=claims["oid"])
        while time.monotonic() - started < 80:
            time.sleep(15)
            status = settlement(credential, config, envelope, "renewlock")
            if status not in (200, 204):
                raise RuntimeError(f"Delegated renewal failed: {status}.")
            renewals += 1
            save(renewals=renewals)
        status = settlement(credential, config, envelope, "complete")
        if status not in (200, 204):
            raise RuntimeError(f"Delegated completion failed: {status}.")
        save(phase="completed", complete_status=status, elapsed_seconds=time.monotonic() - started)


def serve(config: dict, directory: Path) -> None:
    state = {"phase": "ready", "renewals": 0}
    gate = threading.Lock()

    def save(**values):
        state.update(values)
        staged = directory / "result.next"
        staged.write_text(json.dumps(state), encoding="utf-8")
        staged.replace(directory / "result.json")

    def run(envelope: dict):
        try:
            run_lease_proof(config, envelope, save)
        except Exception as error:
            save(phase="failed", error_type=type(error).__name__, error=str(error))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/callback":
                self.send_error(404)
                return
            if not gate.acquire(blocking=False):
                self.send_error(409)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 128 * 1024:
                    raise ValueError("Invalid callback size.")
                envelope = json.loads(self.rfile.read(size))
                digest = validate_envelope(envelope, config)
                save(phase="received", envelope_sha256=digest, message_id=envelope["MessageId"])
            except (ValueError, KeyError, TypeError) as error:
                gate.release()
                self.send_error(400, type(error).__name__)
                return
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"acknowledged":false}')
            threading.Thread(target=run, args=(envelope,), daemon=True).start()

    save()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()


if __name__ == "__main__":
    root = Path(sys.argv[1])
    serve(json.loads((root / "config.json").read_text(encoding="utf-8")), root)
