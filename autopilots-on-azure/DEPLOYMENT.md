# Deployment and operations

These commands update **existing Workers**, preserving their Agent 365 blueprint, Agent Identity, Agent User, grants, and Data Disk. Fresh-Worker bootstrap has an unresolved identity/endpoint dependency; do not substitute placeholder endpoints or copied identities. Other current limitations are in [SPEC.md](SPEC.md).

## Prerequisites

- Azure CLI, Terraform, `uv`, and Agent 365 CLI (`a365`).
- Correct subscription/tenant and permissions for the affected Azure resources.
- An existing direct blueprint owner for endpoint updates; consent/Agent User licensing rights where needed.
- Existing `.local\<worker>\apps\` and `.local\<worker>\agent365\` state.
- GitHub CLI for Promotion; `gh aw` only when operating its review workflows.

From the repository root:

```powershell
az login --use-device-code
Set-Location .\autopilots-on-azure
uv sync --frozen --index-url https://packagefeedproxy.microsoft.io/pypi/simple
terraform -version
$worker = "hermes2"
$workspace = "autopilot-hermes2"
```

For `hermes`, use workspace `autopilot-hermes`. Keep workspace selection explicit. Login must complete in the intended tenant; resolve reported login problems or restart an expired device-code attempt before continuing.

The corporate Python feed is required in this environment. Keep frozen Python/npm locks and use an approved build-reachable feed; do not embed feed credentials or change versions to bypass a resolution failure.

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

The platform supplies Foundry, ACR, networking, private DNS/Express ingress, Service Bus namespace, and monitoring. Per-Worker application state owns its service Groups, identities, and queue. Verify the private-ingress environment before linking a Group: the link is irreversible.

### 2. Build images

```powershell
uv run python -m scripts.build_images --state-name hermes --state-name hermes2
```

The selected states must already exist. The command builds Hermes, gateway, private MCP, and public MCP and writes digest pins plus matching tagged OCI conversion sources.

Sandbox conversion uses a deployer-held short-lived ACR token, as defined in [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md). The helper prepares all four ready disk images before creating services. ACR admin is disabled; runtime/gateway receive prepared IDs, not registry credentials or roles. Reuse matching prepared images without another registry login.

### 3. Apply infrastructure without services

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace `
  --apply --infrastructure-only --capture
```

This creates/reconciles ARM resources and captures Group/identity metadata; it does not invent a gateway endpoint.

### 4. Reconcile federation and consent

```powershell
uv run python -m scripts.setup_identity --state-name $worker
Push-Location ".local\$worker\agent365"
try { a365 query-entra inheritance } finally { Pop-Location }
```

Identity reconciliation updates runtime/gateway federation and writes current identity settings into Worker tfvars. Every resource in the inheritance query must have effective inheritance. If Graph CAE requests reauthentication, complete a new device-code login before retrying.

For a changed `ToolingManifest.json`, reconcile every affected Worker before deployment/publishing. The helper requests missing permissions rather than recreating identities. See the [identity runbook](docs/runbooks/identity-mcp.md).

### 5. Apply reconciled settings and deploy

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace `
  --apply --deploy-services --capture
