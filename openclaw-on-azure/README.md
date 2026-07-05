# OpenClaw on Azure

Run OpenClaw Gateway in Azure Container Apps Sandboxes and expose it through a small standard Azure Container Apps bridge. The bridge receives direct HTTP and Teams traffic, wakes or reuses the sandbox, calls OpenClaw Gateway, and sends the answer back.

## Architecture

```text
Teams or /invoke
  -> bridge Container App
  -> ACA Sandbox OpenClaw Gateway
  -> private incidents MCP Container App
```

Key folders:

```text
terraform\platform\   base Azure resources
terraform\apps\       private MCP, bridge, Teams bot/channel
image\                OpenClaw Gateway sandbox image
bridge\               FastAPI bridge: /health, /invoke, /api/messages
private-incidents-mcp\ mock private MCP server
scripts\              setup, build, packaging helpers
teams\                Teams manifest template
docs\adr\             architecture decision records
```

Important decisions are recorded in `docs\adr\`, including why the bridge uses standard ACA, how Teams event/reaction routing is split between bridge and OpenClaw, why targeted private messages use a separate preview package, and why Agent 365 identity comes before broad Work IQ MCP integration.

## Prerequisites

```powershell
az login
uv sync
terraform -version
```

Run commands from:

```powershell
cd D:\agent-demos\openclaw-on-azure
```

## 1. Deploy platform resources

Creates the resource group, ACR, networking, ACA environments, SandboxGroup, and Foundry/model resources. It does not deploy containers.

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\platform
terraform init
terraform apply
```

Note the generated suffix, for example `ehvw`.

## 2. Build container images

Builds images in ACR and writes digest-pinned app tfvars.

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.build_images
```

Generated file, do not commit:

```text
terraform\apps\generated.images.auto.tfvars.json
```

## 3. Generate bridge bootstrap values

Creates the Gateway token and stable bridge device key. The bridge uses managed identity for Azure API calls.

```powershell
uv run python -m scripts.setup_bridge_tfvars
```

Generated files, do not commit:

```text
terraform\apps\generated.bridge.auto.tfvars.json
.local\<suffix>\openclaw-bridge-device.json
```

Save the printed `deviceId`; you need it for OpenClaw approval.

## 4. Deploy apps

Deploys the private MCP Container App, bridge Container App, app settings/secrets, and image digests.

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\apps
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

## 5. Approve the bridge in OpenClaw

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
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.prepare_control_ui
```

Open the printed Gateway URL, paste the printed Gateway token, then approve the printed bridge `deviceId`:

```text
AGENT -> Nodes and Devices -> find bridge device ID -> approve
```

If the bridge device is not visible, rerun the `/invoke` call so the pending device request is recreated.

Run `/invoke` again. Expected result is an OpenClaw answer, for example:

```json
{
  "response": "- core_banking\n- card_payments\n- digital_onboarding\n- fraud_detection\n- wealth_portfolio"
}
```

## 6. Add Teams app support

Create the Teams bot app registration/tfvars:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.setup_teams_tfvars
```

Rebuild and apply so Terraform updates the bridge settings plus Azure Bot and Teams channel resources:

```powershell
uv run python -m scripts.build_images
cd D:\agent-demos\openclaw-on-azure\terraform\apps
terraform apply
```

Package the normal Teams app:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.package_teams_app
```

Upload the printed ZIP to Teams. In the current demo environment it is:

```text
D:\agent-demos\openclaw-on-azure\.local\ehvw\teams\openclaw-teams.zip
```

Install the app into:

1. A 1:1 chat with OpenClaw.
2. A team/channel where you want to test collaborative behavior.

Approve the Teams consent prompt for RSC permissions if shown:

```text
ChannelMessage.Read.Group
ChatMessage.Read.Chat
```

If the app was already installed before permissions changed, remove and reinstall/update it so Teams asks for consent again.

### Optional targeted private messages preview

Targeted messages are public preview. Build this separate package only if your Teams client/tenant accepts manifest schema 1.29:

```powershell
uv run python -m scripts.package_teams_app --preview-targeted-messages --output .local\ehvw\teams\openclaw-teams-targeted-preview.zip
```

This preview package adds:

```json
{
  "manifestVersion": "1.29",
  "supportsChannelFeatures": "tier1",
  "bots[0].supportsTargetedMessages": true
}
```

