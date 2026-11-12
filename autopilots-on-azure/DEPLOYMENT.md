# Deployment and operations

Most commands below update **existing Workers**, preserving their Agent 365 blueprint, Agent Identity, Agent User, grants, and Worker home with its backups. Create new Workers only with [Fresh Worker bootstrap](#fresh-worker-bootstrap); do not substitute placeholder endpoints or copied identities. Other current limitations are in [SPEC.md](SPEC.md).

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

These files and Terraform state may contain sensitive material; never commit them. Preserve existing Worker names, identity state, release pins, and scheduling settings. Both `hermes` and `hermes2` enable user scheduling and Service Bus Dreaming.

Each Worker's `deployer_principal_id` pins the original deployment principal, independently of the current Azure CLI login. Fresh `setup_app_tfvars` runs record the initial signed-in user's object ID, or accept `--deployer-principal-id`. For an existing state that has no pin, read the original `principal_id` from that Worker's `azurerm_role_assignment.deployer_sandbox_data_owner` Terraform state and supply it explicitly. Never accept role-assignment replacements just because a different operator runs an image update.

## Update sequence

### 1. Shared platform

Apply only when shared infrastructure changes:

```powershell
terraform -chdir=terraform\platform init
terraform -chdir=terraform\platform apply
```

The platform supplies Foundry, the runtime dependency registry, networking, private DNS/Express ingress, Service Bus, and monitoring. Each apps workspace owns five groups/identities, one native receiver for its scheduling queue, and permanent roles. Verify private ingress before linking a Group: the link is irreversible. Deployment refuses a plan that deletes or replaces Worker identities or Sandbox Groups.

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

`setup_identity` writes the blueprint federated credentials for the gateway and runtime identities (removing the obsolete `sandbox` credential), requests missing permissions without recreating identities, and writes and activates the Worker's identity tfvars. Inspect inheritance together with direct Agent Identity roles and fixed Agent User delegated grants: an inheritance-only summary does not attest or disprove those direct grants. Require real tool execution under the intended principal. If Graph CAE requests reauthentication, repeat the admin device-code login before retrying.

Reconcile every affected Worker after a `ToolingManifest.json` change before deploying or publishing. See the [identity runbook](docs/runbooks/identity-mcp.md).

### 4. Build the runtime dependency base only when needed

```powershell
uv run python -m scripts.build_images --state-name hermes --state-name hermes2
```

The selected states must exist. This builds only the heavy Hermes dependency image in ACR and writes its digest and conversion tag to those states. The registry also retains the pinned official upstream image as a server-side build cache; neither repository is a separate thin-service application image. Run the build for a changed Hermes pin, runtime lock, compiled Office helper, or installed dependency—not for ordinary Python source changes.

Gateway and MCP services use a pinned public Python image. Their source archives contain exact file hashes and frozen requirements. Deployment prepares approved-feed wheels once, installs them offline into a lock/base-addressed environment, and reuses that environment on later source changes. The runtime verifies that its source release's dependency lock matches the selected image.

### 5. Apply and deploy services

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace `
  --apply --auto-approve --capture
```

`--apply` implies `--deploy-services` and `--capture` unless `--infrastructure-only` is set. The apply propagates reconciled identity values into Terraform's `deployment_config`; `--deploy-services` without `--apply` reuses that output and is valid only when it is current.

Only runtime private-image conversion needs its managed identity's `AcrPull`. Thin-service groups convert the public Python digest directly. A failed conversion stops deployment without a registry credential fallback. Prepare all four group-scoped images before services; reuse unchanged images. Capture records endpoints, loaded source-release hashes, compute IDs, and native connections/receivers.

A source-only update reuses the existing Sandbox and URL. It verifies source bytes, enters gateway maintenance before touching the runtime, checks unfinished gateway work plus actual native tasks/executor threads, switches the supervised process, and requires health to attest the exact release hash. Completed/quarantined receipts may await settlement; unknown effects are not replayed. Failure restores the preceding application configuration.

A base-image, compute-setting, or Role Release change may replace runtime compute. Deployment first publishes a final native backup and stops if it fails. A new runtime restores its native Hermes home from the preserved volume. The separate gateway journal is restored with the gateway. Superseded gateway/MCP resources are captured as `superseded_sandbox_ids` for retirement only after real replacement validation.

All service ports are OnDemand. Gateway, runtime, and both MCP services suspend to Memory after 300 idle seconds. Only active work sends self-ingress. Native receivers wait outside the actors, pass the whole encoded broker envelope to fixed callbacks, and never equate a callback receipt with broker ACK.

The script creates the scheduling connection and receiver under the settling gateway MI, verifies immutable ownership, and removes temporary creation grants in `finally`. The native namespace MI receives and executes callbacks only on the gateway group; the gateway renews/completes through its own connection. Native trigger GET redacts authentication and normalizes method casing: only a real scheduled callback, checked journal, and successful settlement prove the live scheduling transport.

Source polling remains platform-managed and can delay scheduled work. Teams turns are handled directly and call native Runs without Service Bus admission or settlement. During idle verification, inspect only management metadata: health requests, console execution, and file access can wake compute.

Unknown scheduled effects are retained in the journal and dead-lettered through the original owning connection. This is terminal quarantine, not successful execution. Inspect the error and original schedule occurrence before any manual recovery; never clear the receipt or resubmit under a fresh identity to make an update pass.

### 6. Update the Agent 365 endpoint as its direct owner

Required only if compute replacement changed the gateway URL, not for an ordinary source release. Teams and email continue to use the registered URL until its direct blueprint owner reconciles it. Check the real endpoints:

```powershell
$bridge = (Get-Content ".local\$worker\apps\terraform-outputs.json" | ConvertFrom-Json).bridge_url
$registered = (Get-Content ".local\$worker\agent365\$worker-agent365-identifiers.json" | ConvertFrom-Json).messagingEndpoint
"$bridge/api/messages" -eq $registered
```

`a365` endpoint update is destructive **DELETE → POST**. The wrapper first requires the signed-in Graph user to be a direct blueprint owner; unreadable ownership or a nonowner fails before local mutation or `a365` invocation. Administrative roles alone do not satisfy that check. Terraform stays on the operator login:

```powershell
Invoke-AsAdmin {
    uv run python -m scripts.setup_agent365 `
      --state-name $worker --tenant-id $tenant --update-endpoint --capture
}
```

This registers the captured URL and preserves existing names/identities. The wrapper passes the existing agent name and explicit tenant to current `a365`; an unqualified CLI invocation can rename local config/generated files when it detects another active tenant. Never run setup in a mismatched context or create replacement identities. If deletion succeeded but registration failed, repair that same endpoint as its owner before Teams validation.

### 7. Publish only for a new Worker or changed manifest

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
uv run python -m scripts.demo_ops status --state-name $worker
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --plan
```

