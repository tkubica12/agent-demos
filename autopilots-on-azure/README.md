# Autopilots on Azure

Host autopilot runtimes in Azure Container Apps Sandboxes behind a small standard Azure Container Apps bridge. The bridge receives direct HTTP, Teams, and Agent 365 traffic, wakes or reuses the selected runtime sandbox, forwards the turn, and sends the response back.

The current implemented runtime is **OpenClaw Autopilot**. Hermes is planned as a peer runtime behind the same bridge after the rename/neutralization milestone.

## Architecture

```text
Teams / Agent 365 / /invoke
  -> common bridge Container App
  -> ACA Sandbox runtime
       -> OpenClaw Gateway today
       -> Hermes API server later
  -> private incidents MCP Container App
```

Key folders:

```text
terraform\platform\       shared Azure substrate
terraform\apps\           bridge, private MCP, Teams bot/channel
runtimes\openclaw\        OpenClaw Gateway sandbox image
bridge\                   FastAPI bridge: /health, /invoke, /api/messages
bridge\runtime\           runtime adapter contract and OpenClaw adapter
private-incidents-mcp\    mock private MCP server
scripts\                  setup, build, packaging helpers
teams\                    Teams manifest template
docs\adr\                 architecture decision records
```

`OPENCLAW_OPTION_PATH.md` remains the historical and forward option path for the OpenClaw runtime. `HERMES_OPTION_PLAN.md` tracks the multi-runtime migration and Hermes milestones.

## Prerequisites

```powershell
az login
uv sync
terraform -version
```

Run commands from:

```powershell
cd D:\agent-demos\autopilots-on-azure
```

## 1. Deploy platform resources

Creates the resource group, ACR, networking, ACA environments, SandboxGroup, and Foundry/model resources. It does not deploy containers.

```powershell
cd D:\agent-demos\autopilots-on-azure\terraform\platform
terraform init
terraform apply
```

Note the generated suffix, for example `ehvw`.

## 2. Build container images

Builds the common bridge, OpenClaw runtime, and private MCP images in ACR and writes digest-pinned app tfvars.

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m scripts.build_images
```

Generated file, do not commit:

```text
terraform\apps\generated.images.auto.tfvars.json
```

## 3. Generate app bootstrap values

Creates the OpenClaw Gateway token and stable bridge device key. The bridge uses managed identity for Azure API calls.

```powershell
uv run python -m scripts.setup_app_tfvars
```

Generated files, do not commit:

```text
terraform\apps\generated.app.auto.tfvars.json
.local\<suffix>\openclaw-bridge-device.json
```

Save the printed `deviceId`; you need it for OpenClaw approval.

## 4. Deploy apps

Deploys the private MCP Container App, bridge Container App, app settings/secrets, and image digests.

```powershell
cd D:\agent-demos\autopilots-on-azure\terraform\apps
terraform init
terraform apply
```

Check the bridge:

```powershell
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/health"
```

Expected:

```json
{"status":"ok"}
```

## 5. Approve the OpenClaw bridge device

First invoke usually reaches the sandbox and stops on bridge device approval.

```powershell
$bridge = terraform output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"smoke","message":"List services from private incidents MCP"}'
```

If you see `pairing required: device is not approved yet`, continue:

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m scripts.prepare_control_ui
```

Open the printed Gateway URL, paste the printed Gateway token, then approve the printed bridge `deviceId`:

```text
AGENT -> Nodes and Devices -> find bridge device ID -> approve
```

Run `/invoke` again. Expected result is an OpenClaw answer, for example:

```json
{
  "response": "- core_banking\n- card_payments\n- digital_onboarding\n- fraud_detection\n- wealth_portfolio"
}
```

## 6. Add Teams app support

Create the Teams bot app registration/tfvars:

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m scripts.setup_teams_tfvars
```

Rebuild and apply so Terraform updates the bridge settings plus Azure Bot and Teams channel resources:

```powershell
uv run python -m scripts.build_images
cd D:\agent-demos\autopilots-on-azure\terraform\apps
terraform apply
```

Package the Teams app:

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m scripts.package_teams_app
```

Upload the printed ZIP to Teams. The default path is:

```text
D:\agent-demos\autopilots-on-azure\.local\<suffix>\teams\openclaw-autopilot-teams.zip
```

