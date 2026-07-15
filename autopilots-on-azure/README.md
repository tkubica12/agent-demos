# Autopilots on Azure

Host OpenClaw and Hermes autopilot runtimes in Azure Container Apps Sandboxes behind runtime-specific bridge apps. Each bridge receives `/invoke` and Agent 365 `/api/messages` traffic, wakes or reuses its runtime sandbox, forwards the turn, and returns the response.

The active plan and specification is in `AUTOPILOTS_SPEC.md`.

## Current deployment model

OpenClaw and Hermes run side by side:

| Runtime | Terraform workspace | Bridge app | Sandbox port | Notes |
| --- | --- | --- | --- | --- |
| OpenClaw | `autopilot-openclaw` | `autopilot-bridge-openclaw-*` | `18789` | Requires OpenClaw Gateway device approval. |
| Hermes | `autopilot-hermes` | `autopilot-bridge-hermes-*` | `8642` | Uses Hermes API server and `API_SERVER_KEY`. |

The OpenClaw image intentionally installs `openclaw@latest` so the demo follows the current product. The latest live-tested version is `2026.7.1`; when a newer release is pulled, repeat the OpenClaw smoke and device-approval checks before treating it as the new baseline.

Microsoft 365 app installation is **Agent 365 only**:

- Do not use Teams sideloading.
- Do not create Azure Bot Service resources.
- Do use Agent 365 blueprint setup plus the Microsoft 365 admin center **Upload custom agent** flow.

Teams channel participation is still the goal. For Agent 365 AI teammates, Teams availability comes after package upload through Developer Portal blueprint configuration and Teams agent-instance creation. Do not fall back to Teams sideloading or Azure Bot Service.

## Fast path from the current checked-out demo

If platform/apps were already deployed and packages were already generated, start here:

```powershell
Set-Location .\autopilots-on-azure

uv run python -m scripts.demo_ops status --runtime both
```

Then open Microsoft 365 admin center and upload the Agent 365 packages. This step is still manual: `uv run python -m scripts.setup_agent365 --runtime <runtime> --publish` calls `a365 publish`, but the Agent 365 CLI only creates a ZIP package for admin-center upload.

```text
https://admin.microsoft.com
  -> Agents
  -> All agents
  -> Upload custom agent
```

Upload one or both packages:

```text
.local\openclaw\agent365\manifest\manifest.zip
.local\hermes\agent365\manifest\manifest.zip
```

This is tenant/admin-center publishing, not Teams sideloading. If the upload page shows **Host products: Copilot**, continue; the Teams step is creating the Agent 365 instance from Teams Apps after Developer Portal blueprint configuration. There is no repository script or current `a365` command that uploads this package for us; existing package scripts only create the ZIP and clean/block stale catalog rows.

## Architecture

The durable architecture, trust boundaries, identity flows, networking, state ownership, and component responsibilities are documented in [ARCHITECTURE.md](ARCHITECTURE.md). This section is only the operator-oriented summary.

```text
Agent 365 / /invoke
  -> runtime-specific bridge Container App
  -> runtime-specific ACA Sandbox
       -> OpenClaw Gateway
       -> Hermes API server
       -> loopback Agent Identity MCP adapter
            -> private incidents MCP over Sandbox VNet connection
            -> public shipments MCP over HTTPS
            -> Agent 365 Work IQ Mail MCP
```

Key folders:

```text
terraform\platform\       shared Azure substrate
terraform\apps\           bridge, private MCP, and public BYO MCP app resources
runtimes\openclaw\        OpenClaw Gateway sandbox image
runtimes\hermes\          Hermes API server sandbox image
bridge\                   FastAPI bridge: /health, /invoke, /internal/dream, /api/messages
bridge\runtime\           runtime adapters: OpenClaw and Hermes
private-incidents-mcp\    mock private MCP server
public-shipments-mcp\     public scale-to-zero MCP server for Agent 365 BYO
autopilots_identity\      Agent Identity/Agent User token exchange and MCP adapter
agent365\                 committed Agent 365 Tooling manifest
scripts\                  setup, build, Agent 365, sandbox helpers
docs\adr\                 architecture decision records
```

## Operator console

A6 adds a single operator helper for day-two demo work. Use it before opening Teams or the portal:

```powershell
uv run python -m scripts.demo_ops status --runtime both
uv run python -m scripts.demo_ops status --runtime both --invoke
uv run python -m scripts.demo_ops dream
```