`status` checks Sandbox state and bridge health; the health request intentionally wakes the gateway. Verify model and tool behavior by sending a real message through the registered Agent 365/Teams surface. The Service Bus checks below validate scheduled callbacks, not interactive Teams delivery.

Feature-specific live checks:

```powershell
uv run python -m scripts.user_schedule_smoke `
  --state-name hermes2 --due-seconds 180 --timeout 1200
uv run python -m scripts.servicebus_dream_smoke `
  --state-name hermes2 --timeout 1800
```

- Schedule smoke checks native schedule/queue state, durable execution/delivery receipts, output hash, and DLQ, then removes the test job.
- Dream smoke runs an ad-hoc occurrence through the Sandbox management plane without advancing production cron or preparing a Learning Packet.
- For resume, stop an otherwise idle runtime, send a real Teams message, and verify the same Sandbox ID and an unchanged marker file in the Worker home. Deleting/recreating compute is a different operation.
- For durability, read `backup` in runtime `/health` after a Teams conversation: `lastArchive` changes within about a minute and `lastError` stays empty. For a restore drill, `demo_ops reset-sandbox --execute` publishes a final backup and deletes compute; rerun step 5 and compare session, message, skill and cron counts with the values before the reset.
- For automatic idle/keepalive, run a native model/tool operation for longer than 300 s, then observe only Sandbox management metadata until it stops. Health polling, open ingress connections, execute calls and file reads reset idle time and invalidate this measurement. Repeat with the plugin disabled only in an isolated test Sandbox. Verify OnDemand wake directly as well as through the gateway; compare transport readiness separately from the model's first/final response.

Use [DEMO.md](DEMO.md) for Teams attachments, comments, cards, app authorization, and learning evaluation.

