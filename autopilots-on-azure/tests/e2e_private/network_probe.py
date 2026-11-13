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


def check(url, public=False):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.port not in (None, 443)):
        raise ValueError("Provide an HTTPS origin without credentials or a query.")
    result = {"url": url.rstrip("/"), "public_negative": public}
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)})
    except socket.gaierror as error:
        if not public:
            raise
        result.update(status="pass", dns_error=type(error).__name__)
        return result
    result["addresses"] = addresses
    if not public and not all(private_address(address) for address in addresses):
        raise AssertionError(f"{parsed.hostname} did not resolve exclusively to RFC1918 addresses.")
    if public and any(private_address(address) for address in addresses):
        raise AssertionError("Public-negative probe resolved a private address; run it outside private DNS/VPN.")
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
    result["status"] = "pass"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--public-negative", action="store_true")
    parser.add_argument("--control-url", default="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "results": []}
    if args.public_negative:
        evidence["control"] = public_control(args.control_url)
    failed = False
    for url in args.url:
        try:
            result = check(url, args.public_negative)
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
