# Deployment guide

This guide covers repeatable Azure deployment and Worker lifecycle operations. Demonstration prompts belong in [DEMO.md](DEMO.md).

## Prerequisites

- Azure CLI authenticated to the intended subscription and tenant.
- Terraform.
- `uv`.
- Agent 365 CLI (`a365`).
- GitHub CLI for Promotion pull requests.
- Tenant rights for Agent 365 blueprint creation, consent, Agent Identity, Agent User, licensing, and registration.
- Available Microsoft 365 service licenses for each Agent User capability.

```powershell
az login --use-device-code
Set-Location .\autopilots-on-azure
uv sync
terraform -version
```

Use an isolated browser profile for tenant consent. Confirm the account and tenant before accepting.

## State layout

Generated state is local and must not be committed:

```text
.local\<worker>\apps\generated.app.auto.tfvars.json
.local\<worker>\apps\terraform-outputs.json
.local\<worker>\apps\collective-learning-approval.json
.local\<worker>\agent365\
terraform\apps\generated.app.auto.tfvars.json
terraform\apps\generated.runtime.auto.tfvars.json
```

Each deployed Worker uses:

- a unique `--state-name`;
- a unique `--autopilot-name` / Worker ID;
- a unique Terraform workspace;
- a unique Data Disk volume;
- a unique Agent 365 platform blueprint and bridge;
- a unique Agent Identity and Agent User.

Workers can share the same Role Blueprint, Role Release, images, Foundry deployment, Sandbox Group, networking, ACR, and MCP resource applications.

## 1. Deploy the shared platform

The platform layer creates networking, ACR, Container Apps environments, the Sandbox Group, and Foundry.

```powershell
Set-Location .\terraform\platform
terraform init
terraform apply
Set-Location ..\..
```

The current topology uses:

- Sweden Central for Foundry and ACA Sandboxes;
- North Europe for Container Apps and ACR;
- global VNet peering and shared private DNS.

## 2. Build images

Build one runtime plus the shared bridge and MCP images:

```powershell
uv run python -m scripts.build_images --runtime hermes
uv run python -m scripts.build_images --runtime openclaw
```

The command writes image digests into the active generated Terraform values. Rebuild after runtime or bridge code changes.

## 3. Configure a Hermes Worker

Choose:

```powershell
$worker = "hermes"
$workspace = "autopilot-hermes"
$volume = "hermes-data"
$roleRelease = "3.1.0"
$roleCommit = "<full immutable Role Release commit>"
```

For another Worker:

```powershell
$worker = "hermes2"
$workspace = "autopilot-hermes2"
$volume = "hermes2-data"
```

Create isolated Worker values:

```powershell
uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --state-name $worker `
  --autopilot-name $worker `
  --data-volume-name $volume `
  --role-blueprint junior-project-manager `
  --role-blueprint-source https://github.com/tkubica12/agent-demos.git `
  --role-blueprint-path autopilots-on-azure/blueprints/junior-project-manager `
  --role-release $roleRelease `
  --role-release-commit $roleCommit `
  --assignment-scope "<person-or-team>" `
  --runtime-only
```

For a new named Worker state, pass the tested image digests explicitly or copy them from an existing Worker's ignored tfvars:

```powershell
$images = Get-Content .local\hermes\apps\generated.app.auto.tfvars.json -Raw | ConvertFrom-Json

uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --state-name $worker `
  --autopilot-name $worker `
  --data-volume-name $volume `
  --role-blueprint junior-project-manager `
  --role-blueprint-source https://github.com/tkubica12/agent-demos.git `
  --role-blueprint-path autopilots-on-azure/blueprints/junior-project-manager `
  --role-release $roleRelease `
  --role-release-commit $roleCommit `
  --assignment-scope "<person-or-team>" `
  --runtime-image $images.runtime_image `
  --runtime-disk-image-name $images.runtime_disk_image_name `
  --bridge-image $images.bridge_image `
  --private-mcp-image $images.private_mcp_image `
  --public-shipments-mcp-image $images.public_shipments_mcp_image `
  --runtime-only
```

Deploy the isolated application workspace:

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name $worker `
  --workspace $workspace `
  --apply `
  --auto-approve `
  --capture
```

## 4. Create the Agent 365 platform blueprint

Prepare the Worker-specific Agent 365 workspace and endpoint:

```powershell
$outputs = ".local\$worker\apps\terraform-outputs.json"
$agentUpn = "$worker@<tenant>.onmicrosoft.com"

uv run python -m scripts.setup_agent365 `
  --runtime hermes `
  --autopilot-name $worker `
  --agent-name $worker `
  --runtime-outputs-file $outputs `
  --agent-user-principal-name $agentUpn `
  --manager-email "<manager-upn>" `
  --run-setup `
  --skip-requirements `
  --capture
```

Agent 365 setup can require browser consent and confirmation prompts. Verify:

```powershell
Set-Location ".local\$worker\agent365"
a365 query-entra inheritance
Set-Location ..\..\..
```

Every listed resource must report effective inheritance. Warnings about an empty permission type are acceptable when the other type is granted and effective inheritance is `OK`.

The Agent 365 platform blueprint is not the Git Role Blueprint. See [ADR 0014](docs/adr/0014-per-worker-agent365-blueprints-and-bridges.md).

## 5. Provision Agent Identity, Agent User, and registration

Provision one Agent User per Worker:

