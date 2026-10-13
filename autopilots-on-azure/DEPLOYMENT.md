# Deployment and operations

Most commands below update **existing Workers**, preserving their Agent 365 blueprint, Agent Identity, Agent User, grants, and Data Disk. Create new Workers only with [Fresh Worker bootstrap](#fresh-worker-bootstrap); do not substitute placeholder endpoints or copied identities. Other current limitations are in [SPEC.md](SPEC.md).

## Prerequisites

- Azure CLI, Terraform, `uv`, and Agent 365 CLI (`a365`).
- Operator login with permissions for the affected Azure resources; it runs Terraform and Sandbox deployment.
- A separate tenant-administrator login that is a **direct owner** of the Worker's blueprint; it runs Graph federation, consent, endpoint, and publishing steps. Operator rights are not sufficient for blueprint federation.
- Agent User licensing rights where needed.
- Existing `.local\<worker>\apps\` and `.local\<worker>\agent365\` state.
- GitHub CLI for Promotion; `gh aw` only when operating its review workflows.

From the repository root:

```powershell
az login --use-device-code
Set-Location .\autopilots-on-azure
uv sync --frozen
$worker = "hermes2"
$workspace = "autopilot-$worker"
$tenant = az account show --query tenantId -o tsv
$admin = Join-Path (Get-Location) ".local\admin-login"
function Invoke-AsAdmin([scriptblock]$Command) {
    $operatorConfig = $env:AZURE_CONFIG_DIR
    $env:AZURE_CONFIG_DIR = $admin
    try { & $Command } finally { $env:AZURE_CONFIG_DIR = $operatorConfig }
}
Invoke-AsAdmin { az login --use-device-code --tenant $tenant }
```

Both logins must complete in the same tenant; `setup_identity` rejects a tenant that differs from the captured `tenant_id`. Resolve reported login problems or restart an expired device-code attempt before continuing. When finished, run `Invoke-AsAdmin { az logout }` and delete `.local\admin-login`.

`deploy_apps_runtime` selects or creates `--workspace` (default `autopilot-<worker>`) and activates that Worker's tfvars on every run. `--apply` without `--auto-approve` waits for Terraform confirmation and fails with EOF in a non-interactive shell; omit `--auto-approve` below to review the plan interactively.

`pyproject.toml` pins the approved corporate Python feed. Keep frozen Python/npm locks; do not embed feed credentials or change versions to bypass a resolution failure.

## State

| Path | Purpose |
| --- | --- |
| `.local\<worker>\apps\generated.app.auto.tfvars.json` | Worker-scoped deployment settings |
| `.local\<worker>\apps\terraform-outputs.json` | Captured endpoints, Group identities, and Sandbox/image IDs |
| `.local\<worker>\apps\collective-learning-approval.json` | Approval identity |
| `.local\<worker>\agent365\` | Agent 365 configuration and identity discovery |
| `terraform\apps\generated.*.auto.tfvars.json` | Active selected-workspace inputs |

These files and Terraform state may contain sensitive material; never commit them. Preserve existing Worker names, identity state, release pins, volume names, and scheduling settings. `hermes2` enables user scheduling/Service Bus Dreaming; `hermes` disables both.

## Update sequence

### 1. Shared platform

Apply only when shared infrastructure changes:

```powershell
terraform -chdir=terraform\platform init
terraform -chdir=terraform\platform apply
```

The platform (default Terraform workspace) supplies Foundry, ACR, networking, private DNS/Express ingress, the Service Bus namespace, and monitoring. Each Worker's apps workspace owns its Sandbox Groups, identities, and Service Bus queue. Verify the private-ingress environment before linking a Group: the link is irreversible.

### 2. Apply infrastructure without services

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace `
  --apply --auto-approve --infrastructure-only --capture
```

This reconciles ARM resources and captures Group/identity metadata without deploying Sandbox services or changing the gateway endpoint.

### 3. Reconcile federation and consent

```powershell
Invoke-AsAdmin { uv run python -m scripts.setup_identity --state-name $worker }
Push-Location ".local\$worker\agent365"
try { Invoke-AsAdmin { a365 query-entra inheritance } } finally { Pop-Location }
```

`setup_identity` writes the blueprint federated credentials for the gateway and runtime identities (removing the obsolete `sandbox` credential), requests missing permissions without recreating identities, and writes and activates the Worker's identity tfvars. Every resource in the inheritance query must have effective inheritance. If Graph CAE requests reauthentication, repeat the admin device-code login before retrying.

Reconcile every affected Worker after a `ToolingManifest.json` change before deploying or publishing. See the [identity runbook](docs/runbooks/identity-mcp.md).

### 4. Build images

```powershell
uv run python -m scripts.build_images --state-name hermes --state-name hermes2
```

The selected states must already exist. This builds the Hermes runtime, gateway, private MCP, and public MCP images in ACR and writes their digest pins and matching tagged disk-conversion sources into each `.local\<worker>\apps\generated.app.auto.tfvars.json`; step 5 activates them.

### 5. Apply and deploy services

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace `
  --apply --auto-approve --capture
```

`--apply` implies `--deploy-services` and `--capture` unless `--infrastructure-only` is set. The apply propagates reconciled identity values into Terraform's `deployment_config`; `--deploy-services` without `--apply` reuses that output and is valid only when it is current.

Each Sandbox Group converts images with its own managed identity (`AcrPull`, [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md)). A new role assignment can take minutes to propagate; a failed conversion stops the deployment. All four disk images are prepared before any service is created, and unchanged images are reused. The capture records native endpoints, `sandbox_services`, and disk image IDs.

A changed image creates new gateway and MCP Sandboxes with new URLs; an environment-only change updates them in place. The gateway never auto-suspends (post-ACK work, Service Bus receive). MCPs and the gateway-created runtime suspend to Disk after 1800 s idle; the runtime keeps its persistent Data Disk.

### 6. Update the Agent 365 endpoint as its direct owner

Required whenever step 5 replaced the gateway Sandbox (any gateway image change): its native URL changes, and Teams and email silently go to the old URL until this step runs. Check whether the registered endpoint is current:

```powershell
$bridge = (Get-Content ".local\$worker\apps\terraform-outputs.json" | ConvertFrom-Json).bridge_url
$registered = (Get-Content ".local\$worker\agent365\$worker-agent365-identifiers.json" | ConvertFrom-Json).messagingEndpoint
"$bridge/api/messages" -eq $registered
```

`a365` endpoint update is destructive **DELETE → POST**. The wrapper first requires the signed-in Graph user to be a direct blueprint owner; unreadable ownership or a nonowner fails before local mutation or `a365` invocation. Administrative roles alone do not satisfy that check. Terraform stays on the operator login:

```powershell
Invoke-AsAdmin {
    uv run python -m scripts.setup_agent365 `
      --state-name $worker --update-endpoint --capture
}
```

This registers `<bridge_url>/api/messages` and refreshes the identifiers file. Do not provision replacement identities for an existing-Worker update. If deletion succeeded but registration failed, repair the endpoint as the direct owner before resuming Teams validation.

### 7. Publish the Agent User package

```powershell
$agentName = (Get-Content ".local\$worker\agent365\a365.config.json" | ConvertFrom-Json).agentName
Invoke-AsAdmin {
    uv run python -m scripts.setup_agent365 `
      --state-name $worker --agent-name $agentName --publish --capture
}
```

`--agent-name` overwrites the configured agent and blueprint display names (default `Hermes Autopilot`), so pass the existing name. Publishing rejects missing `ToolingManifest.json` consents and bumps the manifest version. Upload `.local\<worker>\agent365\manifest\manifest.zip` through **Microsoft 365 admin center → Agents → All agents → Upload custom agent**, not the Teams app store.

Assign each Agent User only licenses required by its scenario: Teams/Agent 365 for presence and management; applicable Microsoft 365 services for mailbox/files/calendar; Copilot for relevant Work IQ capabilities. Resource provisioning and package/permission propagation may take time.

## Fresh Worker bootstrap

A new Worker is created blueprint-first, deployed, and only then given its real messaging endpoint. With the operator and `$admin` logins from [Prerequisites](#prerequisites):

```powershell
uv run python -m scripts.worker_lifecycle bootstrap `
  --state-name worker3 --pins-from hermes2 --admin-config-dir $admin
```

The operator login runs Terraform and Sandbox deployment; `--admin-config-dir` runs Agent 365, Graph, and consent. The command:

1. writes `a365.config.json` without an endpoint and runs `a365 setup all --aiteammate --m365` (admin consent opens in a browser);
2. requires complete inheritable permissions and deletes every CLI-created blueprint client secret;
3. provisions the Agent Identity and Agent User (default licenses: Agent 365, Teams, Microsoft 365 E3, Power Automate Free);
4. writes Worker settings with release/image pins copied from `--pins-from`, scheduling and learning disabled;
5. applies infrastructure, reconciles federation and grants, deploys services, and invokes the Worker;
6. registers the deployed bridge endpoint once, rechecks that the blueprint has zero client secrets, and publishes the package.

Rerunning resumes: an existing live blueprint, Worker settings, and an already registered identical endpoint are kept; a different registered endpoint stops the run. Finally upload `.local\<worker>\agent365\manifest\manifest.zip` in Microsoft 365 admin center as in step 7.

`deploy_apps_runtime` leaves the new Worker's tfvars active. Other Workers' `deploy_apps_runtime` runs reactivate their own; before raw `terraform` commands, run `demo_ops activate --state-name <worker>` and select the workspace it prints.

## Validation

### Local checks

```powershell
uv run python -m unittest discover -s tests
```

### Deployed checks

```powershell
uv run python -m scripts.demo_ops status --state-name $worker --invoke
uv run python -m scripts.demo_ops smoke --state-name $worker --timeout 600
uv run python -m scripts.demo_ops smoke --state-name $worker `
  --message "Use private incidents to list services, then public shipments to list demo shipments." `
  --timeout 600
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --plan
```

`status` requires a Running gateway with auto-suspend disabled and a healthy `/health`; `--invoke` adds the default smoke. The default smoke checks its reply marker; a custom `--message` is not checked automatically. Require a real model response, actual tool results, correct Worker/release health metadata, and a converged Terraform plan. Unauthenticated public MCP/operator requests must fail authentication; private MCP must not become publicly reachable. Health alone does not check tool authorization.

Feature-specific live checks:

```powershell
uv run python -m scripts.document_smoke --state-name $worker --timeout 1200
uv run python -m scripts.m365_actions_smoke `
  --state-name $worker --recipient user@contoso.com
uv run python -m scripts.user_schedule_smoke `
  --state-name hermes2 --due-seconds 180 --timeout 1200
uv run python -m scripts.servicebus_dream_smoke `
  --state-name hermes2 --timeout 1800
```

- Document smoke creates a real Agent User DOCX, reads a marker, adds/replies to a comment, and checks results. Remove the document afterward through its Microsoft 365 storage surface.
- M365 actions smoke is a dry run until `--execute`; execution sends and reads back a real Agent User Teams message.
- Schedule smoke checks native schedule/queue state, durable execution/delivery receipts, output hash, and DLQ, then removes the test job.
- Dream smoke runs an ad-hoc occurrence without advancing production cron. A prepared no-change result may contain `packet=null`.
- For resume, stop an otherwise idle runtime, invoke through the gateway, and verify the same Sandbox ID and an existing Data Disk marker. Deleting/recreating compute is a different operation.

Use [DEMO.md](DEMO.md) for Teams attachments, comments, cards, app authorization, and learning evaluation.

## Scheduling and release changes

To configure Service Bus Dreaming on an existing Worker:

```powershell
uv run python -m scripts.setup_app_tfvars `
  --state-name $worker --autopilot-name $worker `
  --no-scheduled-learning-enabled `
  --user-scheduling-enabled --servicebus-dream-enabled `
  --servicebus-dream-cron-expression "0 2 * * *" --runtime-only
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --apply --auto-approve --capture
```

`setup_app_tfvars` preserves unspecified settings; `--apply` redeploys services from the updated settings. The bridge-owned interval timer and Service Bus Dreaming are mutually exclusive, and Service Bus Dreaming requires user scheduling. Manual Dreaming uses `scripts.demo_ops dream`; the interval timer has separate `scheduled-run`/`scheduled-status` commands.

Before a Role Release change, approve/export governed changes or sign their explicit rejection as shown in [DEMO.md](DEMO.md). Then set the reviewed newer release and its full commit:

```powershell
$release = Read-Host "Newer reviewed semantic Role Release"
$commit = Read-Host "Full immutable release commit"
uv run python -m scripts.setup_app_tfvars `
  --state-name $worker --autopilot-name $worker `
  --role-release $release --role-release-commit $commit --runtime-only
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --apply --auto-approve --capture
uv run python -m scripts.demo_ops status --state-name $worker --invoke
```

Refresh preflight validates approval/rejection against exact state before replacing compute. Confirm the new Role Skills, preserved private state, and retired previous-release candidates in a fresh session.

## Diagnostics and recovery

```powershell
uv run python -m scripts.demo_ops logs `
  --state-name $worker --app gateway --tail 120 --execute
uv run python -m scripts.demo_ops reset-sandbox --state-name $worker
```

`logs` prints the target unless `--execute` is given; `--app` is `gateway` (alias `bridge`), `runtime` (requires `--path`), `private-mcp`, or `public-mcp`, and the Sandbox must be running. `reset-sandbox` previews deletion of runtime compute while retaining its Data Disk; add `--execute` only for intentional replacement. Do not reset healthy suspended or `Stopped` compute to perform a resume test.

For trace-interrupted-session `409` and ambiguous `dream_started` receipts, follow [SPEC.md](SPEC.md#observability) and [Scheduling and Dreaming](SPEC.md#scheduling-and-dreaming): restart the runtime wrapper and native gateway, never delete a lease, and inspect before replaying.

Foundry external registration can be inspected with captured deployment values:

```powershell
$outputs = Get-Content ".local\$worker\apps\terraform-outputs.json" | ConvertFrom-Json
uv run python -m scripts.register_foundry_external_agent `
  --project-endpoint $outputs.foundry_project_endpoint `
  --agent-name $outputs.foundry_agent_name --auth azure-cli
```

Both Workers report as `autopilots-hermes`; the OTel agent ID defaults to the agent name. Add `--apply` only to reconcile registration. After a real explicit-session turn, inspect Foundry traces for model/tool callbacks, timings/usage, opaque identifiers, and absent payload content.

## Cleanup

Remove a whole Worker with one command. It previews by default:

```powershell
uv run python -m scripts.worker_lifecycle teardown --state-name worker3
uv run python -m scripts.worker_lifecycle teardown --state-name worker3 `
  --execute --admin-config-dir $admin --restore-state hermes2
```

Review the printed inventory before `--execute`. It deletes the Worker's Sandboxes, volumes, and disk images (only in Sandbox Groups named for the Worker), destroys and deletes Terraform workspace `autopilot-<worker>`, deletes its Agent User, Agent Identity, and blueprint with the admin login, reactivates `--restore-state` (default `hermes2`), and removes `.local\<worker>\`. Shared platform resources are untouched.

`scripts.demo_cohort reset` is reserved for already provisioned disposable `demo-*` Workers/workspaces/volumes; preview first and supply their reviewed baseline release/commit. Never reset long-lived Workers backward or rewrite shared Role Release history to replay a classroom exercise.