`status` checks the captured runtime bridge URLs under `.local\<runtime>\apps\terraform-outputs.json`. With `--invoke`, it also runs the direct bridge smoke prompt and checks expected markers. `dream` reads the local Hermes operator key, wakes or reuses the worker Sandbox, submits an isolated reflection session, and prints the resulting redacted learning packet.

Switch the active Terraform tfvars to one runtime without guessing which generated file is live:

```powershell
uv run python -m scripts.demo_ops activate --runtime openclaw
terraform -chdir=terraform\apps workspace select autopilot-openclaw
terraform -chdir=terraform\apps plan

uv run python -m scripts.demo_ops activate --runtime hermes
terraform -chdir=terraform\apps workspace select autopilot-hermes
terraform -chdir=terraform\apps plan
```

For log triage, print the exact Azure Container Apps log command first. Add `--execute` only when you want the script to run it:

```powershell
uv run python -m scripts.demo_ops logs --runtime openclaw --app bridge
uv run python -m scripts.demo_ops logs --runtime hermes --app bridge --tail 120 --execute
```

If a runtime sandbox is running but its service port no longer answers, delete only that sandbox and keep the data volume. Dry-run first:

```powershell
uv run python -m scripts.demo_ops grant-sandbox-access
uv run python -m scripts.demo_ops grant-sandbox-access --execute
uv run python -m scripts.demo_ops reset-sandbox --runtime hermes
uv run python -m scripts.demo_ops reset-sandbox --runtime hermes --execute
uv run python -m scripts.demo_ops smoke --runtime hermes
```

`reset-sandbox` uses the ACA Sandbox data-plane resource `https://dynamicsessions.io`. If it returns `ERROR: Forbidden`, the current Azure login can still operate normal Azure resources but cannot list/delete sandboxes through the sandbox data plane. Run `grant-sandbox-access --execute` with an identity allowed to create role assignments, or use an identity that already has **Container Apps SandboxGroup Data Owner** on the sandbox group.

## Hermes blueprint lifecycle

The distribution is committed at `blueprints\junior-project-manager`. Hosted workers install it from a commit-pinned Git source into `/data/hermes/profiles/junior-project-manager`; private state remains on the existing Hermes Data Disk. The current release is v2.2.0.

Configure one Hermes worker with the repository commit that contains the desired blueprint version:

```powershell
$commit = "<full-40-character-remote-commit-sha>"

uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --blueprint-name junior-project-manager `
  --blueprint-source https://github.com/tkubica12/agent-demos.git `
  --blueprint-path autopilots-on-azure/blueprints/junior-project-manager `
  --blueprint-version 2.2.0 `
  --blueprint-commit $commit `
  --assignee-scope "person-or-team" `
  --runtime-only

uv run python -m scripts.deploy_apps_runtime --runtime hermes --apply --auto-approve --capture
```

The source must be reachable from the sandbox without an interactive Git prompt. Do not embed credentials in the URL; the source is deployment metadata and is recorded in the instance manifest. The commit is intentionally immutable. On restart with the same commit, Hermes reuses the installed profile without fetching Git.

To upgrade, bump `distribution.yaml`, commit the reviewed blueprint changes, and rerun the two commands with the new version and full commit SHA. The changed sandbox label replaces the sandbox container but reuses the same Data Disk. The upgrade replaces only `distribution_owned` paths and preserves:

```text
memories\
sessions\
state.db*
logs\
workspace\
.env
local\
skills outside skills\junior-project-manager\
learning\records.jsonl
```

The instance record is stored at:

```text
/data/hermes/profiles/junior-project-manager/local/autopilots-instance.json
```

The sandbox labels and `/data/hermes/profiles/junior-project-manager/local/autopilots-instance.json` report the installed blueprint name, version, and commit. The native Hermes `/health` endpoint reports Hermes runtime health and version. OpenClaw remains on its runtime-image plus persistent `/data/home` and `/data/workspace` model; A8 does not add a parallel custom distribution manager for it.

## Local learning and dreaming

Blueprint v2.2.0 classifies learning before storage and adds the `dream-reflection` skill. Private personal/team context, cache, raw sessions, memory, `.env`, auth, logs, workspace content, and `state.db*` remain instance-local. Transferable candidates can be appended only through the runtime validator:

```text
/data/hermes/profiles/junior-project-manager/learning/records.jsonl
```

Run a manual reflection through the secured bridge operation:

```powershell
uv run python -m scripts.demo_ops dream
uv run python -m scripts.demo_ops dream --focus "Review recurring delivery-risk escalation patterns" --max-records 3
```

The command uses the Hermes `api_server_key` from `.local\hermes\apps\generated.app.auto.tfvars.json`; the key is not printed. The bridge uses the stable `dream:<instance-id>` session. Hermes returns generalized candidates in a bounded JSON block, then the trusted runtime validates and appends them without requiring agent shell approval. The response includes only records that match schema v1.0 and pass deterministic checks for credentials, tokens, email addresses, GUIDs, IP addresses, and user-specific absolute paths. Rejected or private observations stay local. Recurring scheduling remains A11.

The A9 flow was live-verified on 2026-07-15 with blueprint commit `c785192de8bca5eceb243dd0f02f0f1886fdec6a`. One dream run produced three accepted procedural records with no redaction rejection. A separate run consolidated one assignment-specific formatting preference into private local memory and added no transferable record; the returned packet continued to expose only the three validated records and the private-path exclusion manifest.

The complete v1-to-v2 lifecycle was live-verified on 2026-07-13. Hermes installed v1.0.0 from `ecc07fad92122d6ae6d4e44bd145c1814a746071`, wrote private memory/session/local skill markers, then installed v2.0.0 from `50342bd359a3f0fce9669a43b1d6eeb4fa690900` in a replacement sandbox using the same `hermes-data` volume. The v2 distribution files changed, every private marker and native `state.db` survived, and the private incidents MCP still returned the expected five services.

## Prerequisites

```powershell
az login --use-device-code
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

