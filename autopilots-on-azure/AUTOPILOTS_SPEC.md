# Autopilots on Azure product specification and roadmap

## Document role

This document defines product requirements, the implemented capability baseline, future design, milestones, and exit criteria.

- Current system architecture and trust boundaries: [ARCHITECTURE.md](ARCHITECTURE.md)
- Deployment and operator guidance: [README.md](README.md)
- Detailed multi-step procedures: [docs/runbooks](docs/runbooks)
- Consequential design rationale: [docs/adr](docs/adr)

This is the active specification for the project from the current implementation point forward. OpenClaw and Hermes are peer runtimes behind one Microsoft 365 bridge pattern. Agent 365 is the primary Microsoft 365 packaging and installation path.

## Implemented capability baseline

The repository has one shared Azure hosting pattern with runtime-specific app deployments:

```text
Agent 365 / /invoke
  -> runtime-specific bridge Container App
  -> ACA Sandbox runtime selected by AGENT_RUNTIME
       -> OpenClaw Gateway on port 18789
       -> Hermes API server on port 8642
       -> loopback Agent Identity MCP adapter
            -> private incidents MCP through the Sandbox VNet connection
            -> public shipments MCP through HTTPS
            -> Agent 365 Work IQ Mail MCP
```

Implemented; live-verified items are called out explicitly:

- `autopilots-on-azure` project layout.
- `bridge\runtime\AgentRuntimeAdapter` abstraction.
- `OpenClawRuntimeAdapter`.
- `HermesRuntimeAdapter`.
- Generic `AgentSandboxConfig` lifecycle for ACA Sandbox.
- OpenClaw runtime image under `runtimes\openclaw`.
- Hermes runtime image under `runtimes\hermes`.
- Private incidents MCP works from both OpenClaw and Hermes.
- Private incidents MCP uses Agent Identity tokens carrying `Incidents.Read.All`; the old static key and bridge relay are removed.
- Public shipments MCP runs as an Entra-protected, scale-to-zero ACA endpoint and works from both runtimes with `Shipments.Read.All`.
- Work IQ Mail works from both runtimes with the runtime's Agent User and `Tools.ListInvoke.All`.
- The public shipments endpoint is registered and approved as Agent 365 BYO MCP `ext_Shipments` for Tooling Gateway and Defender telemetry demonstration.
- Side-by-side app deployments use separate Terraform workspaces:
  - `autopilot-openclaw`
  - `autopilot-hermes`
- Separately managed Azure Bot Service resources and classic Teams app installations are removed; Agent 365 is the only Microsoft 365 app lifecycle. Agent 365 still uses Microsoft 365 Agents SDK Activity Protocol and connector infrastructure.
- Agent 365 setup script supports both runtimes:
  - `uv run python -m scripts.setup_agent365 --runtime openclaw`
  - `uv run python -m scripts.setup_agent365 --runtime hermes`
- OpenClaw and Hermes Agent 365 blueprints, endpoint registration, manifest packages, and non-secret identifier captures have been generated.
- `scripts.provision_agent365_instance` automates the Graph-based Agent 365 AI teammate instance path:
  - app-only helper for `beta/copilot/agentRegistrations`
  - Entra Agent ID identity creation
  - linked `microsoft.graph.agentUser` creation
  - usage location and license assignment
  - Agent 365 registration creation
- Hermes has a clean scripted instance:
  - agent user `hermes1@MngEnvMCAP058702.onmicrosoft.com`
  - registration `T_4d76e01d-2b3f-671e-fd3f-80ea7e3aa3f7`
  - blueprint `86759724-ff11-45f4-9f8e-265d4f2fa1ef`
- OpenClaw has a clean scripted instance:
  - agent user `openclaw1@MngEnvMCAP058702.onmicrosoft.com`
  - registration `T_75259b23-1388-35fe-0e49-9b092a4813f4`
  - blueprint `916c51a6-d55e-430b-af30-7755df3a09c8`