Install the app into:

1. A 1:1 chat with OpenClaw Autopilot.
2. A team/channel where you want to test collaborative behavior.

Approve the Teams consent prompt for RSC permissions if shown:

```text
ChannelMessage.Read.Group
ChatMessage.Read.Chat
```

Optional targeted private messages preview:

```powershell
uv run python -m scripts.package_teams_app --preview-targeted-messages --output .local\<suffix>\teams\openclaw-autopilot-teams-targeted-preview.zip
```

If Teams validation rejects the preview package, use the normal package. The rest of the demo does not require targeted private messages.

## 7. Register Agent 365 identity

The deployed bridge is an externally hosted Agent 365 messaging endpoint:

```text
https://<bridge-fqdn>/api/messages
```

Prepare the local Agent 365 workspace and print the exact commands for the current bridge endpoint:

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m scripts.setup_agent365
```

Run setup when you are ready to create/update tenant resources:

```powershell
uv run python -m scripts.setup_agent365 --run-setup
```

The default is the Agent 365 AI teammate flow. If your tenant is not in the Frontier preview or you only want a blueprint-backed M365 agent without an Entra user, use:

```powershell
uv run python -m scripts.setup_agent365 --blueprint-agent --run-setup
```

Capture non-secret IDs after setup:

```powershell
uv run python -m scripts.setup_agent365 --capture
```

Generated files, do not commit:

```text
.local\<suffix>\agent365\a365.config.json
.local\<suffix>\agent365\a365.generated.config.json
.local\<suffix>\agent365\openclaw-autopilot-agent365-identifiers.json
```

If the bridge URL changes later, update only the Agent 365 endpoint registration:

```powershell
cd D:\agent-demos\autopilots-on-azure\.local\<suffix>\agent365
a365 setup blueprint --update-endpoint https://<new-bridge-fqdn>/api/messages
```

Publish the package and upload it in Microsoft 365 admin center:

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m scripts.setup_agent365 --publish
```

## Demo script

In 1:1 chat:

```text
List services from private incidents MCP
```

Expected: OpenClaw Autopilot answers in the 1:1 chat. The bridge can use Teams streaming here.

In a channel where OpenClaw Autopilot is installed:

```text
@OpenClaw Autopilot Hi, can you hear me? Who am I?
```

Expected: the bridge adds a temporary eyes reaction if Teams accepts bot reactions, removes it when the runtime is done, and sends the response in the same thread. OpenClaw can also request final semantic reactions with:

```text
TEAMS_REACTION: eyes | like | heart | smile | surprised | check
```

Weak-signal channel messages are forwarded as bounded context when enabled. OpenClaw may stay silent by returning exactly `NO_RESPONSE`.

## Troubleshooting

Check bridge health:

```powershell
cd D:\agent-demos\autopilots-on-azure\terraform\apps
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/health"
```

Check Teams diagnostics:

```powershell
Invoke-RestMethod "$bridge/diag/teams" | ConvertTo-Json -Depth 12
```

Common fixes:

```text
No channel messages: reinstall/update the Teams app and approve RSC permissions.
No channel answer: check /diag/teams for responseSent or backgroundException.
No bot reactions: reactionSendFailed means Teams rejected reaction writes in that scope; text answers still work.
Preview package rejected: use the normal package; targeted messages require preview schema support.
Agent 365 instance is not visible in Teams: confirm the Developer Portal blueprint uses API Based and the deployed bridge /api/messages URL, then wait 5-10 minutes.
```

## Local validation

Run targeted tests after bridge/script changes:

```powershell
cd D:\agent-demos\autopilots-on-azure
uv run python -m unittest tests.test_teams_bridge tests.test_agent365_setup
uv run python -m compileall bridge scripts tests runtimes\openclaw\openclaw_gateway -q
```

## Cleanup

```powershell
cd D:\agent-demos\autopilots-on-azure\terraform\apps
terraform destroy

cd D:\agent-demos\autopilots-on-azure\terraform\platform
terraform destroy
```

Optional local cleanup:

```powershell
Remove-Item D:\agent-demos\autopilots-on-azure\.local -Recurse -Force
Remove-Item D:\agent-demos\autopilots-on-azure\terraform\apps\generated.*.auto.tfvars.json -Force
```