Builds one runtime plus the common bridge, private MCP, and public shipments MCP images in ACR. The script updates that runtime's scoped tfvars and the active Terraform tfvars together.

```powershell
uv run python -m scripts.build_images --runtime openclaw
uv run python -m scripts.build_images --runtime hermes
```

Generated files, do not commit:

```text
.local\<runtime>\apps\generated.app.auto.tfvars.json
terraform\apps\generated.app.auto.tfvars.json
terraform\apps\generated.runtime.auto.tfvars.json
```

## 3. Generate app bootstrap values

Creates runtime-specific app bootstrap values. The script writes the active Terraform tfvars file and a runtime-scoped copy under `.local\<runtime>\apps\` so OpenClaw and Hermes values do not collide.

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

## 4. Deploy apps

Deploys the private MCP Container App, public scale-to-zero shipments MCP, bridge Container App, app settings/secrets, and image digests. A5 uses one Terraform workspace per runtime so OpenClaw and Hermes stay live side by side instead of replacing each other in one state file.

```powershell
uv run python -m scripts.deploy_apps_runtime --runtime openclaw --apply
uv run python -m scripts.deploy_apps_runtime --runtime hermes --apply
```

Use `--plan` instead of `--apply` when you only want to inspect changes. Each apply captures runtime outputs for Agent 365:

```text
.local\openclaw\apps\terraform-outputs.json
.local\hermes\apps\terraform-outputs.json
```

Check the bridge:

```powershell
Set-Location .\terraform\apps
terraform workspace select autopilot-openclaw
$bridge = terraform output -raw bridge_url
Invoke-RestMethod "$bridge/health"
Set-Location ..\..
```

Expected:

```json
{"status":"ok"}
```

## 5. Configure A7 identity and MCP permissions

The Sandbox Group managed identity is only the workload credential. A federated identity credential on each Agent 365 blueprint exchanges it into the runtime's Agent Identity; Agent User tokens are used only for worker-owned Microsoft 365 resources.

```text
Sandbox Group managed identity
  -> blueprint federated identity credential
  -> Agent Identity token
       -> private incidents app role: Incidents.Read.All
       -> public shipments app role: Shipments.Read.All
  -> Agent User token
       -> Work IQ Mail delegated scope: Tools.ListInvoke.All