- `/api/messages` is handled by Microsoft 365 Agents SDK for Agent 365. The old `microsoft-teams-apps` reply implementation and its direct Bot Framework app-only token acquisition are removed because Agent 365 agentic applications cannot request those tokens. Replies and reactions use the Agent 365-authenticated connector client supplied by the SDK.
- Hermes replied successfully in a Teams channel mention after switching the bridge to Microsoft 365 Agents SDK and Agent 365 blueprint credentials.
- OpenClaw replied successfully in Teams 1:1 chat and a Teams channel mention after applying the same Microsoft 365 Agents SDK bridge path.
- `scripts.snapshot_system` captures redacted local, Azure, Entra, Graph, and Agent 365 JSON state under `.local\snapshots\<timestamp>` for future clean-state diffs.
- `scripts.demo_ops` provides A6 operator commands for side-by-side health checks, direct `/invoke` smoke validation, active runtime tfvars switching, and Azure Container Apps log triage.
- Agent 365 Teams reactions work through the authenticated connector client: both runtimes add/remove temporary `eyes`, and Hermes validated semantic `heart` from runtime `TEAMS_REACTION` output.
- `scripts.setup_agent365 --publish` creates/repackages `.local\<runtime>\agent365\manifest\manifest.zip`; it does not upload the package. Upload or upgrade in Microsoft 365 admin center remains manual because the current `a365` CLI exposes package creation, not package upload.
- Live API inspection confirmed the Agent User registration and blueprint are healthy, but Agent 365 `agenticUserTemplates` packages are cataloged for Copilot rather than installed as Teams bot apps. The target Team has no RSC grants, so unmentioned channel messages are not delivered to the bridge.
- Typing-indicator source logic and unit coverage exist for 1:1 and small group chats, matching current Agent 365 SDK guidance. Teams does not display typing indicators in channels. Live validation requires the next bridge image deployment.
- Latest post-Teams-routing investigation snapshot: `.local\snapshots\20260709-192121Z`.

Current limitation:

- Full Teams thread history retrieval is not implemented; the bridge sees delivered activities plus bridge-local in-process memory.
- Live verification covers Agent User 1:1 messages and explicit channel mentions. The current path does not deliver every unmentioned channel message; an unmentioned reply is not delivered merely because the agent previously participated in that thread.
- Teams RSC all-message delivery requires a separately installed Teams app with a `bots` capability and is outside the Agent 365-only architecture. Adding RSC fields to `agenticUserTemplates` does not create an app installation or grant.
- The Agent 365 Notifications SDK currently covers email, Office document comments, and Agent User lifecycle events; it does not provide a Teams thread-follow subscription.
- No public Agent 365 roadmap commitment was found for unmentioned Agent User delivery or conversation-follow subscriptions. Review [ADR 0002](docs/adr/0002-teams-event-routing-and-reactions.md) when its documented triggers change.
- Old package inventory rows can remain in Microsoft 365 admin center **All agents** after backing objects are deleted. Microsoft Graph Package Management currently exposes block/unblock/update, not delete; stale Hermes rows are blocked.

## Product direction

Agent 365 is the primary install and user-facing Microsoft 365 path.

Agent 365 AI teammates and classic Teams app/bots share Activity Protocol concepts, but they do not share the same installation and consent lifecycle. An AI teammate is provisioned as an Agent User from an Agent 365 blueprint. It is not an installed Teams app with a `bots` capability, so Teams app RSC grants do not automatically apply.

Removed from the path:

- Teams sideload package generation.
- Azure Bot Service resources.
- Separate Teams manifest preview packages.
- Runtime-specific Teams sideload quickstarts.

The bridge still owns the Agent 365 `/api/messages` behavior.

## Side-by-side app deployments

OpenClaw mode requires:

- `AGENT_RUNTIME=openclaw`
- OpenClaw runtime image and disk image name.
- OpenClaw data volume, for example `openclaw-kind-data`.
- `OPENCLAW_GATEWAY_TOKEN`
- `OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM`
- OpenClaw bridge device approval in the OpenClaw Gateway UI.

Hermes mode requires:

- `AGENT_RUNTIME=hermes`
- Hermes runtime image and disk image name.
- Hermes data volume, for example `hermes-a35-mcp-data`.
- `API_SERVER_KEY`
- Foundry model configuration:
  - `FOUNDRY_OPENAI_BASE_URL`
  - `OPENCLAW_MODEL_ID` for the shared model deployment name
  - `HERMES_MODEL_PROVIDER=azure-foundry`
  - `HERMES_MODEL`
  - `HERMES_INFERENCE_MODEL`

