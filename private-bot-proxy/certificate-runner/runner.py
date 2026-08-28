from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.keyvault.certificates import CertificateClient
from azure.mgmt.dns import DnsManagementClient
from azure.mgmt.dns.models import RecordSet, TxtRecord


def required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def dns_record_name(domain: str, zone: str) -> str:
    suffix = f".{zone}"
    return domain[: -len(suffix)] if domain.endswith(suffix) else domain


def update_dns(action: str) -> None:
    subscription_id = required("AZURE_SUBSCRIPTION_ID")
    resource_group = required("DNS_RESOURCE_GROUP")
    zone = required("DNS_ZONE")
    validation = required("CERTBOT_VALIDATION")
    record_name = dns_record_name(f"_acme-challenge.{required('CERTBOT_DOMAIN')}", zone)
    client = DnsManagementClient(DefaultAzureCredential(), subscription_id)
    if action == "create":
        client.record_sets.create_or_update(
            resource_group,
            zone,
            record_name,
            "TXT",
            RecordSet(ttl=30, txt_records=[TxtRecord(value=[validation])]),
        )
        time.sleep(45)
    else:
        client.record_sets.delete(resource_group, zone, record_name, "TXT")


def hook() -> None:
    update_dns(sys.argv[2])


def issue() -> None:
    hostname = required("CERTIFICATE_HOSTNAME")
    email = required("CERTIFICATE_EMAIL")
    hook_command = f"{sys.executable} /runner/runner.py hook"
    subprocess.run(
        [
            "certbot",
            "certonly",
            "--manual",
            "--preferred-challenges",
            "dns",
            "--manual-auth-hook",
            f"{hook_command} create",
            "--manual-cleanup-hook",
            f"{hook_command} delete",
            "--non-interactive",
            "--agree-tos",
            "--email",
            email,
            "-d",
            hostname,
        ],
        check=True,
    )
    live = Path("/etc/letsencrypt/live") / hostname
    pfx = Path("/tmp/certificate.pfx")
    subprocess.run(
        [
            "openssl",
            "pkcs12",
            "-export",
            "-out",
            str(pfx),
            "-inkey",
            str(live / "privkey.pem"),
            "-in",
            str(live / "cert.pem"),
            "-certfile",
            str(live / "chain.pem"),
            "-passout",
            "pass:",
        ],
        check=True,
    )
    client = CertificateClient(
        vault_url=f"https://{required('KEY_VAULT_NAME')}.vault.azure.net",
        credential=DefaultAzureCredential(),
    )
    client.import_certificate(
        certificate_name=required("KEY_VAULT_CERTIFICATE_NAME"),
        certificate_bytes=pfx.read_bytes(),
        password="",
    )
    pfx.unlink()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hook":
        hook()
    else:
        issue()