```

Configure each runtime:

```powershell
uv run python -m scripts.setup_a7_identity --runtime openclaw
uv run python -m scripts.setup_a7_identity --runtime hermes
```

The script creates or reuses the two MCP Entra resource applications, assigns application roles to the Agent Identity, grants the existing Agent User the Work IQ Mail scope, copies `agent365\ToolingManifest.json`, and applies Agent 365 MCP permissions.

The private server remains VNet-only. `Microsoft.App/sandboxGroups/vnetConnections` provides private DNS and network reachability; it does not perform MCP authentication. The runtime-local adapter on `127.0.0.1:18081` performs the documented blueprint/Agent Identity exchanges and streams MCP traffic directly to the upstream server. No MCP relay or shared API key exists in the public bridge.

The public shipments server is a separate external ACA MCP endpoint with Entra OAuth and scale-to-zero. Register it as Agent 365 BYO MCP:

```powershell
terraform -chdir=terraform\apps workspace select autopilot-openclaw
uv run python -m scripts.register_a7_byo_mcp --runtime openclaw --dry-run
uv run python -m scripts.register_a7_byo_mcp --runtime openclaw
```

BYO registration is preview and requires a public endpoint. Approval remains in Microsoft 365 admin center under **Agents -> Tools -> Requests**. Current supported BYO invocation surfaces do not include OpenClaw or Hermes, so those runtimes call the same public endpoint directly with their Agent Identity; the BYO registration separately demonstrates Agent 365 registry, approval, gateway, policy, and Defender telemetry.

The current `ext_Shipments` registration is approved. During preview, the CLI-generated backing applications can require consent repair before portal approval:

```powershell
uv run python -m scripts.register_a7_byo_mcp --runtime openclaw --repair-consent
```

This idempotently creates missing service principals, enables the generated public client, grants the permissions declared by each backing application, and assigns the approving admin. After approval:

```powershell
uv run python -m scripts.register_a7_byo_mcp --runtime openclaw --mark-approved
```

If the consent popup repeatedly uses an account from the wrong tenant, use an isolated/InPrivate browser profile and sign in explicitly with the tenant administrator.

Live validation confirmed that the approved `ext_Shipments` endpoint accepts the generated public-client token and initializes through the Agent 365 gateway. A raw MCP client receives an empty tool catalog because it does not perform the supported-client connection/OAuth handshake. Complete BYO tool invocation from Copilot Studio, Visual Studio Code, Claude Code, or GitHub Copilot CLI; do not treat the gateway URL as a generic custom-runtime endpoint.

## 6. OpenClaw-only device approval

Skip this step when `AGENT_RUNTIME=hermes`.

When the OpenClaw bridge first invokes its sandbox, it usually stops on bridge device approval:

```powershell
Set-Location .\terraform\apps
terraform workspace select autopilot-openclaw
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

Open the printed Gateway URL and paste the printed Gateway token. In the current Control UI, select **Devices** (monitor icon), find **Pending approval**, and approve the bridge device whose ID matches the printed `deviceId`.

The upstream CLI equivalent is `openclaw devices list --json` followed by `openclaw devices approve <requestId>`. Those commands must run inside the OpenClaw sandbox, so the Control UI is the normal operator path for this demo.

## 7. Validate selected runtime with `/invoke`

OpenClaw expected response:

```powershell
Set-Location .\terraform\apps
terraform workspace select autopilot-openclaw
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

Hermes expected response:

```powershell
Set-Location .\terraform\apps
terraform workspace select autopilot-hermes
$bridge = terraform output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"hermes-smoke","message":"Reply with exactly: Hermes bridge OK"}'
Set-Location ..\..
```

## 8. Register and publish Agent 365

Agent 365 is the Microsoft 365 installation path for both runtimes.

The deployed bridge is an externally hosted Agent 365 messaging endpoint:

```text
https://<bridge-fqdn>/api/messages
```

Prepare runtime-specific Agent 365 workspaces. This writes local config and prints the exact `a365` commands:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw
uv run python -m scripts.setup_agent365 --runtime hermes
```

When `.local\<runtime>\apps\terraform-outputs.json` exists, `setup_agent365` uses that runtime's captured bridge URL automatically. You can override it with `--messaging-endpoint https://<bridge-fqdn>/api/messages`.

Run setup when you are ready to create or update tenant resources. Use an admin-capable account if blueprint creation or consent needs it:

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

After `a365.generated.config.json` exists, push the Agent 365 SDK auth settings into the runtime app tfvars and re-apply the runtime apps stack. This decrypts the locally protected blueprint secret only into ignored Terraform tfvars so the bridge can use the Microsoft Agents SDK, not the old Bot Framework send path:

```powershell
uv run python -m scripts.setup_app_tfvars --runtime hermes --agent365-from-generated --runtime-only
uv run python -m scripts.deploy_apps_runtime --runtime hermes --apply --auto-approve --capture
```