Both runtimes use:

- Private incidents MCP URL and a temporary static key. Replacing this demo credential with Entra-backed workload authorization is the first implementation slice of A7.
- `app=autopilots-on-azure` and `kind=<runtime>` sandbox labels.
- A runtime-specific bridge `/api/messages` endpoint.

## Agent 365 packaging specification

Agent 365 artifacts are runtime-scoped:

```text
.local\openclaw\agent365\
.local\hermes\agent365\
```

Each runtime workspace contains:

```text
a365.config.json
a365.generated.config.json
<runtime>-agent365-identifiers.json
manifest\
```

Runtime defaults:

| Runtime | Agent name | Package branding |
| --- | --- | --- |
| OpenClaw | OpenClaw Autopilot | OpenClaw Autopilot on Azure |
| Hermes | Hermes Autopilot | Hermes Autopilot on Azure |

The generated Agent 365 config must include:

- `autopilotName`
- `agentRuntime`
- `agentName`
- `tenantId`
- `messagingEndpoint`
- AI teammate mode when available.

AI teammate creation should prefer `scripts.provision_agent365_instance provision --register` over portal **Add Instance**. Portal **Add Instance** is fallback only when Graph registration APIs are unavailable.

## Validation baseline

OpenClaw bridge smoke:

```powershell
$bridge = terraform -chdir=terraform\apps output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"openclaw-smoke","message":"List services from private incidents MCP"}'
```

Expected services:

```text
core_banking
card_payments
digital_onboarding
fraud_detection
wealth_portfolio
```

System snapshot baseline:

```powershell
uv run python -m scripts.snapshot_system
```

The known-good Hermes Teams snapshot captured during Agent 365 validation is `.local\snapshots\20260709-121747Z`.

Hermes bridge smoke:

```powershell
$bridge = terraform -chdir=terraform\apps output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"hermes-smoke","message":"Reply with exactly: Hermes bridge OK"}'
```

Hermes private MCP smoke should return the same service list as OpenClaw.

Local validation:

```powershell
uv run python -m unittest tests.test_agent365_setup tests.test_setup_app_tfvars tests.test_deploy_apps_runtime tests.test_demo_ops tests.test_hermes_runtime tests.test_runtime_adapters tests.test_teams_bridge
uv run python -m compileall bridge scripts tests runtimes\openclaw\openclaw_gateway runtimes\hermes -q
Set-Location .\private-incidents-mcp
uv run --with pytest --with pytest-asyncio --with-editable . pytest -q
Set-Location ..
terraform -chdir=terraform\apps validate
```

## Milestone status and roadmap

| Milestone | Status | Outcome |
| --- | --- | --- |
| A4.5 - Agent 365 packages | Complete | Runtime-specific blueprints, endpoints, packages, identifiers, and Teams message validation exist for OpenClaw and Hermes. |
| A5 - Side-by-side deployments | Complete | Both runtimes are live with independent app state, endpoints, identities, and sandbox storage. |
| A6 - Operator polish | Complete | `scripts.demo_ops`, runbooks, diagnostics, snapshots, and recovery commands provide the supported operator path. |
| Teams routing and reaction follow-up | Complete | Reactions work; unmentioned-message and active-thread delivery limits are documented in ADR 0002. This work is not the A7 identity milestone. |
| A7 - MCP and Agent 365 identity model | Next | Replace the private MCP static key, prove Agent User-owned Microsoft 365 actions, and define OBO boundaries. |

### A4.5 - Test Agent 365 packages for both runtimes

Status: Complete.

Goal: prove the Agent 365 path works for OpenClaw and Hermes with the runtime-aware setup script.

Tasks:

- Run `scripts.setup_agent365 --runtime openclaw --run-setup`.
- Publish/capture OpenClaw Agent 365 artifacts.
- Validate OpenClaw Agent 365 messages against the OpenClaw bridge endpoint.
- Run `scripts.setup_agent365 --runtime hermes --run-setup`.
- Publish/capture Hermes Agent 365 artifacts.
- Validate Hermes Agent 365 messages against the Hermes bridge endpoint.
- Record any tenant/Frontier/AI teammate limitations.

