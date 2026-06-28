# OpenClaw on Azure

Run OpenClaw Gateway in ACA Sandboxes. Keep a tiny ACA Express bridge always reachable. The bridge wakes the sandbox and forwards user turns to OpenClaw.

## Current target

```text
Terraform platform
  -> rg-openclaw-xxxx
  -> ACR
  -> VNet + private DNS
  -> ACA private MCP environment
  -> ACA Express bridge environment
  -> ACA SandboxGroup
  -> Foundry + model

Build script
  -> ACR remote builds
  -> image digests in terraform\apps\generated.images.auto.tfvars.json

Terraform apps
  -> private incidents MCP Container App
  -> ACA Express bridge app
  -> app env, secrets, image digests

Bridge invoke
  -> wake/create ACA Sandbox
  -> call OpenClaw Gateway
  -> stop for OpenClaw device approval if needed
```

## Folders

```text
terraform\platform\   base Azure resources, no containers
terraform\apps\       deployed apps and app config
image\                OpenClaw Gateway sandbox image
bridge\               ACA Express bridge, /health and /invoke
private-incidents-mcp\ mock private MCP server
scripts\              build/setup helpers
```

## Prereqs

```powershell
az login
uv sync
terraform -version
```

## 1. Platform

Creates base infra only. No apps.

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\platform
terraform init
terraform apply
```

Output includes the random suffix, for example:

```text
suffix = "ehvw"
resource_group_name = "rg-openclaw-ehvw"
acr_name = "oclawehvw"
```

## 2. Build images

Builds images in ACR. Writes digest-pinned tfvars.

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.build_images
```

Creates:

```text
terraform\apps\generated.images.auto.tfvars.json
```

Do not commit it.

## 3. Bridge bootstrap values

Creates/reuses Entra app for ACA Express. Rotates secret. Generates gateway token. Generates bridge device key. Writes tfvars.

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.setup_bridge_tfvars
```

Creates:

```text
terraform\apps\generated.bridge.auto.tfvars.json
.local\<suffix>\openclaw-bridge-device.json
```

Do not commit them.

Save the printed `deviceId`. You need it for approval.

## 4. Apps

Deploys private MCP and bridge.

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\apps
terraform init
terraform apply
```

Check bridge:

```powershell
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/health"
```

Expected:

```json
{"status":"ok"}
```

## 5. Invoke bridge

```powershell
$bridge = terraform output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"smoke","message":"List services from private incidents MCP"}'
```

Current expected stop point:

```json
{
  "detail": {
    "message": "pairing required: device is not approved yet",
    "type": "OpenClawGatewayError"
  }
}
```

This is good. Azure is up. Bridge is up. Sandbox path is reached. Now OpenClaw needs human device approval.

## 6. Approval step

Prepare the Control UI and print values:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.prepare_control_ui
```

It prints:

```text
Gateway URL
Gateway token
Bridge deviceId
```

Open the sandbox Gateway UI.

Go to:

```text
https://sandboxes.azure.com
  -> select sandbox group openclaw-sandbox-xxxx
  -> click sandbox with label app: openclaw-on-azure and volume openclaw-bridge-e2e-clean
  -> open its exposed port 18789 URL
```

Open the printed Gateway URL. Paste printed Gateway token. Click **Connect**.

The browser should not ask for browser device pairing. For this demo, Control UI device auth is disabled in sandbox config, while the Gateway token is still required.

Approve the bridge device in the Control UI.
Use the printed bridge deviceId.

In Control UI:

```text
AGENT
  -> Nodes and Devices
  -> find bridge device ID
  -> approve
```

If the bridge device is not visible, rerun Step 5 `/invoke`. The pending request can disappear after a Gateway restart.

Run `/invoke` again. It should return an OpenClaw answer. No token copy is needed for this flow; the bridge uses its stable private key and the approved device entry in the Gateway.

Example successful response:

```json
{
  "response": "- core_banking\n- card_payments\n- digital_onboarding\n- fraud_detection\n- wealth_portfolio"
}
```

Security note:

```text
Control UI device auth is disabled for bootstrap UX.
Gateway token is still required.
Bridge device approval is still required.
After bridge works, we can harden Control UI again.
```

## Terraform notes

Two layers only:

```text
platform = base infra, no containers
apps     = all deployed app resources and app config
```

Do not put one Azure resource in both layers.

Terraform does not build images. Scripts build images and write digest tfvars.

ACA Express preview normalizes some values on read. `terraform\apps\main.tf` uses lifecycle `ignore_changes` for that noise:

```text
single -> Single
auto -> Http
null workload profile -> Consumption
default CPU/memory appears on read
```

This keeps plans clean. Intentional changes still flow through Terraform: image digests, env vars, secrets, identity inputs.

## Current deployed test environment

Latest fresh run created:

```text
Resource group: rg-openclaw-ehvw
ACR:            oclawehvw
Bridge app:     ocbridge-ehvw
Bridge URL:     https://ocbridge-ehvw.yellowmushroom-9dd09aa7.westcentralus.azurecontainerapps.io
Private MCP:    ocmcp-ehvw
Bridge volume:  openclaw-bridge-e2e-clean
```

Bridge `/health` works.

Bridge `/invoke` now works after bridge device approval.

## What not to do yet

Do not start Teams integration yet.

First finish:

```text
Approve bridge device
/invoke returns real OpenClaw answer
```

Then start Teams `/api/messages`.

## Cleanup

Delete everything for the current run:

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\apps
terraform destroy

cd D:\agent-demos\openclaw-on-azure\terraform\platform
terraform destroy
```

If needed, remove generated local files:

```powershell
Remove-Item D:\agent-demos\openclaw-on-azure\.local -Recurse -Force
Remove-Item D:\agent-demos\openclaw-on-azure\terraform\apps\generated.*.auto.tfvars.json -Force
```
