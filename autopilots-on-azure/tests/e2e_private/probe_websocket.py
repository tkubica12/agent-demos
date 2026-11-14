"""Transport experiment only: verify a real HTTPS Upgrade and WebSocket echo."""

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def exact(stream, count):
    value = b""
    while len(value) < count:
        part = stream.read(count - len(value))
        if not part:
            raise ConnectionError("WebSocket closed before the full frame arrived.")
        value += part
    return value


class Echo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/api/health":
            value = b'{"transportExperiment":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)
            return
        if self.path != "/ws" or self.headers.get("Upgrade", "").lower() != "websocket":
            self.send_error(404)
            return
        key = self.headers["Sec-WebSocket-Key"]
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        first, length = exact(self.rfile, 2)
        if first != 0x81 or not length & 0x80 or length & 0x7f > 125:
            raise ValueError("This bounded transport experiment accepts one short masked text frame.")
        mask = exact(self.rfile, 4)
        value = exact(self.rfile, length & 0x7f)
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(value))
        self.wfile.write(bytes([0x81, len(payload)]) + payload)
        self.wfile.flush()


def probe(url):
    origin = urlparse(url)
    if origin.scheme != "https" or not origin.hostname or origin.username:
        raise ValueError("Use the native HTTPS origin.")
    nonce = os.urandom(16).hex().encode()
    key = base64.b64encode(os.urandom(16)).decode()
    context = ssl.create_default_context()
    with socket.create_connection((origin.hostname, origin.port or 443), timeout=20) as tcp:
        with context.wrap_socket(tcp, server_hostname=origin.hostname) as tls:
            with tls.makefile("rwb", buffering=0) as stream:
                stream.write(
                    (f"GET /ws HTTP/1.1\r\nHost: {origin.netloc}\r\nUpgrade: websocket\r\n"
                     f"Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode()
                )
                status = stream.readline().decode().strip()
                if " 101 " not in status:
                    raise AssertionError(f"WebSocket upgrade failed: {status}")
                headers = {}
                while (line := stream.readline()) != b"\r\n":
                    if not line:
                        raise ConnectionError("Incomplete HTTP upgrade.")
                    name, value = line.decode().split(":", 1)
                    headers[name.lower()] = value.strip()
                expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                if headers.get("sec-websocket-accept") != expected:
                    raise AssertionError("Invalid WebSocket handshake proof.")
                mask = os.urandom(4)
                stream.write(bytes([0x81, 0x80 | len(nonce)]) + mask +
                             bytes(byte ^ mask[index % 4] for index, byte in enumerate(nonce)))
                opcode, length = exact(stream, 2)
                if opcode != 0x81 or length > 125 or exact(stream, length) != nonce:
                    raise AssertionError("WebSocket echo did not match the unique challenge.")
                return {"http_upgrade": 101, "echo_matched": True, "tls_verified": True,
                        "peer": tls.getpeername()[0], "cipher": tls.cipher()[0]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--serve", type=int)
    group.add_argument("--url")
    args = parser.parse_args()
    if args.serve:
        ThreadingHTTPServer(("0.0.0.0", args.serve), Echo).serve_forever()
    else:
        print(json.dumps(probe(args.url), indent=2))
