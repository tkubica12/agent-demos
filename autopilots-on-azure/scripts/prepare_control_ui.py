from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, terraform_output


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def invoke_for_gateway_url(bridge_url: str) -> tuple[str, str]:
    body = json.dumps(
        {
            "conversationId": "control-ui-prep",
            "message": "OpenClaw device approval check",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{bridge_url.rstrip('/')}/invoke",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return "", f"Bridge invoke succeeded; device approval may already be complete. Response: {payload.get('response', '')}"
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        detail = payload.get("detail") or {}
        gateway_url = detail.get("gatewayUrl") or ""
        message = detail.get("message") or str(payload)
        return gateway_url, message


def main() -> None:
    platform = terraform_output(PLATFORM_DIR)
    apps = terraform_output(APPS_DIR)
    app_tfvars = read_json(APPS_DIR / "generated.app.auto.tfvars.json") or read_json(APPS_DIR / "generated.bridge.auto.tfvars.json")
    gateway_token = app_tfvars.get("openclaw_gateway_token", "")
    data_volume_name = app_tfvars.get("runtime_data_volume_name") or app_tfvars.get("openclaw_data_volume_name", "openclaw-data")
    device = read_json(Path(".local") / platform["suffix"] / "openclaw-bridge-device.json")

    bridge_url = apps["bridge_url"]
    print(f"Probing bridge {bridge_url}/invoke for OpenClaw Gateway details...", flush=True)
    gateway_url, message = invoke_for_gateway_url(bridge_url)
    if not gateway_url:
        print(message)
        return

    print(f"Data volume: {data_volume_name}")
    print(f"Gateway URL: {gateway_url}")
    print(f"Gateway token: {gateway_token}")
    print(f"Bridge deviceId: {device.get('deviceId', '<missing>')}")


if __name__ == "__main__":
    main()