Previously recorded 2026-11-05 live checks validated Memory stop/wake, private/public MCP execution, Teams/email replies, user scheduling, and ad-hoc Dreaming. Those checks predate the current scheduling-only transport and removal of the model-invocation API; they do not prove the revised deployment. Re-run the deployed checks above after applying this architecture. The recorded timings are observations, not SLAs.

A separate human-assisted channel check verified a real structured Agent User mention, a completed/settled native run, and the reply in Teams search and its thread under the demo tenant. The corporate operator WorkIQ connector was not used. Rich-editor text insertion required human assistance; the actual post contained only the unique marker, so the resulting clarification proves channel routing/delivery, not compliance with an exact-echo instruction. The marker was absent from all checked memory/skill Markdown files. Personal-chat success alone must never be treated as proof of group routing.

## Scheduling and release changes

To configure Service Bus Dreaming on an existing Worker:

```powershell
uv run python -m scripts.setup_app_tfvars `
  --state-name $worker --autopilot-name $worker `
  --user-scheduling-enabled --servicebus-dream-enabled `
  --servicebus-dream-cron-expression "0 2 * * *" --runtime-only
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --apply --auto-approve --capture
```

`setup_app_tfvars` preserves unspecified settings; `--apply` redeploys services from the updated settings. Service Bus Dreaming requires user scheduling. There is no bridge interval timer: native cron owns the schedule and wakes the gateway through its managed receiver. Manual Dreaming uses `scripts.servicebus_dream_smoke`, which enqueues a separate ad-hoc occurrence through Sandbox management and leaves production cron unchanged. Dreaming never prepares or exports a Learning Packet; run `scripts.collective_learning prepare` explicitly when desired.

Only the Worker owner receives the learning digest and may undo, approve or share what the Worker learned. Set the owner's Entra object ID, normally the Agent Identity sponsor, the same way: `setup_app_tfvars --state-name $worker --autopilot-name $worker --worker-owner-id <object-id> --runtime-only`, then apply. Without it, nobody receives the digest.

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

`logs` prints the target unless `--execute` is given; `--app` is `gateway` (alias `bridge`), `runtime` (requires `--path`), `private-mcp`, or `public-mcp`, and the Sandbox must be running. `reset-sandbox` previews deletion of runtime compute; `--execute` first publishes a final Worker backup, then deletes compute while the `hermes-backups` volume stays. Use it only for intentional replacement, then rerun step 5 with `--deploy-services --capture`; the new runtime restores the newest backup. The gateway does not recreate runtime compute. Do not reset healthy suspended or `Stopped` compute to perform a resume test.

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

For an in-place upgrade, do not tear down the Worker. Retire only captured `superseded_sandbox_ids` after real replacement validation and after the registered Agent 365 endpoint matches the new gateway. Protect current Sandbox/image IDs, all Worker identities/groups/volumes, generated apps, native home/backups, and the active gateway journal, including quarantined effects. A gateway's `/backup/transport` is current durable state, not an obsolete runtime journal.

Delete unused converted images only after enumerating remaining Sandboxes and snapshots. `DiskImageHasDependents` is a real platform refusal even when the public inventory shows no matching dependent: retain and report that exact image; never delete the live Group, state, or permissions to force cleanup. Registry cleanup must preserve the active runtime digest/conversion tag and the pinned upstream build cache. Delete only unreferenced hashed application directories and installer archives, checking current configuration and process references first. Legacy runtime transport data is removable only when every old receipt is completed and settled; the active gateway's unknown/dead-lettered receipts are never part of that cleanup.

Remove a whole Worker with one command. It previews by default:

```powershell
uv run python -m scripts.worker_lifecycle teardown --state-name worker3
uv run python -m scripts.worker_lifecycle teardown --state-name worker3 `
  --execute --admin-config-dir $admin --restore-state hermes2
```

Review the printed inventory before `--execute`. It deletes the Worker's Sandboxes, volumes, and disk images (only in Sandbox Groups named for the Worker), destroys and deletes Terraform workspace `autopilot-<worker>`, deletes its Agent User, Agent Identity, and blueprint with the admin login, reactivates `--restore-state` (default `hermes2`), and removes `.local\<worker>\`. Shared platform resources are untouched.

`scripts.demo_cohort reset` is reserved for already provisioned disposable `demo-*` Workers/workspaces/volumes; preview first and supply their reviewed baseline release/commit. Never reset long-lived Workers backward or rewrite shared Role Release history to replay a classroom exercise.
