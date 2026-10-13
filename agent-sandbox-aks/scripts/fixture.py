"""Synthetic HTTPS origin for credential-boundary tests."""

import hmac
import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        expected = Path("/secrets/token").read_text().strip()
        authorized = hmac.compare_digest(self.headers.get("Authorization", ""), expected)
        status = 200 if authorized and self.path == "/allowed" else 403
        print(json.dumps({"method": self.command, "path": self.path,
                          "authorized": authorized, "status": status}), flush=True)
        body = json.dumps({"authorized": authorized, "fixture": "aks-sandbox-synthetic-api"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.do_GET()

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
