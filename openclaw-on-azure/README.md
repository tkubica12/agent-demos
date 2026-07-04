# OpenClaw on Azure

Run OpenClaw Gateway in ACA Sandboxes. Keep a tiny standard Azure Container Apps bridge reachable for Teams and direct HTTP calls. The bridge wakes the sandbox and forwards user turns to OpenClaw.

## Current target

```text
Terraform platform
  -> rg-openclaw-xxxx
  -> ACR
  -> VNet + private DNS
  -> ACA private MCP environment
  -> standard ACA bridge environment
  -> ACA SandboxGroup
  -> Foundry + model

Build script
  -> ACR remote builds
  -> image digests in terraform\apps\generated.images.auto.tfvars.json

Terraform apps
  -> private incidents MCP Container App
  -> standard ACA bridge app
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
bridge\               standard ACA bridge, /health, /invoke, and /api/messages
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

Generates gateway token and bridge device key. Writes tfvars. The bridge uses a managed identity for Azure API calls.

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

The bridge uses standard Azure Container Apps, not ACA Express. See `docs\adr\0001-standard-aca-bridge.md` for the Teams outbound TLS limitation that caused this decision.

## Current deployed test environment

Latest fresh run created:

```text
Resource group: rg-openclaw-ehvw
ACR:            oclawehvw
Bridge app:     ocbridge-ehvw
Bridge URL:     Run `terraform output -raw bridge_url` in `terraform\apps`
Private MCP:    ocmcp-ehvw
Bridge volume:  openclaw-bridge-e2e-clean
```

Bridge `/health` works.

Bridge `/invoke` now works after bridge device approval.

## 7. Teams app

This adds the Teams surface over the same bridge:

```text
Teams chat/channel -> bridge /api/messages -> OpenClaw Gateway -> Teams reply
```

Prepare a single-tenant Teams bot app registration and generated app tfvars:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.setup_teams_tfvars
```

Rebuild the bridge image and apply apps so Terraform owns the Azure Bot resource, Teams channel, and bridge app settings:

```powershell
uv run python -m scripts.build_images
cd D:\agent-demos\openclaw-on-azure\terraform\apps
terraform apply
```

Package the Teams app:

```powershell
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.package_teams_app
```

Upload the printed `.local\<suffix>\teams\openclaw-teams.zip` package to Teams. The app supports personal chats, group chats, team channel threads, weak-signal message observation with RSC, and message reaction events.

To build an optional public-preview package for Teams targeted private messages, run:

```powershell
uv run python -m scripts.package_teams_app --preview-targeted-messages --output .local\ehvw\teams\openclaw-teams-targeted-preview.zip
```

Use the normal package first. The preview package switches the manifest schema to `1.29` and adds `supportsTargetedMessages: true`. Some Teams upload validators still reject this public-preview capability if the tenant/client is not enabled for the preview schema.

For the current environment, the package is:

```text
D:\agent-demos\openclaw-on-azure\.local\ehvw\teams\openclaw-teams.zip
```

Install it in Teams:

1. Open **Microsoft Teams**.
2. Go to **Apps**.
3. Choose **Manage your apps** or **Upload a custom app**.
4. Select **Upload an app** / **Upload a custom app**.
5. Pick `D:\agent-demos\openclaw-on-azure\.local\ehvw\teams\openclaw-teams.zip`.
6. Choose **Add** to install it for yourself, or add it to a group chat/team when testing collaborative scopes.
7. If Teams shows a consent prompt for resource-specific permissions, approve it. These permissions allow OpenClaw to receive unmentioned messages in the installed chat/team:

```text
ChannelMessage.Read.Group
ChatMessage.Read.Chat
```

If the app was already installed before these permissions were added, remove and reinstall or update the app in the target chat/team so Teams re-prompts for consent.
8. Open a 1:1 chat with **OpenClaw** and send a prompt such as:

```text
List services from private incidents MCP
```

Expected result: Teams sends the message to `https://ocbridge-ehvw.icymeadow-c517d14c.swedencentral.azurecontainerapps.io/api/messages`, the bridge wakes/reuses the ACA Sandbox, and OpenClaw replies in the current chat or channel thread.

