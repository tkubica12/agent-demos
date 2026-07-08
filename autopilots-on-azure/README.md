# Autopilots on Azure

Host OpenClaw and Hermes autopilot runtimes in Azure Container Apps Sandboxes behind a common bridge. The bridge receives `/invoke` and Agent 365 `/api/messages` traffic, wakes or reuses the selected runtime sandbox, forwards the turn, and returns the response.

The active plan and specification is in `AUTOPILOTS_SPEC.md`.

## Current deployment model

One bridge app is deployed today. It can run either runtime:

| Runtime | Bridge setting | Sandbox port | Notes |
| --- | --- | --- | --- |
| OpenClaw | `AGENT_RUNTIME=openclaw` | `18789` | Requires OpenClaw Gateway device approval. |
| Hermes | `AGENT_RUNTIME=hermes` | `8642` | Uses Hermes API server and `API_SERVER_KEY`. |

Before the side-by-side milestone, switching the bridge runtime changes what the same bridge URL, Agent 365 endpoint, and previously installed Teams entry point talk to.

## Architecture

```text
Agent 365 / /invoke
  -> common bridge Container App
  -> ACA Sandbox runtime
       -> OpenClaw Gateway
       -> Hermes API server
  -> private incidents MCP Container App
```

Key folders:

```text
terraform\platform\       shared Azure substrate
terraform\apps\           bridge and private MCP app resources
runtimes\openclaw\        OpenClaw Gateway sandbox image
runtimes\hermes\          Hermes API server sandbox image
bridge\                   FastAPI bridge: /health, /invoke, /api/messages
bridge\runtime\           runtime adapters: OpenClaw and Hermes
private-incidents-mcp\    mock private MCP server
scripts\                  setup, build, Agent 365, sandbox helpers
docs\adr\                 architecture decision records
```

## Prerequisites

```powershell
az login
uv sync
terraform -version
```

Run commands from the project root:

```powershell
Set-Location .\autopilots-on-azure
```

## 1. Deploy platform resources

Creates the resource group, ACR, networking, ACA environments, SandboxGroup, and Foundry/model resources. It does not deploy containers.

```powershell
Set-Location .\terraform\platform
terraform init
terraform apply
Set-Location ..\..
```

## 2. Build container images

Builds the common bridge, OpenClaw runtime, and private MCP images in ACR and writes digest-pinned app tfvars.

```powershell
uv run python -m scripts.build_images
```

Generated file, do not commit:

```text
terraform\apps\generated.images.auto.tfvars.json
```

Hermes runtime images are built separately while A5 side-by-side deployment is still in progress. See `AUTOPILOTS_SPEC.md` for the current Hermes image build/smoke command.

## 3. Generate app bootstrap values

Creates runtime-specific app bootstrap values. The script writes the active Terraform tfvars file and a runtime-scoped copy under `.local\<runtime>\apps\` so OpenClaw and Hermes values do not collide while A5 side-by-side deployment work is in progress.

```powershell
uv run python -m scripts.setup_app_tfvars --runtime openclaw
```

Generated files, do not commit:

```text
terraform\apps\generated.app.auto.tfvars.json
terraform\apps\generated.runtime.auto.tfvars.json
.local\openclaw\apps\generated.app.auto.tfvars.json
.local\openclaw\apps\openclaw-bridge-device.json
```

Hermes mode does not need OpenClaw device approval. It generates an `API_SERVER_KEY` for the bridge-to-Hermes API server call path:

```powershell
uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --runtime-image "<acr>.azurecr.io/hermes-runtime@sha256:<digest>"
```

Generated Hermes file, do not commit:

```text
terraform\apps\generated.runtime.auto.tfvars.json
.local\hermes\apps\generated.app.auto.tfvars.json
```

`generated.runtime.auto.tfvars.json` intentionally loads after `generated.images.auto.tfvars.json` so runtime selection and Hermes image settings do not get overwritten by an older OpenClaw image build file.

## 4. Deploy apps

Deploys the private MCP Container App, bridge Container App, app settings/secrets, and image digests.

```powershell
Set-Location .\terraform\apps
terraform init
terraform apply
```

Check the bridge:

```powershell
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/health"
Set-Location ..\..
```

Expected:

```json
{"status":"ok"}
```

## 5. OpenClaw-only device approval

Skip this step when `AGENT_RUNTIME=hermes`.

When the bridge is in OpenClaw mode, the first invoke usually reaches the sandbox and stops on bridge device approval:

```powershell
Set-Location .\terraform\apps
$bridge = terraform output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"openclaw-smoke","message":"List services from private incidents MCP"}'
Set-Location ..\..
```

If you see `pairing required: device is not approved yet`, run:

```powershell
uv run python -m scripts.prepare_control_ui
```

Open the printed Gateway URL, paste the printed Gateway token, and approve the printed bridge `deviceId`.

## 6. Validate selected runtime with `/invoke`

OpenClaw expected response:

```powershell
Set-Location .\terraform\apps
$bridge = terraform output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"openclaw-smoke","message":"List services from private incidents MCP"}'
Set-Location ..\..
```

Expected services:

```text
core_banking
card_payments
digital_onboarding
fraud_detection
wealth_portfolio
```

Hermes expected response when the bridge is switched to `AGENT_RUNTIME=hermes`:

```powershell
Set-Location .\terraform\apps
$bridge = terraform output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"hermes-smoke","message":"Reply with exactly: Hermes bridge OK"}'
Set-Location ..\..
```

## 7. Register Agent 365 identity

Agent 365 is the primary Microsoft 365 installation path for both runtimes. Teams sideloading is not part of the active operator flow.

The deployed bridge is an externally hosted Agent 365 messaging endpoint:

```text
https://<bridge-fqdn>/api/messages
```

Prepare runtime-specific Agent 365 workspaces:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw
uv run python -m scripts.setup_agent365 --runtime hermes
```

