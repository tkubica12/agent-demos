from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import DefaultAzureCredential

from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, output, terraform_output


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    platform = terraform_output(PLATFORM_DIR)
    app_tfvars = read_json(APPS_DIR / "generated.app.auto.tfvars.json") or read_json(APPS_DIR / "generated.bridge.auto.tfvars.json")
    data_volume_name = app_tfvars.get("runtime_data_volume_name") or app_tfvars.get("openclaw_data_volume_name", "openclaw-data")
    gateway_token = app_tfvars.get("openclaw_gateway_token", "")
    device = read_json(Path(".local") / platform["suffix"] / "openclaw-bridge-device.json")

    subscription_id = output(["az", "account", "show", "--query", "id", "-o", "tsv"])
    client = SandboxGroupClient(
        endpoint_for_region(platform["location"]),
        DefaultAzureCredential(),
        subscription_id=subscription_id,
        resource_group=platform["resource_group_name"],
        sandbox_group=platform["sandbox_group_name"],
    )

    for sandbox in client._dp_get(f"{client._group_path}/sandboxes"):
        if not any(volume.get("volumeName") == data_volume_name for volume in sandbox.get("volumes", [])):
            continue
        gateway_url = next((port.get("url") for port in sandbox.get("ports", []) if port.get("port") == 18789), "")
        if not gateway_url:
            raise SystemExit("Sandbox exists, but Gateway port 18789 URL was not found.")

        origin = f"{urlparse(gateway_url).scheme}://{urlparse(gateway_url).netloc}"
        sandbox_client = client.get_sandbox_client(sandbox["id"])
        command = f"""
python3 - <<'PY'
from pathlib import Path
import json

origin = {json.dumps(origin)}
config_path = Path("/data/home/.openclaw/openclaw.json")
config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {{}}
gateway = config.setdefault("gateway", {{}})
gateway["trustedProxies"] = ["127.0.0.1", "::1", "10.0.0.0/8", "172.16.0.0/12"]
control_ui = gateway.setdefault("controlUi", {{}})
control_ui["enabled"] = True
control_ui["dangerouslyDisableDeviceAuth"] = True
control_ui["dangerouslyAllowHostHeaderOriginFallback"] = True
allowed = control_ui.setdefault("allowedOrigins", [])
for item in ["http://localhost:18789", "http://127.0.0.1:18789", origin]:
    if item not in allowed:
        allowed.append(item)
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
PY
"""
        result = sandbox_client.exec(command)
        if result.exit_code != 0:
            raise SystemExit(result.stderr or result.stdout or "Failed to patch Control UI config.")

        print(f"Gateway URL: {gateway_url}")
        print(f"Gateway token: {gateway_token}")
        print(f"Bridge deviceId: {device.get('deviceId', '<missing>')}")
        return

    raise SystemExit(f"No sandbox found with volume {data_volume_name}. Run /invoke first.")


if __name__ == "__main__":
    main()