If Teams validation rejects it, use the normal package. The rest of the demo does not require targeted private messages.

## 7. Register Agent 365 identity

Milestone 4 uses the deployed bridge as an externally hosted Agent 365 messaging endpoint:

```text
https://<bridge-fqdn>/api/messages
```

The Agent 365 working files are generated under `.local\<suffix>\agent365\` so tenant-specific IDs and generated secrets stay out of source control. The existing Teams sideload package remains the fallback/demo path until the Agent 365 instance is visible and tested in Teams.

Install or update the Agent 365 CLI if needed:

```powershell
dotnet tool install --global Microsoft.Agents.A365.DevTools.Cli
a365 -h
```

Prepare the local Agent 365 workspace and print the exact commands for the current bridge endpoint:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.setup_agent365
```

Run setup when you are ready to create/update tenant resources:

```powershell
uv run python -m scripts.setup_agent365 --run-setup
```

The milestone default is the Agent 365 AI teammate flow because it is the path that can create an agent user identity. If your tenant is not in the Frontier preview or you only want a blueprint-backed M365 agent without an Entra user, use:

```powershell
uv run python -m scripts.setup_agent365 --blueprint-agent --run-setup
```

After setup succeeds, capture non-secret IDs for the OpenClaw option path and troubleshooting:

```powershell
uv run python -m scripts.setup_agent365 --capture
```

Generated files, do not commit:

```text
.local\<suffix>\agent365\a365.config.json
.local\<suffix>\agent365\a365.generated.config.json
.local\<suffix>\agent365\openclaw-agent365-identifiers.json
```

Configure the blueprint in Teams Developer Portal:

```text
https://dev.teams.microsoft.com/tools/agent-blueprint/<agentBlueprintId>/configuration
```

Use:

```text
Agent Type: API Based
Notification URL: https://<bridge-fqdn>/api/messages
```

If the bridge URL changes later, update only the Agent 365 endpoint registration:

```powershell
cd D:\agent-demos\openclaw-on-azure\.local\<suffix>\agent365
a365 setup blueprint --update-endpoint https://<new-bridge-fqdn>/api/messages
```

