# Deployment guide

This guide covers repeatable Azure deployment and Worker lifecycle operations. Demonstration prompts belong in [DEMO.md](DEMO.md).

**Status, 2026-09-06 22:33 CEST:** both final Workers and endpoints are deployed; native inference, MCP, schedule, no-change Dream, resume, operator `401`s, and nonowner preflight blocking are verified. All 49 reviewed native parents resolve; 105 audited spans contain metadata only. Controlled requests isolate changed parent IDs to the Sandbox-origin egress path, while TraceId survives. Platform intermediate parents are absent from AppInsights: this is a current native-platform waterfall limitation. Terminal `Failed` service replacement is corrected without an image/endpoint rollout. Targeted inbound, automatic eight-hour idle behavior, independent VNet/MI-authentication evidence, and general learning quality remain separate gaps.

**Operator scope:** the staged sequence below modernizes existing Workers. Preserve their Agent365 blueprint, AgentIdentity, AgentUser, and local identity/configuration state. The update was exercised on both existing Workers; this does not validate first-time provisioning or every Teams/authorization scenario. First-time bootstrap is unresolved: infrastructure-only output has no native bridge URL, A365 setup requires a real endpoint, and identity setup requires existing A365 state. Do not use placeholder endpoints, copied identities, or the obsolete runtime-only → apply → new A365 setup sequence.

The current modernization reuses existing Agent 365 blueprints and Agent Users. On that path, apply ARM infrastructure with `--infrastructure-only`, reconcile runtime/gateway federation, then create services with `--deploy-services` and capture their real endpoints. A fresh Worker cannot invent a messaging endpoint to break the identity/endpoint bootstrap dependency; the first-deployment sequence below still requires a verified bootstrap path before it is treated as turnkey.

## Prerequisites

- Azure CLI authenticated to the intended subscription and tenant.
- Terraform.
- `uv`.
- Agent 365 CLI (`a365`).
- GitHub Agentic Workflows CLI (`gh extension install github/gh-aw`).
- GitHub CLI for Promotion pull requests.
- Tenant rights for Agent 365 blueprint creation, consent, Agent Identity, Agent User, licensing, and registration.
- Available Microsoft 365 service licenses for each Agent User capability.

```powershell
az login --use-device-code
Set-Location .\autopilots-on-azure
uv sync --frozen --index-url https://packagefeedproxy.microsoft.io/pypi/simple
terraform -version
```

Use an isolated browser profile for tenant consent. Confirm the account and tenant before accepting.

Public PyPI is blocked in this environment. Hermes 0.19.0 was actually installed using the corporate feed above; it is not an untested version bump. Python and npm locks are committed, and weekly Dependabot opens dependency updates. Do not use unfrozen dependency resolution to work around a feed failure. Image builds must use the approved feed reachable from ACR without storing feed credentials in the image or repository.

### Native MI blocker and verified deployment-time conversion