Exit criteria:

- OpenClaw and Hermes each have runtime-specific Agent 365 config and metadata artifacts.
- OpenClaw and Hermes each have Agent 365 blueprint setup, endpoint registration, manifest package, and captured non-secret identifiers.
- Both Agent Users respond in Teams 1:1 and explicit channel-mention scenarios; remaining unmentioned-message behavior is a documented platform limitation rather than an installation blocker.

### A5 - Side-by-side app deployments and Agent 365 publishing

Status: Complete.

Goal: run OpenClaw and Hermes at the same time instead of switching one bridge, and publish both as separate Agent 365 autopilots with independent endpoints, identities, configuration, and runtime state.

Tasks:

- Generate separate runtime app tfvars:
  - `.local\openclaw\apps`
  - `.local\hermes\apps`
- Deploy `terraform\apps` once per runtime using dedicated Terraform workspaces:
  - `autopilot-openclaw`
  - `autopilot-hermes`
- Capture per-runtime Terraform outputs:
  - `.local\openclaw\apps\terraform-outputs.json`
  - `.local\hermes\apps\terraform-outputs.json`
- Create separate bridge app names.
- Create separate bridge and private MCP identities/secrets.
- Use separate runtime images, disk image names, and data volumes.
- Use separate Agent 365 package/config directories.
- Generate, publish, and capture separate Agent 365 artifacts for both runtimes.
- Validate that Agent 365 messages reach the correct runtime endpoint.
- Validate runtime-specific identities, secrets, sandbox labels, data volumes, logs, generated identifiers, and diagnostics do not collide.
- Ensure logs, diagnostics, and outputs include runtime kind.
- Keep Terraform `apps` as one-run-per-runtime with workspaces for now; defer `for_each` until both runtimes are stable.
- Record any Frontier, AI teammate, blueprint-only, admin approval, or tenant limitations.

Exit criteria:

- OpenClaw and Hermes bridge endpoints are both live.
- OpenClaw and Hermes packages can be uploaded and activated independently, and their Agent User instances can be provisioned independently.
- Each runtime has its own endpoint, identity metadata, and runtime state.
- A smoke prompt reaches each runtime through Agent 365 and returns the expected response.
- Runtime state and sandbox volumes do not collide.

### A6 - Operator polish

Status: Complete.

Goal: make the repository easy to run as a demo.

Tasks:

- Update README around Agent 365-first operation.
- Add explicit runtime switch commands.
- Add side-by-side deployment guide after A5.
- Add `scripts.demo_ops` as the A6 operator entry point:
  - `status --runtime both --invoke` checks both bridge endpoints and direct runtime smokes.
  - `activate --runtime <runtime>` makes the selected runtime tfvars active before Terraform plan/apply.
  - `logs --runtime <runtime> --app bridge` prints or runs the Azure Container Apps log command.
  - `grant-sandbox-access` grants the current operator user **Container Apps SandboxGroup Data Owner** when the active Azure login differs from the original platform deployer.
  - `reset-sandbox --runtime <runtime>` dry-runs sandbox deletion for stale runtime sandboxes while preserving data volumes; it requires ACA Sandbox data-plane access to `https://dynamicsessions.io`.
- Add troubleshooting for:
  - OpenClaw device approval.
  - Hermes API server health.
  - Private MCP host validation.
  - Agent 365 setup/publish/capture.
  - Azure `azapi` token refresh failures.

Exit criteria:

- A new operator can deploy, switch, package, and validate either runtime without reading historical planning docs.

## Planned track: Hermes digital worker evolution

The A4.5-A6 prerequisites are complete. This track now proceeds from A7 onward. It turns the Hermes runtime from a single demo autopilot into a repeatable digital-worker pattern where one reviewed blueprint can be instantiated many times, adapted privately to a senior person or team, and periodically improved from fleet experience.

Do not use Azure Container Apps Dynamic Sessions for this track. Dynamic Sessions are a different Azure primitive with ephemeral session-pool lifecycle. Digital workers need Azure Container Apps Sandboxes because they provide explicit lifecycle control, suspend/resume, snapshots, and persistent volumes.