Repeat for OpenClaw when validating its Agent 365 instance:

```powershell
uv run python -m scripts.setup_app_tfvars --runtime openclaw --agent365-from-generated --runtime-only
uv run python -m scripts.deploy_apps_runtime --runtime openclaw --apply --auto-approve --capture
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

Build the upload package. This command name mirrors the Agent 365 CLI, but it does **not** upload to Microsoft 365 admin center; it updates manifest IDs and creates/repackages the local ZIP:

```powershell
uv run python -m scripts.setup_agent365 --runtime openclaw --publish
uv run python -m scripts.setup_agent365 --runtime hermes --publish
```

The generated packages are:

```text
.local\openclaw\agent365\manifest\manifest.zip
.local\hermes\agent365\manifest\manifest.zip
```

Upload them through Microsoft 365 admin center:

```text
https://admin.microsoft.com
  -> Agents
  -> All agents
  -> Upload custom agent
```

No script currently replaces this upload step. `a365 publish --help` describes the command as "create a package for upload to Microsoft 365 Admin Center", and the repository's package-management helper only lists/blocks stale package catalog rows.

Automation boundary:

| Operation | Automated? | Command or location |
| --- | --- | --- |
| Create/update the Agent 365 blueprint and permissions | Yes | `scripts.setup_agent365 --run-setup` |
| Generate/repackage `manifest.zip` | Yes | `scripts.setup_agent365 --publish` |
| Upload or upgrade the package in the tenant catalog | No | Microsoft 365 admin center **Agents -> All agents -> Upload custom agent** |
| Create Agent identity, Agent user, assign licenses, and register the instance | Yes | `scripts.provision_agent365_instance provision --register` |
| Remove scripted identity/user/registration state | Yes | `scripts.provision_agent365_instance cleanup --instance` |
| List/block stale Agent 365 package rows | Yes | `scripts.provision_agent365_instance cleanup-packages --block` |

This distinction explains the earlier mostly automated flow: the package was uploaded manually once, then `scripts.provision_agent365_instance` replaced the failing portal **Add Instance** orchestration. It did not upload the package itself.

Do not use Teams app sideloading or Azure Bot Service. Those paths were removed.

Agent 365 AI teammates are Agent Users added as members of Teams. They use the same Activity Protocol envelope and connector family as Teams bots, but they are not installed Teams app/bot instances. The different installation and consent lifecycle matters: the current Agent User path receives direct messages, explicit mentions, and targeted activities, but it does not receive every unmentioned channel message or automatically follow a thread after one mention.

| Teams activity | Current Agent 365 AI teammate behavior |
| --- | --- |
| 1:1 message | Delivered and live-verified |
| Explicit channel mention | Delivered and live-verified |
| Unmentioned channel message | Not delivered |
| Unmentioned reply after the agent participated in the thread | Not delivered in the live test |
| Outbound reactions | Public developer preview; temporary `eyes` and semantic `heart` live-verified |
| Inbound reaction to an agent-authored message | Public developer preview; handler implemented, not yet live-verified |
| Typing indicator | Sent for 1:1 and small group chats; Teams does not show it in channels |

Adding Teams RSC fields to an `agenticUserTemplates` package does not change channel delivery: the tenant catalogs the package, but no Teams app is installed in the Team and no resource-specific consent grant is created. RSC all-message delivery requires a Teams app manifest with a `bots` capability and installation in the target Team. That is a separate Teams app/bot lifecycle and is outside this Agent 365-only architecture.

The Agent 365 Notifications SDK is not an ambient Teams subscription API. Its current notification types cover email, Word/Excel/PowerPoint comments, and Agent User lifecycle events. Full Teams thread history requires a separate pull through Microsoft Graph or Work IQ MCP; the bridge currently uses only delivered activities plus local in-process memory. See [ADR 0002](docs/adr/0002-teams-event-routing-and-reactions.md) for evidence, consequences, and review triggers.

Configure each Agent 365 blueprint in Teams Developer Portal:

```text
https://dev.teams.microsoft.com/tools/agent-blueprint/<agentBlueprintId>/configuration
```

Use values from `.local\<runtime>\agent365\a365.generated.config.json`:

```text
Agent Type: API Based
Notification URL: <messagingEndpoint>
```

Prefer the scripted instance path. It creates the Entra Agent ID identity, linked agent user, usage location, licenses, and then tries the Agent 365 registration API:

```powershell
# One-time per tenant: create an app-only Graph client for beta /copilot/agentRegistrations.
# Requires the current az login admin to have application/app-role assignment rights.
uv run python -m scripts.provision_agent365_instance bootstrap-registration-app