```

This apply propagates reconciled identity values into Terraform's `deployment_config`, which the service helper consumes. A bare `--deploy-services` is valid only when that output is already current.

Capture actual native endpoints, `sandbox_services`, and prepared image IDs. Gateway/MCP ingress uses native HTTPS transport with application authentication. Gateway auto-suspend remains disabled for post-ACK work and Service Bus receive; runtime is OnDemand with a persistent Data Disk.

### 6. Update the Agent 365 endpoint as its direct owner

`a365` endpoint update is destructive **DELETE → POST**. The wrapper first resolves the signed-in Graph user and all direct blueprint owners; unreadable ownership or a nonowner fails before local mutation or `a365` invocation. Administrative privileges alone do not satisfy that check.

Use a separate Azure CLI cache for the explicitly authorized owner. Keep Terraform on the original operator identity:

```powershell
$tenant = az account show --query tenantId -o tsv
$operatorConfig = $env:AZURE_CONFIG_DIR
$ownerConfig = Join-Path (Get-Location) (".local\owner-login-" + [guid]::NewGuid())
New-Item -ItemType Directory $ownerConfig | Out-Null
try {
    $env:AZURE_CONFIG_DIR = $ownerConfig
    az login --use-device-code --tenant $tenant
    if ($LASTEXITCODE -ne 0) { throw "Owner login did not complete." }
    uv run python -m scripts.setup_agent365 `
      --state-name $worker --update-endpoint --capture
    if ($LASTEXITCODE -ne 0) { throw "Endpoint update failed; inspect before retrying." }
}
finally {
    az logout
    $env:AZURE_CONFIG_DIR = $operatorConfig
    Remove-Item -LiteralPath $ownerConfig -Recurse -Force
}
```

Do not use `--run-setup` or provision replacement identities for an existing-Worker update. If deletion succeeded but replacement failed, repair the registered endpoint with the direct owner before resuming Teams validation.

### 7. Publish the Agent User package

```powershell
uv run python -m scripts.setup_agent365 `
  --state-name $worker --agent-name $worker --publish
```

Publishing rejects missing manifest consents. Upload `.local\<worker>\agent365\manifest\manifest.zip` through **Microsoft 365 admin center → Agents → All agents → Upload custom agent**, not the Teams app store.

Assign each Agent User only licenses required by its scenario: Teams/Agent 365 for presence and management; applicable Microsoft 365 services for mailbox/files/calendar; Copilot for relevant Work IQ capabilities. Resource provisioning and package/permission propagation may take time.

## Validation

### Local checks

```powershell
uv run python -m unittest discover -s tests
```

### Deployed checks

```powershell
uv run python -m scripts.demo_ops status --state-name $worker --invoke
uv run python -m scripts.demo_ops smoke --state-name $worker `
  --message "Reply exactly: Hermes ready" --timeout 600
uv run python -m scripts.demo_ops smoke --state-name $worker `
  --message "Use private incidents to list services, then public shipments to list demo shipments." `
  --timeout 600
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --plan
```

Require a real model response, actual tool results, correct Worker/release health metadata, and a converged Terraform plan. Unauthenticated public MCP/operator requests must fail authentication; private MCP must not become publicly reachable. Health alone does not check tool authorization.

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
  --state-name $worker --workspace $workspace --apply --capture
```

The bridge-owned interval timer and Service Bus Dreaming are mutually exclusive. Manual Dreaming uses `scripts.demo_ops dream`; the configured interval timer has separate `scheduled-run`/`scheduled-status` commands.

Before a Role Release change, approve/export governed changes or sign their explicit rejection as shown in [DEMO.md](DEMO.md). Then set the reviewed newer release and its full commit:

```powershell
$release = Read-Host "Newer reviewed semantic Role Release"
$commit = Read-Host "Full immutable release commit"
uv run python -m scripts.setup_app_tfvars `
  --state-name $worker --autopilot-name $worker `
  --role-release $release --role-release-commit $commit --runtime-only
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --apply --capture
uv run python -m scripts.demo_ops status --state-name $worker --invoke
```

Refresh preflight validates approval/rejection against exact state before replacing compute. Confirm the new Role Skills, preserved private state, and retired previous-release candidates in a fresh session.

## Diagnostics and recovery

```powershell
uv run python -m scripts.demo_ops logs `
  --state-name $worker --app gateway --tail 120 --execute
uv run python -m scripts.demo_ops reset-sandbox --state-name $worker
```

`reset-sandbox` previews deletion of runtime compute while retaining its Data Disk; add `--execute` only for intentional replacement. Do not reset healthy `Stopped` compute to perform a resume test.

Trace interrupted-session `409` requires stopping the uncertain native executor: restart **runtime wrapper and native gateway**, not only the public gateway. Do not delete a lease to permit overlapping work. Ambiguous Dream `dream_started` also requires inspection rather than blind replay.

Foundry external registration can be inspected with captured deployment values:

```powershell
$projectEndpoint = Read-Host "Foundry project endpoint"
uv run python -m scripts.register_foundry_external_agent `
  --project-endpoint $projectEndpoint `
  --agent-name "autopilots-$worker" --otel-agent-id "autopilots-$worker" `
  --auth azure-cli
```

Add `--apply` only to reconcile registration. After a real explicit-session turn, inspect Foundry traces for model/tool callbacks, timings/usage, opaque identifiers, and absent payload content. Registration is metadata; tracing and runtime operation are separate.

## Cleanup

For a deliberately deleted Worker, remove its Agent 365 instance separately from Azure infrastructure:

```powershell
uv run python -m scripts.provision_agent365_instance cleanup `
  --state-name $worker `
  --mail-nickname $worker `
  --state-file ".local\$worker\agent365\instance.$worker.json" `
  --remove-state

uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace
terraform -chdir=terraform\apps workspace select $workspace
terraform -chdir=terraform\apps destroy
```

Review the exact targets first; place `--dry-run` before `cleanup` to preview Graph writes. The helper deletes only the recorded Agent User and Agent Identity, not the blueprint or package. Retained Data Disks and shared platform resources need separate intentional cleanup. Generated apps use owner-bound delete actions or native post-suspension deletion.

`scripts.demo_cohort reset` is reserved for already provisioned disposable `demo-*` Workers/workspaces/volumes; preview first and supply their reviewed baseline release/commit. Never reset long-lived Workers backward or rewrite shared Role Release history to replay a classroom exercise.