For group chats and channel threads, mention the bot and include the prompt:

```text
@OpenClaw List services from private incidents MCP
```

The bridge accepts `groupchat`, `channel`, and `team` conversation types. When OpenClaw is mentioned, the bridge strips the bot mention before forwarding the prompt and preserves the Teams conversation/thread in the OpenClaw session key.

Every Teams turn sent to OpenClaw includes a metadata envelope so the agent can distinguish:

```text
Signal type: explicit_bot_mention | targeted_private_message | textual_bot_name_mention | reply_in_thread_without_bot_mention | reaction_to_message | undirected_message
Response contract: must_answer | observe_then_maybe_answer
Conversation type / id
Activity id
Reply-to activity id
Sender
Targeted private message flag
Bot explicitly mentioned flag
Reaction types, when present
```

For mention and targeted private messages, OpenClaw must answer. For unmentioned public messages and reactions received through RSC/activity events, the bridge sends the event to OpenClaw as context and suppresses the reply if OpenClaw returns exactly `NO_RESPONSE`.

Plain-text references to `OpenClaw` without a Teams mention are treated as `textual_bot_name_mention` and use `must_answer`. Channel replies in a thread where OpenClaw has already answered are also promoted to `must_answer`; Teams channel replies can carry the thread root in `conversation.id` as `;messageid=...`, so the bridge keys memory by that root instead of by each reply activity.

### Teams context window sent to OpenClaw

The bridge does not send the whole Teams conversation and does not send only the latest message. It keeps a bounded in-memory history per Teams session/thread and sends OpenClaw:

```text
1. Current Teams event envelope
2. Recent local window for the same session/thread
3. Reply/reacted-to anchor when the referenced message is in bridge memory
4. Latest OpenClaw answer when available
5. Current message text
```

Default context policy:

| Signal | Recent events sent |
| --- | --- |
| `explicit_bot_mention`, `targeted_private_message`, 1:1 | 18 |
| `reply_in_thread_without_bot_mention` | 12 |
| `undirected_message` | 8 |
| `reaction_to_message` | 6 |

The context is limited by both event count and characters. Current knobs:

```text
OPENCLAW_TEAMS_MEMORY_MAX_EVENTS=30
OPENCLAW_TEAMS_MEMORY_EVENT_CHARS=1200
OPENCLAW_TEAMS_CONTEXT_MAX_CHARS=12000
OPENCLAW_TEAMS_CONTEXT_MUST_ANSWER_EVENTS=18
OPENCLAW_TEAMS_CONTEXT_REPLY_EVENTS=12
OPENCLAW_TEAMS_CONTEXT_WEAK_SIGNAL_EVENTS=8
OPENCLAW_TEAMS_CONTEXT_REACTION_EVENTS=6
```

This memory is intentionally bridge-local and demo-grade. It survives while the bridge replica is warm, but it is not durable across bridge restarts or scale-out replicas. Production should replace or augment it with durable conversation state plus Teams Graph/RSC retrieval.

Response delivery and UX policy:

```text
1:1 personal chats     -> Teams streaming response
group chats/channels   -> quoted Teams reply into the current conversation/thread
must-answer work       -> eyes reaction on the triggering message when supported
gratitude in thread    -> like reaction instead of noisy text, when supported
```

The bridge intentionally does not use Teams streaming for group chat or channel replies because channel validation showed Teams accepted the incoming activity and OpenClaw completed, but streaming updates were not visible in the channel UI. Microsoft documents streaming bot messages as a Teams SDK capability for AI-powered bots, but the reliable path for this deployed channel demo is a normal Teams message. For collaborative scopes the bridge uses `ctx.reply()` by default so Teams renders a quoted reply and keeps channel responses in the current thread. Set `OPENCLAW_TEAMS_QUOTED_REPLIES=false` to fall back to unquoted `ctx.send()`.

Bot reactions use the documented Teams SDK reaction API (`ctx.api.reactions.add`). `OPENCLAW_TEAMS_ADD_REACTIONS=true` by default. The bridge adds an eyes reaction when OpenClaw must answer, and a like reaction when a user posts a simple thanks in an active OpenClaw thread. If a target Teams scope rejects reaction writes, the bridge records `reactionSendFailed` in `/diag/teams` and continues normally.