# Hermes instance. Use the tenant *.onmicrosoft.com domain for Teams-capable agent users.
uv run python -m scripts.provision_agent365_instance provision `
  --runtime hermes `
  --owner-upn tomas@tomasonline.net `
  --display-name hermes1 `
  --mail-nickname hermes1 `
  --agent-upn hermes1@MngEnvMCAP058702.onmicrosoft.com `
  --register

# OpenClaw instance, same pattern.
uv run python -m scripts.provision_agent365_instance provision `
  --runtime openclaw `
  --owner-upn tomas@tomasonline.net `
  --display-name openclaw1 `
  --mail-nickname openclaw1 `
  --agent-upn openclaw1@MngEnvMCAP058702.onmicrosoft.com `
  --register
```

Local state is written under `.local\<runtime>\agent365\instance.<mailNickname>.json`. The app-only registration client is written to `.local\agent365-registration-app.json`; it contains a client secret and must stay local.

If a scripted instance must be removed, delete it from its local state file. This removes the Agent 365 registration, agent user, and agent identity only when `--instance` is explicit:

```powershell
uv run python -m scripts.provision_agent365_instance cleanup `
  --runtime hermes `
  --mail-nickname hermes1 `
  --instance `
  --remove-state `
  --purge-deleted
```

If previous portal/uploads left stale entries in **Microsoft 365 admin center -> Agents -> All agents**, they might be tenant Teams app-catalog uploads. Delete those through the tenant app catalog. This uses delegated Graph `AppCatalog.ReadWrite.All`. Azure CLI's built-in public client cannot request that scope in this tenant, so bootstrap a tenant-local public client once, then use device-code login through that app:

```powershell
uv run python -m scripts.provision_agent365_instance bootstrap-catalog-app

uv run python -m scripts.provision_agent365_instance cleanup-catalog `
  --delete-display-names "Hermes Autopilot,hermes-foundry-a365dev" `
  --keep-display-names "Hermes Autopilot Blueprint"
```

Do not delete the current runtime blueprint unless you are intentionally rebuilding it. For the active Hermes run, keep `Hermes Autopilot Blueprint` with `agentBlueprintId` from `.local\hermes\agent365\a365.generated.config.json`.

If the stale row is only present in Agent 365 package inventory, Microsoft Graph exposes block/unblock/update but no delete API. Use package cleanup to list exact matches and block any that are not already blocked:

```powershell
uv run python -m scripts.provision_agent365_instance bootstrap-catalog-app `
  --display-name "Autopilots Agent 365 Package Cleanup" `
  --scopes CopilotPackages.ReadWrite.All `
  --output .local\agent365-package-cleanup-app.json

uv run python -m scripts.provision_agent365_instance cleanup-packages `
  --delete-display-names "Hermes Autopilot,hermes-foundry-a365dev" `
  --keep-display-names "Hermes Autopilot Blueprint,hermes1" `
  --block

uv run python -m scripts.provision_agent365_instance cleanup `
  --runtime hermes `
  --mail-nickname hermes1 `
  --package-app `
  --purge-deleted
```

The output `.local\agent365-package-cleanup.json` records matching package IDs. As of the current Agent 365 Package Management API, stale package rows can remain visible in **All agents** after backing blueprints/users are deleted; the supported cleanup action is to keep them blocked.

If the registration API is not rolled out or returns 403/404, rerun without `--register`. That still creates the Entra-backed agent identity/user and assigns licenses, which is the repeatable part of the portal **Add Instance** flow:

```powershell
uv run python -m scripts.provision_agent365_instance provision `
  --runtime hermes `
  --owner-upn tomas@tomasonline.net `
  --display-name hermes1 `
  --mail-nickname hermes1 `
  --agent-upn hermes1@MngEnvMCAP058702.onmicrosoft.com
```

Use Microsoft 365 admin center **Add Instance** only as a fallback when the Graph registration API is blocked:

```text
Microsoft 365 admin center -> Agents -> All agents -> <agent>
  -> Instances -> Add Instance
  -> set display name, alias, domain, and owner
  -> use a domain that supports Teams/OfficeCommunicationsOnline, typically the tenant *.onmicrosoft.com domain
  -> wait for the created agent user to appear in Microsoft 365/Teams
  -> add the created agent user to the target team/channel