Publish the package and upload it in Microsoft 365 admin center:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.setup_agent365 --publish
```

Current demo state:

```text
Setup done: blueprint created, permissions verified with `a365 query-entra inheritance`, bridge endpoint registered.
Package ready: D:\agent-demos\openclaw-on-azure\.local\ehvw\agent365\manifest\manifest.zip
IDs file:      D:\agent-demos\openclaw-on-azure\.local\ehvw\agent365\openclaw-agent365-identifiers.json
```

Next manual steps:

1. Open the `developerPortalConfigurationUrl` from the IDs file, set **Agent Type** to **API Based**, and set **Notification URL** to the bridge `/api/messages` URL.
2. Upload `.local\<suffix>\agent365\manifest\manifest.zip` at:

```text
https://admin.microsoft.com -> Agents -> All agents -> Upload custom agent
```

3. Create/request the instance from Teams Apps. If admin approval is required, approve it at:

```text
https://admin.cloud.microsoft/#/agents/all/requested
```

4. Search for the agent user in Teams and send a smoke message. The bridge should receive the same `/api/messages` traffic path as the sideloaded Teams package.

Ownership boundary:

| Surface | Owner |
| --- | --- |
| Existing Teams sideload manifest, RSC permissions, preview targeted-message package | `teams\manifest.template.json` and `scripts.package_teams_app` |
| Bridge `/api/messages` runtime, Teams event routing, quoting/reactions | `bridge\` and `terraform\apps` app settings |
| Agent 365 blueprint, instance, agent user lifecycle, admin-center publishing | `.local\<suffix>\agent365\` generated config plus `a365` CLI |
| Generated tenant IDs and agent user metadata | `.local\<suffix>\agent365\openclaw-agent365-identifiers.json` |

Cleanup commands are destructive; preview first:

```powershell
cd D:\agent-demos\openclaw-on-azure\.local\<suffix>\agent365
a365 cleanup --dry-run
a365 cleanup instance --dry-run
a365 cleanup blueprint --endpoint-only
```

## 8. Demo script

### 1:1 chat

Send:

```text
List services from private incidents MCP
```

Expected: OpenClaw answers in the 1:1 chat. The bridge can use Teams streaming here.

### Channel mention

In a channel where OpenClaw is installed, start a new post:

```text
@OpenClaw Hi, can you hear me? Who am I?
```

Expected: OpenClaw adds a temporary eyes reaction if Teams accepts bot reactions, then removes it after OpenClaw has decided whether to answer. If it answers, the response appears in the same thread as a quoted/threaded reply. Final semantic reactions such as heart, smile, surprised, or check are decided by OpenClaw.

### Reply in an active OpenClaw thread

Reply to OpenClaw's channel answer:

```text
OK, what can you do?
```

Expected: OpenClaw treats this as an active thread and answers even without another `@OpenClaw`.

### Plain text bot name without Teams mention

Start a new channel post:

```text
Maybe OpenClaw should say hi even when I do not tag it, right?
```

Expected: the bridge treats plain-text `OpenClaw` as a strong signal and OpenClaw should answer.

### Weak signal where OpenClaw may stay silent

Start a new channel post:

```text
We will run the database migration on Friday evening.
```

Expected: with RSC consent, the bridge forwards this as weak context. OpenClaw may correctly stay silent by returning `NO_RESPONSE`.

### Strong weak-signal intervention

Start a new channel post:

```text
I suggest running the production database migration during peak hours without a rollback plan.
```

Expected: the bridge forwards this as observed context; OpenClaw should decide to jump in because it can prevent a material mistake. Good demo detail: the temporary eyes reaction is removed when the decision is done; OpenClaw may add a surprised reaction to the original risky message and then answer with a concise warning.

### Reactions

Add a heart or like to an OpenClaw message.

Expected: the bridge receives a `messageReaction` event. The design is to forward reactions to OpenClaw's own messages as feedback context and ignore reactions to unrelated human messages by default.

Reply in an active OpenClaw thread:

```text
thanks!
```

Expected: OpenClaw adds a like reaction and does not send a noisy text reply.

OpenClaw can also request a Teams reaction with an internal control line that is removed before the text is shown. It can use the control line by itself for reaction-only acknowledgement, or combine it with normal visible text:

```text
TEAMS_REACTION: eyes | like | heart | smile | surprised | check
```

Suggested meanings:

```text
eyes       working / looking into it
like       thanks or simple acknowledgement
heart      user praises OpenClaw
smile      light joke related to OpenClaw or its previous answer
surprised  risky or alarming proposal
check      done / confirmed
```

## 9. Troubleshooting

Check bridge health:

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\apps
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/health"
```

Check Teams diagnostics:

```powershell
Invoke-RestMethod "$bridge/diag/teams" | ConvertTo-Json -Depth 12
```

Useful events:

```text
handler              incoming Teams activity reached bridge
typingSent           typing indicator sent
reactionSent         bot reaction sent
reactionSendFailed   Teams rejected bot reaction write
reactionDeleted      temporary bot reaction removed
reactionDeleteFailed Teams rejected temporary reaction removal
messageReaction      user reaction received
ignoredReactionToNonOpenClawMessage  reaction ignored because it was not on an OpenClaw message
responseSent         answer sent to Teams
responseSuppressed   OpenClaw returned NO_RESPONSE or emoji-only acknowledgement
backgroundException  OpenClaw/bridge failure
```

Common fixes:

```text
No channel messages: reinstall/update the Teams app and approve RSC permissions.
No channel answer: check /diag/teams for responseSent or backgroundException.
No bot reactions: reactionSendFailed means Teams rejected reaction writes in that scope; text answers still work.
Preview package rejected: use the normal package; targeted messages require preview schema support in the tenant/client.
Agent 365 instance is not visible in Teams: confirm the Developer Portal blueprint configuration uses API Based and the deployed bridge `/api/messages` notification URL, then wait 5-10 minutes for propagation.
Upload custom app missing: Teams sideloading is disabled for your user or tenant.
```

## 10. Local validation

Run targeted tests after bridge/script changes:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m unittest tests.test_teams_bridge
uv run python -m compileall bridge scripts tests -q
```

## 11. Cleanup

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\apps
terraform destroy

cd D:\agent-demos\openclaw-on-azure\terraform\platform
terraform destroy
```

Optional local cleanup:

```powershell
Remove-Item D:\agent-demos\openclaw-on-azure\.local -Recurse -Force
Remove-Item D:\agent-demos\openclaw-on-azure\terraform\apps\generated.*.auto.tfvars.json -Force
```