Related decisions:

- `docs\adr\0009-hermes-blueprints-on-sandbox-state.md`
- `docs\adr\0010-learning-packets-and-github-consolidation.md`
- `docs\adr\0011-dreaming-scheduler-through-bridge.md`

### Digital worker vocabulary

| Term | Meaning |
| --- | --- |
| Digital worker blueprint | Reviewed, releasable Hermes profile distribution for a role such as junior project manager. Contains `SOUL.md`, base skills, MCP configuration, and optional cron/job templates. |
| Digital worker instance | One running Hermes profile in one ACA Sandbox, assigned to a senior person, team, or workstream. Keeps private state across blueprint upgrades. |
| Private adaptation | Instance-local personal/team memory, preferences, session history, private workspace artifacts, and any sensitive customer/team facts. |
| Transferable learning | Generalized on-the-job procedural/domain learning that may improve future blueprint versions for other instances. |
| Learning packet | Exportable, redacted package containing allowed skill file changes, a why/rationale journal, evidence summaries, source instance metadata, and classification/confidence. |

### State and storage model

Hermes-native state is the default. Do not add a database for Hermes core memory until native files and Hermes SQLite state prove insufficient.

| State category | Storage | Lifecycle |
| --- | --- | --- |
| Active Hermes profile state | ACA Sandbox Data Disk mounted as `HERMES_HOME`, currently `/data/hermes` | Preserved across suspend/resume, sandbox restart, and blueprint upgrade. Single-writer per worker instance. |
| Personal/team memory | Hermes `memories\USER.md`, `memories\MEMORY.md`, optional external Hermes memory provider state, and `state.db` | Private to the assigned person/team. Never exported into shared blueprint consolidation. |
| Session history and search | Hermes `state.db` with built-in SQLite/FTS5 session storage | Private operational state. Used by the instance for recall and by local dreaming, not by shared consolidation unless explicitly summarized and redacted. |
| Blueprint-owned files | Git-backed Hermes profile distribution: `SOUL.md`, `skills\`, `mcp.json`, selected `config.yaml` defaults, and optional cron templates | Replaced or updated from reviewed blueprint versions. Canonical source is Git, not the sandbox disk. |
| Candidate transferable learnings | Local `learning\records.jsonl` plus changed allowed skill files under a designated skills namespace | Exported by central extractor into a learning packet. Reviewed before promotion. |
| Policy documents and business data | External systems exposed through MCP/data agents/RAG, not Hermes core storage | Queried as tools. Governed by the owning system. |

This deliberately avoids a central database in v1. A database may be added later for fleet administration, proposal tracking, dashboards, or audit search, but it must not become the source of truth for blueprint skills.

### Blueprint distribution and upgrade lifecycle

Use Hermes profile distributions as the blueprint packaging mechanism. A blueprint repository should contain:

```text
distribution.yaml
SOUL.md
config.yaml
mcp.json
skills\
cron\
README.md
```

The blueprint repository must not contain:

```text
.env
auth.json
memories\
state.db*
sessions\
logs\
workspace\
plans\
home\
local\
```

Each digital worker instance stores its installed blueprint source and base commit in an instance manifest, for example:

```json
{
  "blueprintName": "junior-project-manager",
  "blueprintSource": "git@github.com:org/junior-project-manager-blueprint.git",
  "blueprintVersion": "v1.0.0",
  "blueprintCommit": "<git-sha>",
  "instanceId": "<worker-instance-id>",
  "assigneeScope": "person-or-team"
}
```

Upgrade flow:

1. Pause or drain the worker instance.
2. Run learning export for allowed paths and `learning\records.jsonl`.
3. Preserve private state on the sandbox Data Disk.
4. Update blueprint-owned files from the new profile distribution version.
5. Restart or resume Hermes with the same `HERMES_HOME`.
6. Validate health and a stateful session smoke.

Private memory and session state must survive every blueprint upgrade. Deleting and reinstalling a profile is not an acceptable upgrade path for assigned workers unless the operator explicitly chooses to discard private state.

### Self-improvement inside one worker

Hermes may continue to self-improve locally:

- Built-in memory writes can update `USER.md` and `MEMORY.md`.
- Hermes can create or patch skills through `skill_manage`.
- Hermes background review and curator can improve or prune agent-created skills.
- A worker can record on-the-job rationale in `learning\records.jsonl`.

For this track, allow local writes freely. Do not require every local memory or skill write to be approved before it takes effect. The safety boundary is promotion, not local adaptation: nothing becomes part of the shared blueprint until the central consolidation flow opens a reviewed GitHub pull request.

Hermes must be instructed to classify learnings before persisting them:

| Classification | Store | Examples |
| --- | --- | --- |
| Private personal/team | `USER.md`, `MEMORY.md`, local session state, private workspace files | Senior person's communication preferences, team structure, customer names, internal stakeholder preferences. |
| Private cache | Local memory/session/workspace only | Reusable facts for this assignment that are too specific for other workers. |
| Candidate transferable procedural learning | Blueprint skill candidate or `learning\records.jsonl` | Better meeting-prep procedure, issue triage checklist, risk escalation heuristic. |
| Candidate domain knowledge | Skill reference or external knowledge proposal | Generalizable project-management concept or policy interpretation, with sources. |
| Do not store | Nowhere durable | Secrets, raw customer data, trivial facts, one-off noise, low-confidence speculation. |

Transferable candidates should be generalized. Prefer variables, conditions, and decision rules over named people, named customers, or one-off anecdotes.

### Dreaming / reflection

Dreaming is the offline reflection stage for one worker instance. It analyzes recent sessions in batches and can produce:

- Private memory consolidation.
- Skill patches or new skill candidates.
- `learning\records.jsonl` entries with rationale and evidence.
- Redaction warnings when a potentially transferable learning contains private details.

Dreaming is distinct from normal turn-time self-improvement:

| Stage | Trigger | Output |
| --- | --- | --- |
| Hot-path learning | During or right after a user turn | Immediate local memory/skill updates. |
| Dreaming | Scheduled or manual offline run | Batch reflection over sessions and evidence; local consolidation and learning packets. |
| Fleet consolidation | Periodic central process across many instances | Reviewed blueprint PR for the next blueprint version. |

For hosted Azure workers, dreaming should be submitted through the bridge to a stateful Hermes endpoint. The bridge must wake or reuse the ACA Sandbox, pass stable session identity, and keep the run isolated from user-facing conversation threads.

### Fleet consolidation and shared blueprint evolution

The consolidation flow is central and GitHub-based:

1. Enumerate worker instances for a blueprint version.
2. For each instance, export allowed paths and `learning\records.jsonl`; do not export private memory, raw sessions, `.env`, auth files, logs, or workspace secrets.
3. Check out the recorded `blueprintCommit`.
4. Compute diffs centrally between base blueprint files and exported current files.
5. Build learning packets with:
   - instance metadata,
   - exact file diffs,
   - why/rationale records,
   - evidence summaries,
   - classification,
   - redaction status,
   - confidence and support count.
6. Run a merger/judge LLM over packets from multiple instances.
7. The judge proposes changes to the blueprint distribution in a Git branch.
8. Open a draft pull request with summary, conflict analysis, outliers, rejected candidates, and evidence references.
9. Require human expert review before merge.
10. Tag or otherwise mark the next blueprint version.
11. Roll workers forward while preserving private state.

Do not make worker instances create Git diffs themselves in v1. They only need to produce rationale and keep local files. Central extraction owns diffing and PR creation.

Conflict handling rules:

- Repeated independent patterns across workers are stronger promotion candidates.
- Outliers are not automatically discarded; the judge should decide whether they represent a valuable edge case, a local-only condition, or a mistake.
- Natural contextual conflicts can remain conditional in a skill: "If X, do A; if Y, do B."
- Conflicts caused by low evidence, obvious mistakes, or private-only context should be rejected or kept local.

### GitHub governance model

GitHub is the v1 review and release surface:

- Blueprint repositories contain the releasable profile distribution.
- Consolidation opens branches and pull requests.
- `CODEOWNERS`, branch rules/rulesets, and required reviews protect blueprint files.
- GitHub Actions validates packaging, redaction checks, and structural skill rules.
- GitHub Copilot cloud agent may assist with PR implementation or review, but Copilot review does not replace required human approval.

An admin app may be added later, but it should write GitHub issues/branches/PRs rather than becoming a parallel source of truth.

### Scheduling options for dreaming

ACA Sandboxes do not provide a documented native timer trigger. They provide stateful lifecycle, autosuspend, snapshots, volumes, ports, and data-plane management. Azure scheduling should live outside the sandbox.

Supported options:

| Option | Use when | Notes |
| --- | --- | --- |
| Bridge-owned cron/timer | v1 demo or when the bridge has minimum replicas and can stay alive | Simple. The bridge calls `ensure_agent_sandbox` and submits a dream run. |
| Azure Container Apps scheduled Job | Production-friendly scale-to-zero scheduler | Put in Bicep where possible. The job calls the bridge; the bridge wakes the sandbox. |
| Azure Container Apps event-driven Job | Later event fan-out from Service Bus/Queue/Event Hub | Useful for queue-based learning exports, not required for simple periodic dreaming. |
| Service Connector | Service wiring only | Helps configure app-to-service connectivity; it is not the scheduling primitive. |
| Hermes gateway cron | Local/pure-Hermes deployments | Less suitable as primary hosted scheduler because it depends on Hermes gateway ticking and fresh cron sessions have different behavior than user sessions. |

Initial implementation should support bridge-owned cron for simplicity, with a clear path to an ACA scheduled Job.

### Roadmap from A7

#### A7 - MCP and Agent 365 identity model

Goal: prove how sandbox-hosted OpenClaw and Hermes workers access private and Microsoft 365 tools using the correct Agent 365 identities.

Status: Complete for Agent Identity, Agent User, private/custom MCP, and Work IQ Mail. Human OBO remains intentionally separate and deferred pending a user-consent route.

Tasks:

- Define supported identity modes:
  - Agent Identity or runtime workload identity for unattended private MCP and service-to-service access.
  - Agent User identity for resources owned by the digital worker, such as its mailbox, calendar, OneDrive, and documents.
  - User-delegated / OBO identity only for explicit user-owned-resource requests.
- Add explicit auth-boundary metadata to the bridge/runtime request contract, aligned with ADR 0004: selected auth mode, Agent User/instance identifiers when known, invoking human identity, and public/private conversation boundary.
- Replace the private demo MCP static-key path with Entra-backed Agent Identity application authorization. Do not use Agent User delegated identity for unattended service-to-service calls.
- Validate that both OpenClaw and Hermes call private and public custom MCP servers through the same Sandbox-local identity adapter.
- Build one Agent User Microsoft 365 scenario through Agent 365 Work IQ Mail.
- Evaluate Microsoft 365 / Work IQ MCP tools and adopt Mail as the first live scenario.
- Document which tools can be used directly from bridge / sandbox runtimes and which require Microsoft 365 Agents SDK, Agent 365 notification handlers, or Foundry Hosted Agent execution.
- Evaluate OBO for private 1:1 user requests, including consent, token acquisition, prompt boundaries, and why it is not the default for autonomous group/chat work.
- Define least-privilege blueprint, Agent Identity app-role, Agent User delegated grant, and Agent 365 Tooling setup.

Exit criteria:

- Private MCP authorization works with Agent Identity `Incidents.Read.All` tokens federated from the Sandbox Group managed identity.
- Public custom MCP authorization works with Agent Identity `Shipments.Read.All`; its public endpoint is registered as Agent 365 BYO MCP.
- Agent User email works end to end through Work IQ Mail for OpenClaw and Hermes.
- OBO is deferred to a per-turn user consent implementation; it is not used for autonomous or shared-conversation work.
- OpenClaw and Hermes use the same Sandbox-local identity and tooling contract.

#### A8 - Blueprint distribution and local state

Goal: install and update digital-worker blueprints from Git while preserving instance-local state, with Hermes as the first full implementation and OpenClaw evaluated against the same lifecycle contract.

Status: In progress. The commit-pinned Git lifecycle, instance manifest, persistent named profile, sandbox replacement labels, state-preservation unit proof, and OpenClaw exception are implemented. A live ACA Sandbox v1-to-v2 upgrade remains before completion.

Tasks:

- Define `junior-project-manager` blueprint distribution layout.
- Add instance manifest with blueprint source, version, and commit.
- Update Hermes sandbox startup so it does not overwrite distribution-owned files or private state unexpectedly.
- Ensure `HERMES_HOME` remains on the sandbox Data Disk.
- Add stateful Hermes invocation through `/api/sessions/{id}/chat`, `/v1/responses`, or `/v1/runs`.
- Preserve and pass stable `X-Hermes-Session-Key`.
- Evaluate what the equivalent OpenClaw profile/package/state boundary should be, or document why OpenClaw stays on a lighter runtime-package model.

Exit criteria:

- A Hermes worker instance can install v1, chat, write local memory/skills, update to v2, and keep private memory/session state.
- OpenClaw has either an equivalent tested packaging/state story or an explicit documented exception.

#### A9 - Local learning and dreaming

Goal: let one worker adapt locally and produce learning packets without central consolidation.

Tasks:

- Add digital-worker instructions for learning classification.
- Add `learning\records.jsonl` schema.
- Add dream-run prompt/skill.
- Add bridge endpoint or internal operation to submit a dream run.
- Add redaction checks for learning packets.

Exit criteria:

- A worker can produce private memory updates and candidate transferable learning records from recent sessions.
- No private memory is exported by default.

#### A10 - Fleet consolidation to GitHub PR

Goal: promote transferable learnings from multiple workers into a reviewed blueprint version.

Tasks:

- Export allowed files and learning records from multiple workers.
- Compute diffs centrally against each worker's recorded blueprint commit.
- Build learning packets.
- Run merger/judge LLM.
- Open a draft PR against the blueprint repository.
- Validate redaction, skill structure, and packaging.

Exit criteria:

- Human reviewers can approve a blueprint v2 PR with traceable evidence and rejected-candidate notes.

#### A11 - Scheduled dreaming and fleet automation

Goal: automate recurring dream and consolidation cycles.

Tasks:

- Implement bridge-owned cron or ACA scheduled Job for dreaming.
- Add operator controls for cadence, per-worker enablement, and backoff.
- Add optional event-driven queue trigger for large fleets.
- Add status reporting for last dream, last export, and last blueprint version.

Exit criteria:

- Workers can be periodically reflected and fleet learnings can be proposed without manual sandbox access.

#### A12 - Agent 365 workload notifications

Goal: add optional non-Teams Microsoft 365 notification inputs while keeping Teams chat on the existing `/api/messages` activity path.

Status: Planned after the A7 identity and permission model. The notification SDK and payload types have been researched, but no Email or Office comment notification route is implemented yet.

Tasks:

- Validate Agent 365 notification delivery for Email and Word first; Excel and PowerPoint can follow.
- Treat notifications as new bridge sources such as `a365_email`, `a365_word_comment`, `a365_excel_comment`, and `a365_powerpoint_comment`.
- Map notification payloads into `AgentRequest` with stable session keys based on message, document, thread, or comment identifiers.
- Decide response behavior per workload:
  - Email responses should use mail / Microsoft 365 tooling, not Teams chat.
  - Word, Excel, and PowerPoint comment responses should reply to the document comment thread when tooling supports it.
  - Lifecycle notifications should initialize or clean up worker state only when useful.
- Ensure notification handling does not bypass existing runtime selection, identity boundaries, redaction rules, or private-state rules.
- Add minimal end-to-end smoke tests with synthetic notification payloads before using live Microsoft 365 events.

Exit criteria:

- Email and Word notifications can reach the bridge and be routed to the selected runtime.
- The runtime can produce an appropriate action plan or response for the notification.
- Any required Microsoft MCP/tool permissions are documented and least-privilege.

## Deferred

Do not implement until the Agent 365 and side-by-side paths are stable:

- Foundry Hosted Agents as a possible thin adapter only, not as the default OpenClaw or Hermes runtime host.
- Deeper user identity/profile isolation.
- Hermes native Teams mode.
- Hermes dashboard exposure.
- Full multi-user memory/profile policy outside the Hermes-first digital-worker track.