```

This is the Agent 365 AI teammate path for Teams chats and channels.

Agent 365 AI teammate replies use the Microsoft 365 Agents SDK. Do not use Bot Framework or `microsoft-teams-apps` reply paths for Agent 365 blueprints: agentic applications are not allowed to request Bot Framework app-only tokens. Teams status and semantic reactions use the Agent 365-authenticated connector client against the Teams preview reaction endpoint. The bridge refreshes typing indicators in 1:1 and small group chats; Teams does not display them in channels.

If **Add instance** shows **You have run out of licenses** but the button is still usable, continue and submit the instance form. Treat the banner as blocking only if submit fails. The instance is a real Entra-backed agent user and consumes its associated licenses, not just the uploaded template. For the full Teams AI teammate scenario, expect Agent 365 plus Microsoft 365, Teams Enterprise, and Copilot-related licenses to be involved depending on the associated-license list.

The template might not appear in the normal Teams **Apps -> Add apps** catalog before an instance exists. After instance creation, search Teams for the created agent user's display name and add that user to chats, teams, and channels.

If instance submit fails with **Request failed with status 500**, check these before retrying:

```text
Users -> Active users -> <owner>
  -> Licenses: Agent 365, Microsoft 365 Copilot, Microsoft 365 E5 or equivalent, Teams Enterprise
  -> Usage location: set, for example CZ

Agents -> All agents -> <agent> -> View associated licenses
  -> every listed SKU has unassigned capacity

Tenant enrollment
  -> Microsoft Agent 365 Frontier must be enabled for the tenant
  -> Microsoft 365 admin center -> Copilot -> Settings -> View all
  -> search for Frontier -> Copilot Frontier
  -> set access to All users or a group containing the owner/admin users
  -> allow up to 3 hours for propagation
```

The Agent 365 CLI can verify most blueprint and permission setup, but it cannot automatically verify Frontier tenant enrollment.

If the agent is **Available** in Microsoft 365 admin center and Developer Portal already has **Agent Type: API Based** plus the correct notification URL, but the agent does not appear in Teams Apps:

```text
Microsoft 365 admin center -> Agents -> All agents -> <agent>
  -> confirm the activation scope includes the user who is searching in Teams
  -> confirm the agent is not blocked

Teams admin center -> Teams apps -> Manage apps
  -> search for the agent
  -> allow it if it is blocked or restricted by app policy
```

After changing activation scope, licenses, or app policy, wait 5-10 minutes and restart Teams before searching again.

## 9. Validate from Teams

After the agent instance is approved and visible in Teams, test first in 1:1 chat, then add it to a team/channel and @mention it with these smoke prompts:

| Runtime | Agent | Prompt | Expected |
| --- | --- | --- | --- |
| OpenClaw | OpenClaw Autopilot | `List services from private incidents MCP` | `core_banking`, `card_payments`, `digital_onboarding`, `fraud_detection`, `wealth_portfolio` |
| Hermes | Hermes Autopilot | `Reply with exactly: Hermes bridge OK` | `Hermes bridge OK` |

If Teams chat fails, first confirm the direct bridge smokes in step 7 still pass. Then verify Developer Portal has **Agent Type: API Based** and the runtime-specific `/api/messages` notification URL.

## Troubleshooting

Common fixes:

```text
Azure login needed: prefer az login --use-device-code.
OpenClaw pairing required: run scripts.prepare_control_ui and approve the bridge device.
Hermes /health works but /invoke fails: check Hermes logs and model provider env vars.
Hermes sandbox proxy says Failed to forward request: run `scripts.demo_ops reset-sandbox --runtime hermes`, then rerun with `--execute` if the dry-run points at the stale Hermes sandbox. If dry-run returns `ERROR: Forbidden`, run `scripts.demo_ops grant-sandbox-access --execute` or switch to an Azure identity with ACA Sandbox data-plane access.
Private MCP unavailable: verify private-incidents-mcp image includes FastMCP host-origin protection disabled on both app and run paths.
Agent 365 endpoint wrong: confirm setup_agent365 used .local\<runtime>\apps\terraform-outputs.json or pass --messaging-endpoint.
Runtime confusion: run `uv run python -m scripts.demo_ops status --runtime both --invoke`, then `scripts.demo_ops activate --runtime <runtime>` before Terraform plan/apply.
Teams delivery delay: run `scripts.demo_ops logs --runtime <runtime> --app bridge --execute`; if OPENCLAW_BRIDGE_DEBUG is enabled, also run `scripts.demo_ops status --runtime <runtime> --diag`.
Agent 365 instance not visible: confirm Developer Portal, activation scope, Teams app policy, and wait for propagation.
Agent 365 says run out of licenses: if Add Instance is usable, submit the instance form first. If submit fails, open View associated licenses on the agent template, then verify Billing -> Licenses has unassigned capacity for every listed SKU. If the instance exists but license assignment failed, go to Users -> Active users, find the agent user, and assign the missing license manually.
Agent 365 Add Instance returns 500: use `scripts.provision_agent365_instance` first. It automates the direct Graph Agent ID identity/user/license creation path and can call the beta Agent 365 registration API with app-only `AgentRegistration.ReadWrite.All`. If `--register` returns 403/404, the registration API is blocked by permission or tenant rollout; retry without `--register`, then use portal Add Instance only for the remaining registration step.
Agent 365 Add Instance returns 500 after submitting a custom UPN domain: retry with the tenant *.onmicrosoft.com domain if the custom domain does not show OfficeCommunicationsOnline in Microsoft Graph domain supportedServices.
Azure azapi token failures: refresh az login and retry, or use az containerapp update for one-off image/env updates during development.
```

Clean template recovery for repeatable Agent 365 Add Instance 500:

```powershell
Set-Location .local\hermes\agent365
a365 cleanup instance --dry-run
a365 cleanup blueprint --dry-run