To test the current Teams behavior:

1. **1:1**: send `List services from private incidents MCP`; OpenClaw should answer.
2. **Group chat mention**: add OpenClaw to a group chat and send `@OpenClaw List services from private incidents MCP`; OpenClaw should react with eyes while working and answer.
3. **Channel mention**: add OpenClaw to a team, open a standard channel thread, and send `@OpenClaw List services from private incidents MCP`; OpenClaw should answer in that thread.
4. **Weak signal**: in the same group chat or channel, send a normal message without mentioning OpenClaw, for example `We should run the production migration during peak traffic.` With RSC consent, the bridge receives it and lets OpenClaw decide whether to jump in; no answer is expected if OpenClaw returns `NO_RESPONSE`.
5. **Reaction event**: add a reaction to a message in the group/chat thread. The bridge records and forwards reaction events as context; OpenClaw replies only if it decides the event needs a public answer.
6. **Targeted private message preview**: the bridge runtime can recognize and answer targeted private messages if Teams sends them. Build and upload the preview package with `--preview-targeted-messages` only in a tenant/client where the Teams upload validator accepts `supportsTargetedMessages`.

### Suggested Teams test script

Use these examples in a standard channel where OpenClaw is installed. Start with a clean channel thread so it is easy to see what happened.

| Scenario | Message/action | Expected behavior |
| --- | --- | --- |
| Explicit mention | `@OpenClaw Ahoj, slyšíš mě? Kdo jsem?` | OpenClaw answers in the channel thread. |
| Reply in OpenClaw thread | Reply to OpenClaw's answer with `dobře, co umíš?` | OpenClaw treats this as part of an active OpenClaw thread and answers, even without another `@OpenClaw`. |
| Plain-text name, no Teams mention | New channel post: `Možná by mohl OpenClaw říct ahoj, i když ho netaguji, ne?` | OpenClaw treats plain-text `OpenClaw` as `textual_bot_name_mention` and should answer. |
| Weak untagged context | New channel post: `Budeme dělat migraci databáze v pátek večer.` | Bridge sends it to OpenClaw as weak context. OpenClaw may stay silent by returning `NO_RESPONSE`. |
| Strong weak-signal intervention | New channel post: `Navrhuji spustit produkční migraci databáze během špičky bez rollback plánu.` | OpenClaw should jump in because it can prevent a material mistake. |
| Emoji received | Add a heart/like to OpenClaw's answer. | Bridge receives a `reaction_to_message` event and sends it to OpenClaw as feedback context. It usually stays silent unless follow-up is useful. |
| Quoted/threaded answer | `@OpenClaw Ahoj, slyšíš mě? Kdo jsem?` | In a channel, OpenClaw should answer in the current thread with a visual quote of the triggering message. |
| Emoji/status sent by bot | Mention OpenClaw or reply in an active thread. | OpenClaw should add eyes while working if Teams accepts reaction writes. |
| Thanks acknowledgement | Reply in the OpenClaw thread with `díky!` | OpenClaw should add a like reaction and avoid a noisy text reply. |
| Targeted private message preview | Package with `--preview-targeted-messages` and test in a preview-enabled Teams client/tenant. | If Teams sends `recipient.is_targeted`, the bridge responds privately to the targeted user. Preview messages expire and do not support reactions/replies/forwarding. |

When troubleshooting, call:

```powershell
cd D:\agent-demos\openclaw-on-azure\terraform\apps
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/diag/teams" | ConvertTo-Json -Depth 12
```

Look for `handler`, `messageReaction`, `responseSent`, `responseSuppressed`, and `backgroundException` events.

If **Upload a custom app** is not available, Teams app sideloading is disabled for your user or tenant. Enable custom app upload in Teams admin settings or use a tenant/user where custom apps are allowed, then retry the same zip.

## What not to do yet

- Do not move Teams webhook handling into the sandbox.
- Do not call private MCP directly from the bridge.
- Do not add Work IQ MCP until Teams/Agent identity shape is clear.

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
