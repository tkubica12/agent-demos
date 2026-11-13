"""Verify private TLS reachability, or run the separate public-negative check."""

import argparse
import datetime
import ipaddress
import json
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


RFC1918 = tuple(ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def request(url, timeout=20):
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout)


def public_control(url):
    with request(url) as response:
        if response.status != 200:
            raise AssertionError("Public connectivity control must return HTTP 200.")
    return {"url": url, "status": "pass"}


def private_address(value):
    address = ipaddress.ip_address(value)
    return any(address in network for network in RFC1918)


def check(url, public=False, transport_only=False, temporary_public=False):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.port not in (None, 443)):
        raise ValueError("Provide an HTTPS origin without credentials or a query.")
    if public and transport_only:
        raise ValueError("Transport-only is a private DNS/TLS check, not a public-negative proof.")
    if public and temporary_public:
        raise ValueError("Public-negative and temporary-public readiness are separate phases.")
    result = {
        "url": url.rstrip("/"), "public_negative": public,
        "scope": ("public-negative" if public else
                  "temporary-public-dns-tls" if temporary_public and transport_only else
                  "temporary-public-readiness" if temporary_public else
                  "private-dns-tls" if transport_only else "private-readiness"),
    }
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror as error:
        if not public:
            raise
        result.update(status="pass", dns_error=type(error).__name__)
        return result
    result["addresses"] = addresses
    if temporary_public and not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise AssertionError("Temporary-public testing must resolve exclusively to global addresses.")
    if not public and not temporary_public and not all(private_address(address) for address in addresses):
        raise AssertionError(f"{parsed.hostname} did not resolve exclusively to RFC1918 addresses.")
    if public and any(private_address(address) for address in addresses):
        raise AssertionError("Public-negative probe resolved a private address; run it outside private DNS/VPN.")
    if transport_only or temporary_public:
        with socket.create_connection((parsed.hostname, 443), timeout=20) as connection:
            with ssl.create_default_context().wrap_socket(connection, server_hostname=parsed.hostname) as tls:
                peer = tls.getpeername()[0]
                if temporary_public and not ipaddress.ip_address(peer).is_global:
                    raise AssertionError("Temporary-public TLS did not reach a global peer.")
                if not temporary_public and not private_address(peer):
                    raise AssertionError("Verified TLS connection did not reach a private peer.")
                result.update(tls_verified=True, peer=peer, cipher=tls.cipher()[0], tls_version=tls.version())
        if transport_only:
            result["status"] = "pass"
            return result
    try:
        with request(f"{url.rstrip('/')}/api/health") as response:
            result["http_status"] = response.status
            result["health"] = json.load(response)
    except urllib.error.HTTPError as error:
        result["http_status"] = error.code
        if not public or error.code not in (403, 404):
            raise
    except urllib.error.URLError as error:
        if not public or isinstance(error.reason, ssl.SSLCertVerificationError):
            raise
        result["transport_error"] = type(error.reason).__name__
    if public and result.get("http_status") == 200:
        raise AssertionError("Simulator health is reachable over the public path.")
    if not public and (result.get("http_status") != 200 or not isinstance(result.get("health"), dict)
                       or result["health"].get("status") != "ok"):
        raise AssertionError("Simulator readiness requires HTTP 200 and status=ok.")
    result["status"] = "pass"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--public-negative", action="store_true")
    parser.add_argument("--transport-only", action="store_true")
    parser.add_argument("--temporary-public", action="store_true")
    parser.add_argument("--control-url", default="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "results": []}
    if args.public_negative or args.temporary_public:
        evidence["control"] = public_control(args.control_url)
    failed = False
    for url in args.url:
        try:
            result = check(url, args.public_negative, args.transport_only, args.temporary_public)
        except (ValueError, AssertionError, OSError, urllib.error.URLError) as error:
            result = {"url": url, "status": "fail", "error": str(error)}
            failed = True
        evidence["results"].append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