# If dry-run only shows the intended Hermes resources, delete the broken blueprint/template backing objects.
a365 cleanup instance
a365 cleanup blueprint

Set-Location ..\..\..
uv run python -m scripts.setup_agent365 --runtime hermes --run-setup

# Optional diagnostic: upload the unmodified a365-generated package first.
Set-Location .local\hermes\agent365
a365 publish --aiteammate
```

After setup, upload the regenerated package, confirm Developer Portal still shows **Agent Type: API Based** and the Hermes `/api/messages` URL, then retry **Add Instance** with the `*.onmicrosoft.com` domain.

## System snapshot

After major Agent 365 or Azure changes, capture a redacted snapshot of the live system. This is diagnostic state for future diffing; it is not a secret backup and cannot recreate credentials.

```powershell
uv run python -m scripts.snapshot_system
```

The script writes timestamped JSON under:

```text
.local\snapshots\<utc-timestamp>\
```

Captured surfaces include:

| Surface | Examples |
| --- | --- |
| Local ignored state | runtime Terraform outputs, Agent 365 generated config, instance state, package cleanup output |
| Azure | account context, resource groups, Container App definitions, active revisions |
| Microsoft Graph / Entra | domains, subscribed SKUs, Agent 365 blueprint application/SP, agent identity, agent user |
| Agent 365 | agent registration by ID when `.local\agent365-registration-app.json` exists |

Values whose key names contain `secret`, `password`, `token`, `credential`, `privateKey`, or `apiKey` are replaced with `<redacted>`. To compare a later rebuild against the known-good state, capture another snapshot and diff the JSON directories:

```powershell
uv run python -m scripts.snapshot_system
git --no-pager diff --no-index .local\snapshots\<old> .local\snapshots\<new>
```

The first Hermes Teams-working snapshot in this session was written to:

```text
.local\snapshots\20260709-121747Z
```

After A6 operator validation and Hermes sandbox reset, the latest both-runtimes baseline snapshot is `.local\snapshots\20260709-170709Z`.

## Local validation

```powershell
uv run python -m unittest tests.test_agent365_setup tests.test_setup_app_tfvars tests.test_deploy_apps_runtime tests.test_demo_ops tests.test_provision_agent365_instance tests.test_snapshot_system tests.test_a7_setup tests.test_agent_identity_tokens tests.test_identity_mcp_proxy tests.test_hermes_runtime tests.test_runtime_adapters tests.test_teams_bridge
uv run python -m compileall autopilots_identity bridge scripts tests runtimes\openclaw\openclaw_gateway runtimes\hermes -q
Set-Location .\private-incidents-mcp
uv run --with pytest --with pytest-asyncio --with-editable . pytest -q
Set-Location ..
Set-Location .\public-shipments-mcp
uv run --with pytest --with-editable . pytest -q
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
