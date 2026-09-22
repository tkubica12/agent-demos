"""Deterministic sandbox workload with separate memory and durable file state."""

import json
import os
import platform
import ssl
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

memory = {"marker": None, "counter": 0}
data = Path("/data/state.json")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/readyz":
            self.reply(200, {"ready": True})
        elif self.path == "/state":
            self.reply(200, {"memory": memory, "file": json.loads(data.read_text()) if data.exists() else None})
        elif self.path == "/facts":
            self.reply(200, {
                "kernel": platform.release(), "pid": os.getpid(), "uid": os.getuid(),
                "environment_names": sorted(os.environ),
                "host_paths": {p: Path(p).exists() for p in (
                    "/opt/substrate-mvp/private", "/var/lib/kubelet", "/dev/kvm",
                    "/var/run/secrets/kubernetes.io/serviceaccount/token",
                    "/run/podidentity.podcert.ate.dev/credential-bundle.pem")},
                "ssl_paths": ssl.get_default_verify_paths()._asdict(),
            })
        else:
            self.reply(404, {"error": "Unknown path"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 2048:
            self.reply(413, {"error": "Request exceeds 2048 bytes"})
            return
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/advance":
            memory["marker"] = memory["marker"] or str(uuid.uuid4())
            memory["counter"] += 1
            durable = {"marker": body["marker"], "counter": memory["counter"]}
            data.write_text(json.dumps(durable))
            self.reply(200, {"memory": memory, "file": durable})
        elif self.path == "/probe":
            request = Request(body["url"], method=body.get("method", "GET"))
            try:
                with urlopen(request, timeout=10, context=ssl.create_default_context()) as response:
                    payload = response.read(2048).decode("utf-8", errors="replace")
                    self.reply(200, {"status": response.status, "body": payload})
            except HTTPError as error:
                self.reply(200, {"status": error.code, "error": str(error)})
            except (URLError, TimeoutError, OSError) as error:
                self.reply(200, {"status": None, "error": str(error)})
        else:
            self.reply(404, {"error": "Unknown path"})


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 80), Handler).serve_forever()