SDK 0.1.0b3 with `managedIdentityResourceId`, SDK 0.1.0b4, and REST with `managedIdentityClientId` returned `401 RegistryAuthFailed` for native MI conversion. Identity attachment and `AcrPull` were verified. [Upstream issue #1768](https://github.com/microsoft/azure-container-apps/issues/1768) reports a managed-identity conversion SDK problem with no fix in the reviewed September 4 public status. The approved explicit short-lived Entra-token path subsequently succeeded with SDK b4: private-MCP disk image `deb1880f-c394-406e-9d02-760840e1cfd7` reached `Ready` on September 6. This is conversion evidence, not proof that native MI was fixed or the MCP service is reachable.

Both MCP Docker paths use frozen `uv.lock` resolution through the corporate feed. The private MCP image passed an actual ACR build, and targeted local tests passed: private MCP 6, public MCP 1. Image build, Sandbox disk conversion, service start, and end-to-end authorization are separate gates.

### Approved deployment-time token boundary

Classic bridge/private-MCP/public-MCP Container Apps pulled images through native managed identity. Historical Sandbox runtime conversion did not: committed `ensure_agent_sandbox` retrieved the ACR admin username and `passwords[0].value` through `az acr credential show`, then supplied `RegistryCredentials`. Azure CLI access to retrieve the password did not change what the registry received.

A short-lived Entra-derived registry token was **approved on 2026-09-06 for deployment-time conversion only**. The deployment helper uses the deployer's Azure CLI identity rather than reading the ACR admin password; the conversion API still receives an explicit bearer credential through `RegistryCredentials`. Microsoft documents registry login tokens as valid for three hours. This limits lifetime, not necessarily permissions, and is not native MI-only conversion or credential-free operation.

The helper prepares **all four** disk images—runtime, gateway, private MCP, and public MCP—before creating any service workload. The gateway receives the prepared runtime ID through `AGENT_RUNTIME_DISK_IMAGE_ID` and uses it for runtime startup/resume. Runtime/gateway do not acquire ACR credentials or convert images; workload identities have no ACR role assignments. Only the deployer needs registry/conversion permissions.

For direct `scripts.sandbox_run_runtime` invocations, supply `--disk-image-id` with the prepared image ID. `--registry-managed-identity-resource-id` has been removed; direct startup never converts an image or retrieves registry credentials.

Keep the admin account disabled, keep bearer values out of logs, arguments, files, Terraform state and workload settings, and do not add a refresh daemon, token cache/service, or authentication fallback. New image content requires fresh deployment-time conversion, not runtime token renewal.

[ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md) records the accepted, user-approved short-lived-token decision and live conversion mechanism—not an admin-password fallback. All four images were built, both Workers' runtime/gateway paths serve real inference, and all four MCP services are healthy.

### Live MCP milestone and service limits

The four healthy MCP services are `hermes/private-mcp`, `hermes/public-mcp`, `hermes2/private-mcp`, and `hermes2/public-mcp`. Both Workers also have deployed gateway/runtime paths; authenticated private/public MCP scenarios passed on Hermes. Native endpoint URLs and prepared disk-image IDs are persisted beneath `.local\<worker>\apps\`; use those real values rather than constructing hostnames.

The deployment helper now respects the SDK's 63-character label limit and uses a `10Gi` service root disk with `500m` CPU; this CPU allocation does not allow a larger root disk. This is separate from the runtime's persistent Data Disk. Successful service health checks do not replace identity setup, scoped MCP calls, negative authorization tests, or private DNS/routing checks from the runtime.

**Coalesced live evidence, confirmed by the deployment owner September 6 at 22:33 CEST:**

- Public MCP endpoints reject unauthenticated requests with HTTP `401`.
- External connections to private MCP reset. This is not an observed HTTP `403` and does not prove the positive VNet path from the runtime.
- Repeating live MCP deployment retained the same Sandbox IDs. The registry-login function was guarded to fail if called; deployment succeeded without invoking it. This verifies prepared-image reuse without registry login for the tested MCP redeployment path.
- All ten workload `AcrPull` assignments were successfully removed and ACR `adminUserEnabled` is `false`.
- All four images are built and converted; both existing Workers' workloads are deployed. Both gateway/runtime boot and native Entra model invocations succeeded. Private/public MCP scenarios and their exact tool spans passed on Hermes. Controlled requests now isolate parent rewriting to the native Sandbox-origin egress path; the full waterfall remains incomplete.
- Initial Hermes deployment IDs, before later gateway redeployments: gateway `09e77569-bba7-4b05-add6-0c8e6772f557`; runtime `66c77cbf-9654-47a8-84cb-1fa6d69c5be1`. The real `/invoke` returned HTTP `200` / `Hermes bridge OK`, not merely health.
- Both final Agent365/Teams gateway endpoints are registered through the explicitly authorized owner/admin path. The isolated cache was removed; `tomas` remains default Azure operator. Both final model checks returned `200`; both operator pending GETs returned `401` without a key. These negative GET checks do not prove every operator authorization route.
- The direct-owner endpoint-update preflight was also live-tested: a nonowner was blocked. The parent's latest test run passed 351 tests; earlier suite counts elsewhere describe their own narrower runs and must not be added to this count.
- Hermes 2's real Service Bus user schedule produced a delivered receipt and verified output SHA with DLQ `0`. Gateway was Running/auto-suspend `false`; runtime was already Running. This is delivery evidence, not wake/resume proof.
- Hermes 2's ad-hoc Dream completed with phase `prepared`, `success=true`, `recordCount=0`, and `packet=null`. Production cron was unchanged, scheduled message count remained `1`, and DLQ was `0`. A legitimate no-change result proves this bounded execution path, not learning improvement or crash recovery.
- Direct SDK stop/start on runtime ID beginning `4358e341` proved `Running → Stopped → Running`, preserving the same ID and a persistent Data Disk marker; reported resume timing was 1.3 s. The marker was removed afterward.
- The initial application-resume probe returned a model `200` but created a new Sandbox ID: legacy `scripts.sandbox_runtime.recycle_stopped_agent_sandbox` deleted `Stopped` runtimes as if they were failed. That is recreation, not resume.
- Stopped-runtime recycling is removed; only `Failed` is recycled. The rebuilt gateway was deployed and the application-resume check retained runtime ID `65db4109-ee34-4b52-a296-31d5499a8f3e` and its Data Disk marker. This path took 28.5 s including model execution, versus the separately scoped 1.3 s direct SDK test—not a like-for-like latency comparison. Neither proves automatic suspension after eight idle hours.
- The user's Teams UI recheck found Hermes in public group `@` mention discovery but not under `/`. No private content was sent in that recheck. Targeted inbound availability remains unobserved for this deployment; neither universal platform incompatibility nor a pure UI bug is established.
- Generated manifest 1.1.7 uses `devPreview` and `agenticUserTemplates` only, with no `bots[]`. Refreshed Learn still documents receive opt-in through `bots[].supportsTargetedMessages`. Installed `microsoft-agents-hosting-core` 1.1.0 `TurnContext` lacks `send_targeted_activity`, and plain `send_activity` does not set targeting. These package/SDK observations are not a platform-wide prohibition. Do not add a bot fallback; see [ADR 0018](docs/adr/0018-teams-command-surfaces.md).
- Real Hermes 0.19 CLI evaluation through `azure-foundry` / `gpt-5-6-terra` completed: independent baseline `3/4`, candidate `4/4`, regressions `0`. The four response-only cases were manually/operator-authored (`independence=operator_declared`, `packetDigest=null`), not proposed by a learning packet. The local report is `.artifacts\role-330-evaluation-verified.json`. This does not measure statistical generalization, tool workflows, native skill discovery, or broad learning quality.
- The initial evaluation authentication failure came from isolated `USERPROFILE` hiding the Azure CLI cache. Explicitly inheriting `AZURE_CONFIG_DIR` restored access to the existing cache; credentials were never copied.

**Earlier identity/service evidence:** Graph CAE required a new device-code login before both identity reconciliations succeeded; identities/grants and schedules were preserved. All four MCP services emit `/app/.sandbox-service.log`, and service replacement was observed. Do not generalize that replacement to runtime resume: `Stopped` is resumable, as the direct SDK test now proves. A redundant SDK stop call returning `409` does not justify deleting a healthy stopped runtime.

**Terminal service failure handling:** the operator excludes a terminal `Failed` gateway/MCP instance from matching-image reuse and can replace matching or stale failed instances. An unchanged matching `Stopped` or `Suspended` service remains eligible for same-ID resume; a changed deployment is a separate replacement case. Nineteen targeted Sandbox tests passed. The live private-MCP redeployment retained its existing healthy ID and returned health `200`; that verifies healthy reuse, not a live failed-instance replacement. This operator-only change required no image rebuild or endpoint rollout. Do not add its test count to the earlier 351-run total.

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

Workers can share the same Role Blueprint, Role Release, images, Foundry deployment, networking, ACR, and MCP resource applications. They do not share service Sandbox Groups or their user-assigned identities.

## 1. Deploy the shared platform

The platform layer creates networking, ACR, the private-ingress Express ACA managed environment, Private Endpoint/private DNS, Foundry, and keyless Application Insights/Log Analytics. Per-Worker application infrastructure owns separate Groups and user-assigned identities for `runtime`, `gateway`, `private-mcp`, `public-mcp`, and `generated-apps`.

```powershell
Set-Location .\terraform\platform
terraform init
terraform apply
Set-Location ..\..
```

The current topology uses:

- Sweden Central for Foundry and ACA Sandboxes;
- the application network and ACR in North Europe;
- global VNet peering and shared private DNS.

Private MCP runs in a Sandbox Group linked to the Express managed environment, not a classic Container App. Group-to-environment linking is irreversible; verify the intended environment before applying. Do not solve a private-ingress failure with a public endpoint.

## 2. Build images

Build Hermes runtime plus the gateway and both MCP image/source pairs for the existing Worker states:

```powershell
uv run python -m scripts.build_images --runtime hermes --state-name hermes --state-name hermes2
```

The command writes digests and matching tagged OCI disk-source images into the selected Worker states. Existing per-Worker tfvars are required; `scripts.build_images` refuses missing state. Runtime deployment stays pinned to a digest; Sandbox disk-image conversion uses the matching tagged OCI source. Rebuild after runtime or bridge code changes. All four images are built and both Workers' bootstrap/model invocations succeeded independently. Shared image-build success alone would not establish either invocation result.

The gateway is now a Sandbox with auto-suspend disabled, not a KEDA-scaled Container App. It must preserve detached Agent 365 work after HTTP acknowledgement and continuously receive Service Bus messages. Hermes has a 900-second bridge budget and an independent OnDemand runtime/Data Disk lifecycle. This is not a full scale-to-zero architecture.

Gateway and MCP service ports use native anonymous HTTPS transport. Agent 365 SDK or MCP Entra validation still authenticates application requests. No model proxy is deployed: Hermes 0.19.0 uses native `azure-foundry` with an Entra token callback. The separate loopback MCP identity adapter remains.

Generated web apps run in one child Sandbox per app in the dedicated group. The bridge identity receives only Sandbox Group Data Owner there. Each public port is Entra-authenticated, participant-limited, and `OnDemand`; the ADC proxy authenticates the user, resumes stopped compute, and forwards directly to the app. Apps suspend after five idle minutes and default to native deletion 24 hours after suspension. Owner-bound Adaptive Card actions update the native retention policy or delete the Sandbox directly; Service Bus is not part of this lifecycle. The bridge manages lifecycle but never proxies app traffic.

## 3. Configure a Hermes Worker

Select the existing Worker and its explicit workspace:

```powershell
$worker = "hermes"
$workspace = "autopilot-hermes"
```

For the second existing Worker:

```powershell
$worker = "hermes2"
$workspace = "autopilot-hermes2"
```

Keep `--runtime hermes` for both. Workspace selection cannot be omitted: its default follows runtime, not state name. Preserve existing identity state and scheduling/Dreaming settings: Hermes `false/false`, Hermes 2 `true/true`.

**Role Release status, 2026-09-06 21:20 CEST:** Role Blueprint 3.3.0 was published to `origin/main` at immutable commit `b6b7f64d8ee92b1f1d1fd8b1023ce0f0b486123c`, with explicit approval covering only its four files. Hermes requires `>=0.19.0`. Both configurations are pinned and both Workers serve model invocations. The four-case response-only evaluation completed at baseline `3/4`, candidate `4/4`, zero regressions; this is a bounded result, not general learning-quality proof. Other modernization code/docs remain uncommitted.

Apply infrastructure without creating native services:

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name $worker `
  --workspace $workspace `
  --apply `
  --infrastructure-only `
  --capture
```

The captured `.local\<worker>\apps\terraform-outputs.json` records the workspace/runtime and each Sandbox Group's identity and network metadata. It does not invent a native bridge URL. `--apply` normally deploys services, so `--infrastructure-only` is essential at this stage.

## 4. Reconcile existing Worker federation

Reconcile the already-provisioned blueprint, AgentIdentity, and AgentUser:

```powershell
uv run python -m scripts.setup_identity `
  --runtime hermes `
  --state-name $worker
```

This updates gateway/runtime federation credentials and writes current identity values to `.local\<worker>\apps\generated.app.auto.tfvars.json`, preserving existing Agent365 identities and grants. If Graph CAE demands interactive reauthentication, complete a new device-code login before retrying. Verify the existing blueprint's permission inheritance:

```powershell
Set-Location ".local\$worker\agent365"
a365 query-entra inheritance
Set-Location ..\..\..
```

Every listed resource must report effective inheritance. Warnings about an empty permission type are acceptable when the other type is granted and effective inheritance is `OK`.

The Agent 365 platform blueprint is not the Git Role Blueprint. See [ADR 0014](docs/adr/0014-per-worker-agent365-blueprints-and-bridges.md).

## 5. Apply identity configuration and deploy services

Apply the values written by identity reconciliation before creating services:

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name $worker `
  --workspace $workspace `
  --apply `
  --deploy-services `
  --capture
```

The second apply is necessary because the service helper reads Terraform's `deployment_config`, not the newly edited tfvars directly. Bare `--deploy-services` is appropriate only when that output already contains the current identity/configuration. This staged identity reconciliation is explicit; it is not `local-exec` or retrying an apply with guessed flags.

Keep the captured native bridge/private/public URLs, Sandbox IDs, runtime disk-image ID, and `sandbox_services` mapping. Sensitive `deployment_config` is excluded from the captured JSON. The gateway receives `AGENT_RUNTIME_DISK_IMAGE_ID`; no registry credential is moved into workload settings.

Existing Agent Users still require individual licenses for the Microsoft 365 services they use:

| Capability | Typical required service license |
| --- | --- |
| Teams chat and channel membership | Teams Enterprise |
| Agent 365 management | Agent 365 |
| Mailbox, calendar, SharePoint, OneDrive | Appropriate Microsoft 365 suite |
| Embedded Copilot / Work IQ scenarios | Microsoft 365 Copilot and applicable suite |

Do not assign E5 or Copilot merely for runtime learning or direct bridge tests.

Resource provisioning after license assignment commonly takes 10-15 minutes and can take longer.

## 6. Update the existing Agent365 endpoint

**Owner-only update:** run this step only from an explicitly approved, isolated Azure CLI session for an **existing direct blueprint owner**. Retain the original deployment operator for Terraform. Observed `a365` CLI 1.1.214 behavior deletes the old endpoint before creating its replacement; a nonowner's replacement POST can fail with `403`, leaving the endpoint missing.

`setup_agent365 --update-endpoint` now performs a read-only preflight **before changing local configuration or invoking `a365`**. It identifies the active Azure CLI Graph user through `/me`, resolves generated `agentBlueprintId` (an appId) to the application object ID, and reads `/applications/{id}/microsoft.graph.agentIdentityBlueprint/owners?$select=id`, following all pages. Nonowners and unreadable ownership fail closed without an endpoint update; administrative access alone is not a direct-owner match. The script does not grant ownership, switch login, or fall back. See the [official blueprint owners API](https://learn.microsoft.com/en-us/graph/api/agentidentityblueprint-list-owners?view=graph-rest-1.0).

Update the existing blueprint with the real captured bridge URL:

```powershell
uv run python -m scripts.setup_agent365 `
  --runtime hermes `
  --autopilot-name $worker `
  --update-endpoint `
  --capture
```

Do not use `--run-setup` or provision replacement identities in this modernization chain. Endpoint update retains existing blueprint metadata. When `agent365\ToolingManifest.json` changes, reconcile every Worker's permissions before applying and deploying; reconciliation requests only missing permissions.

Both endpoints were repaired and their final updates live-verified through the explicitly approved isolated owner/admin login. That cache was logged out and removed; the original operator login remained unchanged. The parent subsequently live-verified that a nonowner is blocked by the preflight. The earlier 28 targeted local tests plus CLI help checks performed no cloud writes and remain a separate evidence set. Endpoint reconciliation does not prove every Teams inbound scenario or private group-thread behavior.

After permissions are current, generate the Teams package:

```powershell
uv run python -m scripts.setup_agent365 `
  --runtime hermes `
  --autopilot-name $worker `
  --agent-name $worker `
  --publish
```

Publishing now fails with the missing server names when a Worker's consents lag behind `ToolingManifest.json`. The post-processor otherwise bumps the manifest version and removes unsupported bot capabilities from Agent User packages. Upload `.local\<worker>\agent365\manifest\manifest.zip` only through Microsoft 365 admin center **Agents > All agents > Upload custom agent**. Teams app-store upload rejects `agenticUserTemplates` packages; see ADR 0018.

The Agent Registry permission summary can lag or omit inherited Agent User permissions. Treat `a365 query-entra inheritance` as the authoritative configuration check and require every listed resource to report effective inheritance. Confirm actual access with the workload smoke rather than the Registry label.

Use `--skip-workiq-permissions` when the Worker has no mailbox/Copilot scenario.

Detailed identity troubleshooting is in [the identity and MCP runbook](docs/runbooks/identity-mcp.md).

## 7. Inspect the updated Worker

```powershell
uv run python -m scripts.demo_ops status `
  --runtime hermes `
  --state-name $worker
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

Live Work IQ Word smoke:

```powershell
uv run python -m scripts.document_smoke `
  --state-name $worker `
  --timeout 1200
```

This creates a temporary DOCX in the Worker Agent User's OneDrive, reads back a unique marker, adds a comment, replies to it, and validates the returned SharePoint URL and operation results. Work IQ Word currently has no delete tool, so remove smoke documents from the Agent User's OneDrive when they are no longer useful.

Dry-run or execute a real proactive Agent User Teams message:

```powershell
uv run python -m scripts.m365_actions_smoke `
  --state-name $worker `
  --recipient user@contoso.com

uv run python -m scripts.m365_actions_smoke `
  --state-name $worker `
  --recipient user@contoso.com `
  --execute `
  --timeout 1200
```

The executed smoke creates or reuses a one-to-one chat, sends a unique project follow-up as the Agent User, then reads the exact message back through Work IQ Teams.

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

### Live telemetry evidence and the native-platform parent limitation

External Foundry registration uses `ExternalAgentDefinition(otel_agent_id)` with preview-enabled project access and a `get()` read-back. The actual `autopilots-hermes` registration was created/read back and unchanged on repeat. It registers metadata only; it neither hosts nor invokes the Worker. Azure Monitor export uses Entra CLI/managed-identity authentication and excludes prompt/tool payloads by default.

Inspect the verified registration without modifying it; add `--apply` only to create/reconcile its external definition:

```powershell
uv run --no-sync python -m scripts.register_foundry_external_agent `
  --project-endpoint https://autopilots-ehvw.services.ai.azure.com/api/projects/autopilots-project `
  --agent-name autopilots-hermes `
  --otel-agent-id autopilots-hermes `
  --auth azure-cli
```

The Hermes 0.19.0 plugin instruments actual `llm_execution` and `tool_execution` callbacks, including streaming and available usage counts. Startup/plugin activation, fresh headers, real wrapper handoff, HTTP `409` conflicts, and uncertain-`5xx` lease retention are locally integrated. The combined 87 adapter/runtime/bootstrap/native-helper tests passed, including installed Hermes discovery, offline model SDK transport, and a real native file tool in a separate process. They do not prove deployed gateway operation, live model inference, or telemetry ingestion.

For deployed correlation, use an explicit-session API: `/api/sessions/{id}/chat`, or `/v1/chat/completions` with `X-Hermes-Session-Id`. The wrapper's short-lived local context handoff bridges the gateway's executor-thread context loss. `/v1/responses` creates its own session; require the explicit `autopilots.trace.correlation=missing` marker rather than treating separate spans as one trace.

After cancellation or transport interruption, the handoff lease remains because native work may still run. Expiration stops attribution but does not make the session safe to reuse. Restart the Hermes runtime wrapper and native gateway before reusing that session; restarting only the public bridge does not stop the native executor. Do not delete the lease merely to bypass the conflict.

After a real supported explicit-session model/tool invocation, allow 2–5 minutes and inspect **Foundry Agents > autopilots-hermes > Traces**. Verify trace membership, actual ParentId joins, and privacy separately: a shared TraceId or `correlation=propagated` alone does not prove a fully connected waterfall. A `missing` correlation marker remains a failure.

**Read-only live verification, updated September 6 at 22:19 CEST:** the full-day September 6 query, executed at `18:06:31 UTC`, matched the actual invocations using hashed bridge conversation identifiers in Log Analytics workspace `590b0c22-7aa7-42ad-9f6f-4c59d48aa908`, Application Insights `appi-autopilots-ehvw`. The earlier last-four-hours lookup excluded the morning MCP invocation; its absence was a query-window mistake, not an access or ingestion blocker. All four following cases used Role Release 3.3.0.

| Evidence | Observed result |
| --- | --- |
| Initial smoke, trace `75450e6e528b0236f34bd9c423cea78e`, 08:58:25–08:58:56 UTC | HTTP `200`; one native model span, 12,881 input / 8 output tokens; 11 total spans. |
| Hermes MCP, trace `9e02cd67edc33364ab97058d1c3af139`, 09:00:43–09:01:22 UTC | HTTP `200`; six successful `gpt-5-6-terra` model spans, 84,855 input / 368 output tokens in aggregate. Three actual tool spans: `skill_view`, `mcp__private_incidents__list_services`, `mcp__public_shipments__list_demo_shipments`. Eighteen total spans across bridge, wrapper, and native gateway. |
| Exact MCP tool attribution | Private tool: `09:01:19.306 UTC`, `99 ms`, span `fa0d9f0440e9e019`. Public tool: `09:01:20.409 UTC`, `85 ms`, span `6821eac5db1d98c9`. All nine native model/tool children share ingested runtime parent `e54a407438d6aca3`; correlation is propagated. |
| Hermes 2 user cron, trace `04dcef1944ae211549ca1893cdd52309` | Service Bus schedule at `16:57:17`, process at `17:00:17`, `/cron/fire` `200`, wrapper in-process native chat at `17:00:53` with 10,946 input / 15 output tokens, ACK `200` at `17:01:32` UTC. Eight total spans. Runtime was already Running: not wake proof. |
| Hermes 2 ad-hoc Dream, trace `7e85aa2783ca35e2779b39197d26aa1b`, 17:07:43–17:13:40 UTC | Run-now/send/process/claim/checkpoints, seven successful native gateway model spans and 31 successful tool-execution spans, then completion `200`. Tool spans include 18 propagated and 13 nested in-process executions; names include `skill_view`, `search_files`, `session_search`, `read_file`, `terminal`, `skills_list`. This is ad-hoc Dream, not the production cron occurrence; 31 spans are not 31 proven unique calls. |
| Native linkage across the four cases | All 49 native model/tool parents are ingested. The 38 Dream native spans have matching runtime/gateway parents and missing-correlation count `0`. |
| Latest reviewed privacy sample | Across 105 audited spans, only allowed metadata keys; zero raw identity fields, zero nonopaque identifiers, no Data/Url/Message content, and no AppTraces/AppExceptions rows in the reviewed scope. This is sample evidence, not an all-time privacy guarantee. |

The separately inspected later normal invoke `efff0a6353424dcbb0d83a4698585b25` at `17:36:30–17:37:01 UTC` contained model success (13,014 input / 8 output tokens, 2,419 ms), not a tool call. Native span `ee0c7aa5c9b1feb7` matched wrapper parent `a383a2cbc4c06faf`. This additional trace is not part of the four-case/49-native summary above.

**Native-platform parent limitation, isolated with two real read-only requests on September 6:** both targeted the runtime, but only the request originating inside a Sandbox changed its supplied parent.

| Request origin | TraceId | Supplied parent | Runtime-recorded parent |
| --- | --- | --- | --- |
| Local `urllib` to runtime | `31a14ce5fa8362e199a951e20b09cb36` | `f466c48ce55f3340` | `f466c48ce55f3340` — preserved |
| Fresh, uninstrumented `urllib` inside gateway Sandbox to runtime | `584cabe44d94e00671fa98ec16c11d82` | `6c756943513aba2b` | `e27aaa33d331e626` — changed; runtime server span `6775da76297a6d45`, HTTP `200`, `18:33:25 UTC` |

TraceId remained intact. This isolates the changed parent to the **Sandbox-origin egress path**, not ingress alone or application-exporter span loss. The exact proxy implementation is not identified. Application spans remain correlated through TraceId and opaque IDs, but platform intermediate parents are absent from AppInsights. The earlier normal invoke had 10 unresolved wrapper ParentIds; do not present a 100%-connected parent tree or manufacture intermediate spans to conceal this native-platform limitation.

No custom trace headers, fake parent spans, or egress-security bypass were added. Actual MCP attribution and native parent matching remain proven; tool execution alone still does not independently prove the private VNet route or MI-authentication mechanism.

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

1. Prepare and approve a Learning Packet when governed changes are worth exporting, or explicitly sign a `reject_and_refresh` disposition when discarding them.
2. Publish the newer immutable Role Release.
3. Update Worker tfvars with the new release and commit.
4. Apply the Worker's Terraform workspace.
5. Invoke the bridge.

Refresh preflight validates the exact state-bound approval or rejection before deleting the old Sandbox. Rejection does not approve export and must not manufacture an approved packet. Personal Memory, Private Playbooks, and Work History remain on the Data Disk. Role Skills are replaced and previous Candidate Improvements are archived.

## Scheduled Dreaming

Bridge-owned scheduling is the classroom path. It runs inside the non-suspending gateway and uses the same Worker transaction as manual operations.

Enable it for one Hermes Worker:

```powershell
uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --state-name hermes2 `
  --autopilot-name hermes2 `
  --scheduled-learning-enabled `
  --scheduled-learning-initial-delay-seconds 300 `
  --scheduled-learning-interval-seconds 86400 `
  --scheduled-learning-max-records 3 `
  --scheduled-learning-retry-limit 3 `
  --scheduled-learning-retry-backoff-seconds 30 `
  --scheduled-learning-prepare-packet `
  --runtime-only

uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name hermes2 `
  --workspace autopilot-hermes2 `
  --apply `
  --auto-approve `
  --capture
```

Run and inspect a cycle without waiting for the interval:

```powershell
uv run python -m scripts.demo_ops scheduled-run --state-name hermes2 --timeout 900
uv run python -m scripts.demo_ops scheduled-status --state-name hermes2
```

Dreaming prepares a packet only when transferable records exist. Human digest approval, export, Collective Learning Review, Promotion, and Worker Refresh remain separate gates.

### Production Service Bus Dreaming

Production Dreaming uses a reserved Hermes system schedule. Its one next occurrence is a `system.dream` Service Bus message received by the running gateway. The gateway wakes the runtime and runs the same Dreaming and packet-preparation coordinator used by the operator endpoint.

```powershell
uv run python -m scripts.setup_app_tfvars `
  --runtime hermes `
  --state-name hermes2 `
  --autopilot-name hermes2 `
  --no-scheduled-learning-enabled `
  --user-scheduling-enabled `
  --servicebus-dream-enabled `
  --servicebus-dream-cron-expression "0 2 * * *" `
  --runtime-only

uv run python -m scripts.deploy_apps_runtime `
  --runtime hermes `
  --state-name hermes2 `
  --workspace autopilot-hermes2 `
  --apply `
  --auto-approve `
  --capture
```

Enqueue one operator-triggered real occurrence without changing the configured schedule:

```powershell
uv run python -m scripts.servicebus_dream_smoke `
  --state-name hermes2 `
  --due-seconds 180 `
  --timeout 1800
```

The smoke checks due-message receive, runtime readiness, the durable system receipt, optional packet preparation, DLQ state, and an unchanged production occurrence. The September 6 user-schedule and ad-hoc Dream runs passed on Hermes 2. Its runtime was already Running during the schedule test, so queue-driven wake from suspension remains a separate check; the independent stop/start proof does not establish that combination. A zero-replica/KEDA assertion from the old topology is not compatible with the non-suspending gateway.

Phase checkpoints and fencing permit replay of a persisted completed response. An ambiguous `dream_started` stops rather than repeating the model/tools blindly. Run-now has a separate occurrence identity. Teams can still accept a send before the runtime records its receipt; that crash window is not exactly-once delivery.

### Disposable demo cohort reset

Create demo Workers with `demo-*` state names, Worker IDs, Data Disks, and Terraform workspaces. Pin their tfvars to the immutable classroom baseline.

Reset is dry-run by default:

```powershell
uv run python -m scripts.demo_cohort `
  reset `
  --state-name demo-hermes-a `
  --workspace demo-hermes-a `
  --baseline-release 3.1.0 `
  --baseline-commit 60b8e7ef3fb594f386d5177032df434eb4e62917
```

After reviewing the exact Sandbox and Data Disk names, add `--execute`. The command refuses any resource whose state name, Worker ID, Data Disk, or workspace does not start with `demo-`.

Create an ephemeral Git baseline for demonstrations that include merge and Worker Refresh:

```powershell
uv run python -m scripts.demo_cohort create-git-base `
  --branch demo/collective-learning-class `
  --baseline-commit 60b8e7ef3fb594f386d5177032df434eb4e62917
```

Pass `--base-branch demo/collective-learning-class` and a unique `--promotion-branch` to `scripts.collective_review`. After resetting the demo Workers, delete the lane:

```powershell
uv run python -m scripts.demo_cohort delete-git-base `
  --branch demo/collective-learning-class `
  --close-pull-requests
```

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