Run setup when you are ready to create or update tenant resources:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw --run-setup
uv run python -m scripts.setup_agent365 --runtime hermes --run-setup
```

If AI teammate / Frontier is unavailable and you only want a blueprint-backed agent:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw --blueprint-agent --run-setup
uv run python -m scripts.setup_agent365 --runtime hermes --blueprint-agent --run-setup
```

Capture non-secret IDs after setup:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw --capture
uv run python -m scripts.setup_agent365 --runtime hermes --capture
```

Generated files, do not commit:

```text
.local\openclaw\agent365\a365.config.json
.local\openclaw\agent365\a365.generated.config.json
.local\openclaw\agent365\openclaw-agent365-identifiers.json
.local\hermes\agent365\a365.config.json
.local\hermes\agent365\a365.generated.config.json
.local\hermes\agent365\hermes-agent365-identifiers.json
```

Publish the package and upload it in Microsoft 365 admin center:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw --publish
uv run python -m scripts.setup_agent365 --runtime hermes --publish
```

## Runtime switching

Until A5 creates side-by-side deployments, runtime switching is done by updating bridge app environment variables. The most recently validated state can be inspected with:

```powershell
az containerapp show `
  --resource-group <resource-group> `
  --name <bridge-app-name> `
  --query "properties.template.containers[0].env[?name=='AGENT_RUNTIME']"
```

Use `AUTOPILOTS_SPEC.md` for the current required OpenClaw and Hermes environment variables.

## Troubleshooting

Common fixes:

```text
OpenClaw pairing required: run scripts.prepare_control_ui and approve the bridge device.
Hermes /health works but /invoke fails: check Hermes logs and model provider env vars.
Private MCP unavailable: verify private-incidents-mcp image includes FastMCP host-origin protection disabled on both app and run paths.
Agent 365 instance not visible: confirm the Agent 365 package/blueprint endpoint uses the bridge /api/messages URL and wait for propagation.
Azure azapi token failures: refresh az login and retry, or use az containerapp update for one-off image/env updates during development.
```

## Local validation

```powershell
uv run python -m unittest tests.test_agent365_setup tests.test_hermes_runtime tests.test_runtime_adapters tests.test_teams_bridge
uv run python -m compileall bridge scripts tests runtimes\openclaw\openclaw_gateway runtimes\hermes -q
Set-Location .\private-incidents-mcp
uv run --with pytest --with pytest-asyncio --with-editable . pytest -q
Set-Location ..
terraform -chdir=terraform\apps validate
```

## Cleanup

```powershell
Set-Location .\terraform\apps
terraform destroy

Set-Location ..\platform
terraform destroy
Set-Location ..\..
```

Optional local cleanup:

```powershell
Remove-Item .\.local -Recurse -Force
Remove-Item .\terraform\apps\generated.*.auto.tfvars.json -Force
```