```powershell
$generated = Get-Content ".local\$worker\agent365\a365.generated.config.json" -Raw | ConvertFrom-Json

uv run python -m scripts.provision_agent365_instance provision `
  --runtime hermes `
  --owner-upn "<owner-upn>" `
  --agent-upn $agentUpn `
  --display-name $worker `
  --mail-nickname $worker `
  --identity-display-name "$worker Identity" `
  --agent-blueprint-id $generated.agentBlueprintId `
  --usage-location "<ISO-country-code>" `
  --license-skus "AGENT_365,Microsoft_Teams_Enterprise_New" `
  --state-file ".local\$worker\agent365\instance.$worker.json" `
  --register
```

Agent Users require individual licenses for the Microsoft 365 services they use:

| Capability | Typical required service license |
| --- | --- |
| Teams chat and channel membership | Teams Enterprise |
| Agent 365 management | Agent 365 |
| Mailbox, calendar, SharePoint, OneDrive | Appropriate Microsoft 365 suite |
| Embedded Copilot / Work IQ scenarios | Microsoft 365 Copilot and applicable suite |

Do not assign E5 or Copilot merely for runtime learning or direct bridge tests.

Resource provisioning after license assignment commonly takes 10-15 minutes and can take longer.

## 6. Configure federation and MCP authorization

Create or reuse the shared MCP resource applications, add blueprint federation, assign Agent Identity app roles, and write Worker identity values:

```powershell
uv run python -m scripts.setup_identity `
  --runtime hermes `
  --state-name $worker `
  --mail-nickname $worker `
  --state-file ".local\$worker\agent365\instance.$worker.json"
```

Use `--skip-workiq-permissions` when the Worker has no mailbox/Copilot scenario.

Detailed identity troubleshooting is in [the identity and MCP runbook](docs/runbooks/identity-mcp.md).

## 7. Inject Agent 365 SDK credentials and redeploy

```powershell
uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --state-name $worker `
  --autopilot-name $worker `
  --data-volume-name $volume `
  --role-blueprint junior-project-manager `
  --role-blueprint-source https://github.com/tkubica12/agent-demos.git `
  --role-blueprint-path autopilots-on-azure/blueprints/junior-project-manager `
  --role-release $roleRelease `
  --role-release-commit $roleCommit `
  --assignment-scope "<person-or-team>" `
  --agent365-from-generated `
  --runtime-only

uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name $worker `
  --workspace $workspace `
  --apply `
  --auto-approve `
  --capture
```

## 8. Validate the Worker

Direct bridge smoke:

```powershell
uv run python -m scripts.demo_ops smoke `
  --runtime hermes `
  --state-name $worker `
  --message "Reply exactly: $worker ready" `
  --timeout 600
```

Health and Role Release:

```powershell
uv run python -m scripts.demo_ops status `
  --runtime hermes `
  --state-name $worker `
  --invoke
```

Terraform convergence:

```powershell
Set-Location .\terraform\apps
terraform workspace select $workspace
terraform plan
Set-Location ..\..
```

Expected: no changes.

## 9. Teams availability

After license and registration propagation:

1. Search Teams for the Agent User display name.
2. Start a 1:1 chat.
3. Add the Agent User as a Team member when channel demonstrations are needed.
4. Use an explicit `@mention` in channels.

Agent Users do not receive every unmentioned channel message.

## OpenClaw deployment differences

OpenClaw uses the same platform and Agent 365 pattern but additionally requires:

- OpenClaw Gateway token;
- bridge device identity and approval;
- OpenClaw-specific Data Disk;
- Control UI approval after initial Sandbox creation.

Prepare and deploy with:

```powershell
uv run python -m scripts.setup_app_tfvars --runtime openclaw
uv run python -m scripts.deploy_apps_runtime `
  --runtime openclaw `
  --workspace autopilot-openclaw `
  --apply `
  --auto-approve `
  --capture
```

If pairing is required:

```powershell
uv run python -m scripts.prepare_control_ui
```

## Worker Refresh

Before replacing a Role Release:

1. Prepare and approve a Learning Packet when governed changes exist.
2. Publish the newer immutable Role Release.
3. Update Worker tfvars with the new release and commit.
4. Apply the Worker's Terraform workspace.
5. Invoke the bridge.

Refresh preflight validates the approved packet before deleting the old Sandbox. Personal Memory, Private Playbooks, and Work History remain on the Data Disk. Role Skills are replaced and previous Candidate Improvements are archived.

## Runtime image updates

Rebuild images, update the Worker's scoped tfvars, and apply its workspace. The runtime-image label forces controlled Sandbox replacement even when the Role Release is unchanged.

## Diagnostics

```powershell
uv run python -m scripts.demo_ops logs `
  --runtime hermes `
  --state-name $worker `
  --app bridge `
  --tail 120 `
  --execute
```

Reset one Sandbox while preserving its volume:

```powershell
uv run python -m scripts.demo_ops reset-sandbox `
  --runtime hermes `
  --state-name $worker

uv run python -m scripts.demo_ops reset-sandbox `
  --runtime hermes `
  --state-name $worker `
  --execute
```

## Cleanup

Delete a scripted Agent 365 Worker only with explicit intent:

```powershell
uv run python -m scripts.provision_agent365_instance cleanup `
  --runtime hermes `
  --mail-nickname $worker `
  --state-file ".local\$worker\agent365\instance.$worker.json" `
  --instance `
  --remove-state `
  --purge-deleted
```

Destroy its Terraform workspace separately:

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name $worker `
  --workspace $workspace

Set-Location .\terraform\apps
terraform workspace select $workspace
terraform destroy
Set-Location ..\..
```

Do not delete shared platform resources or Data Disks unless their retained state is intentionally discarded.
