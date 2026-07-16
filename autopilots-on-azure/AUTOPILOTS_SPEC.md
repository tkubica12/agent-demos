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
- Both runtimes use Foundry deployment `gpt-5-6-terra` (`gpt-5.6-terra`, version `2026-07-09`) with Global Standard capacity 100 and workload-identity authentication.
- The live regional topology is Foundry plus ACA Sandboxes in Sweden Central and Container Apps plus ACR in North Europe. Globally peered VNets and shared private DNS preserve private Sandbox-to-MCP connectivity.
- OpenClaw and Hermes both passed direct `/invoke` validation against Terra from the Sweden Central Sandbox Group on 2026-07-15.
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

The A4.5-A6 prerequisites are complete. This track turns Hermes from a single demo autopilot into a repeatable digital-worker pattern: one reviewed Role Blueprint produces a Role Release, many named Workers adopt that release, each Worker adapts locally, and Collective Learning Review promotes selected improvements into the next Role Release.

Do not use Azure Container Apps Dynamic Sessions for this track. Dynamic Sessions are a different Azure primitive with ephemeral session-pool lifecycle. Digital workers need Azure Container Apps Sandboxes because they provide explicit lifecycle control, suspend/resume, snapshots, and persistent volumes.

Related decisions:

- `docs\adr\0009-hermes-blueprints-on-sandbox-state.md`
- `docs\adr\0010-candidate-improvements-and-collective-learning-review.md`
- `docs\adr\0011-dreaming-scheduler-through-bridge.md`

### Digital worker vocabulary

Use these terms consistently in user experience, documentation, APIs, manifests, scripts, and new code. Lower-level Azure resources may still use `instance` where the platform requires it, but the product concept is a **Worker**.

| Term | Meaning |
| --- | --- |
| **Role Blueprint** | Reviewed central definition of a job such as Junior Project Manager or Customer Support Specialist. Contains role instructions, Role Skills, tool configuration, and release metadata. |
| **Role Release** | A numbered, immutable release of a Role Blueprint. This is the public lifecycle term; do not introduce a second user-facing “generation” number. |
| **Worker** | One named digital teammate created from a Role Release, with its own Teams identity, manager or team assignment, memory, Work History, and local adaptations. |
| **Personal Memory** | Small, private, always-loaded facts in `USER.md` and `MEMORY.md`: identity, preferences, communication style, and critical durable context. |
| **Private Playbook** | A Hermes-native private skill, optionally with reference files, containing rich assignment-, customer-, account-, manager-, or team-specific knowledge and procedures. Never enters Collective Learning Review. |
| **Work History** | Private Hermes SQLite sessions, messages, tool calls, and search indexes. Used for recall and Dreaming; raw history is never exported. |
| **Role Skill** | Skill inherited from the Role Release. A Worker may improve it locally; the local diff becomes a Candidate Improvement. |
| **Candidate Improvement** | A locally patched Role Skill or newly authored reusable skill that may benefit other Workers. Active locally in a fresh session and eligible for Collective Learning Review. |
| **Dreaming** | Offline reflection over several sessions, outcomes, corrections, memories, and skills. It may update private state or create Candidate Improvements. |
| **Collective Learning Review** | Multi-Worker process that compares Candidate Improvements, evidence, and provenance, then proposes reviewed Role Blueprint changes. Prefer this over “fleet consolidation” or “swarm learning” in product language. |
| **Promotion** | Human-reviewed acceptance of a Candidate Improvement into the next Role Release. |
| **Worker Refresh** | A Worker adopting a new Role Release while preserving Personal Memory, Private Playbooks, and Work History. |
| **Learning Packet** | Fail-closed export containing allowed skill artifacts or diffs plus provenance, evidence summaries, source Worker metadata, confidence, and privacy checks. |

### State and storage model

Hermes-native state is the default. Do not add a database for Hermes core memory until native files and Hermes SQLite state prove insufficient.

| State category | Storage | Lifecycle |
| --- | --- | --- |
| Active Worker profile | ACA Sandbox Data Disk mounted as `HERMES_HOME`, currently `/data/hermes` | Preserved across suspend/resume, Sandbox restart, and Worker Refresh. Single-writer per Worker. |
| Personal Memory | Hermes `memories\USER.md`, `memories\MEMORY.md`, and optional external memory-provider state | Private to the Worker and assigned person/team. Always preserved and never exported. |
| Private Playbooks | Hermes-native skills under the reserved private namespace with optional `references\` files | Private progressive-disclosure knowledge. Preserved during Worker Refresh and never exported. |
| Work History | Hermes `state.db` with built-in SQLite/FTS5 session storage | Private operational history used for recall and Dreaming. Raw content is never exported. |
| Role Skills | Git-backed Role Blueprint skill paths | Inherited from the Role Release, locally patchable, and replaced during Worker Refresh only after eligible diffs are exported. |
| Candidate Improvements | New reusable skills in the reserved candidate namespace plus local Role Skill diffs | Active locally in a fresh session, exported with provenance, and reviewed before Promotion. Scoped to the current Role Release. |
| Learning provenance | `learning\records.jsonl` schema v2 | Explains why Candidate Improvements were created, with evidence and artifact hashes. Exported with the matching artifact or diff. |
| Policy documents and business data | External systems exposed through MCP/data agents/RAG, not Hermes core storage | Queried as tools. Governed by the owning system. |

This deliberately avoids a central database in v1. A database may be added later for Worker administration, proposal tracking, dashboards, or audit search, but Git remains the source of truth for Role Blueprints and Role Releases.

### Role Blueprint distribution and Worker Refresh

Use Hermes profile distributions as the Role Blueprint packaging mechanism. A Role Blueprint repository should contain:

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

Each Worker stores its installed Role Blueprint source and Role Release commit in a Worker manifest:

```json
{
  "roleBlueprint": "junior-project-manager",
  "roleBlueprintSource": "git@github.com:org/junior-project-manager-blueprint.git",
  "roleRelease": "3.0.0",
  "roleReleaseCommit": "<git-sha>",
  "workerId": "<worker-id>",
  "assignmentScope": "person-or-team"
}
```

Worker Refresh flow:

1. Pause or drain the Worker.
2. Export allowed Candidate Improvements and their provenance.
3. Persist an approved export receipt.
4. Preserve private state on the sandbox Data Disk.
5. Replace Role Skills with the new immutable Role Release.
6. Archive previous-release Candidate Improvements and provenance.
7. Preserve Personal Memory, Private Playbooks, Work History, and other explicitly private Worker state.
8. Restart or resume Hermes with the same `HERMES_HOME`.
9. Start a fresh session and validate the adopted Role Release.

Personal Memory, Private Playbooks, and Work History must survive every Worker Refresh. Deleting and reinstalling a profile is not an acceptable refresh path unless the operator explicitly chooses to discard private state.

### Self-improvement inside one worker

Hermes may continue to self-improve locally:

- Built-in memory writes can update `USER.md` and `MEMORY.md`.
- Hermes can create or patch skills through `skill_manage`.
- Hermes background review can create or improve agent-created skills; curator manages autonomously created skills, while foreground user-directed `/learn` skills are curator-exempt in Hermes 0.18.
- A Worker can patch a Role Skill or create a Candidate Improvement.
- Every Candidate Improvement has a linked provenance record in `learning\records.jsonl`.

Local writes take effect for the Worker without central approval. The safety boundary is Promotion: nothing becomes part of a Role Release until Collective Learning Review opens a pull request and a human approves it. Candidate Improvements are scoped to one Role Release; Personal Memory and Private Playbooks survive Worker Refresh.

A9 used a generated aggregate `hot-learning` skill as an interim bridge. A10 removes that bridge and embraces native Hermes skill evolution: inherited Role Skill patches and new Candidate Improvements are the actual local behavior, while `records.jsonl` is provenance. Private Playbooks remain excluded. This is a policy and lifecycle boundary, not a filesystem ACL; enable `skills.write_approval` when all native skill edits must be staged.

Hermes must be instructed to classify learnings before persisting them:

| Classification | Store | Examples |
| --- | --- | --- |
| Personal Memory | `USER.md`, `MEMORY.md` | Small identity, communication, environment, and critical durable facts injected at a fresh session start. |
| Private Playbook | Reserved private skill namespace plus optional references | Customer-, account-, project-, manager-, or team-specific facts and procedures. |
| Role Skill improvement | Local patch to an inherited Role Skill plus provenance | Better meeting preparation, issue triage, or risk escalation behavior. |
| New Candidate Improvement | Reserved candidate skill namespace plus provenance | New reusable procedure or generalizable domain capability. |
| Do not store | Nowhere durable | Secrets, raw customer data, trivial facts, one-off noise, low-confidence speculation. |

Candidate Improvements must be generalized. Prefer variables, conditions, and decision rules over named people, named customers, or one-off anecdotes.

Foreground learning does not wait for Dreaming. When a normal turn establishes a high-confidence reusable rule, Hermes can patch a Role Skill or create a Candidate Improvement and attach provenance. A fresh session guarantees the updated skill tree is used. `records.jsonl` remains a provenance and Collective Learning Review source rather than ordinary prompt context.

### Dreaming / reflection

Dreaming is offline reflection for one Worker. It analyzes several sessions, outcomes, corrections, memories, and skills. It can produce:

- Personal Memory consolidation.
- Private Playbook creation or improvement.
- Role Skill patches.
- New Candidate Improvements.
- Linked provenance with rationale and evidence.
- Redaction warnings when a potentially transferable learning contains private details.

Dreaming is distinct from normal turn-time self-improvement:

| Stage | Trigger | Output |
| --- | --- | --- |
| Foreground learning | During or immediately after a user turn | Personal Memory, Private Playbook, Role Skill patch, or new Candidate Improvement. |
| Dreaming | Scheduled or manual offline run | Wider reflection over Work History and existing skills; the same artifact types with broader evidence. |
| Collective Learning Review | Periodic central process across Workers of one Role Release | Reviewed pull request proposing the next Role Release. |

For hosted Azure workers, dreaming should be submitted through the bridge to a stateful Hermes endpoint. The bridge must wake or reuse the ACA Sandbox, pass stable session identity, and keep the run isolated from user-facing conversation threads.

### Collective Learning Review and Role Blueprint evolution

Collective Learning Review is central and GitHub-based:

1. Enumerate Workers for one Role Release.
2. Export only Candidate Improvements, local Role Skill diffs, and linked provenance; never export Personal Memory, Private Playbooks, raw Work History, credentials, logs, or workspace secrets.
3. Check out the recorded `roleReleaseCommit`.
4. Compute exact diffs against the Role Release.
5. Build learning packets with:
   - source Worker metadata,
   - exact file diffs,
   - why/rationale records,
   - evidence summaries,
   - classification,
   - redaction status,
   - confidence and support count.
6. Run a merger/judge over packets from multiple Workers.
7. The judge proposes changes to the Role Blueprint in a Git branch.
8. Open a draft pull request with summary, conflict analysis, outliers, rejected candidates, and evidence references.
9. Require human expert review before Promotion.
10. Publish the next Role Release.
11. Refresh Workers while preserving private state.

Workers do not create Git diffs themselves. They modify local skills and record provenance. Central extraction owns diffing and pull-request creation.

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

Status: Complete. The commit-pinned Git lifecycle, instance manifest, persistent named profile, sandbox replacement labels, state-preservation proof, and OpenClaw exception are implemented and live-validated.

Live evidence from 2026-07-13:

- Installed `junior-project-manager` v1.0.0 from commit `ecc07fad92122d6ae6d4e44bd145c1814a746071`.
- Chatted through `/api/sessions/{id}/chat` with the stable bridge session headers.
- Wrote private memory, session, instance-local skill, and `local\` markers while Hermes maintained its native `state.db`.
- Upgraded to v2.0.0 at commit `50342bd359a3f0fce9669a43b1d6eeb4fa690900`.
- Replaced sandbox `c5aed5f2-eb12-4330-bb86-c582723f273d` with `827fdced-a813-4d3f-9db6-89bcd5a9c001` while retaining the `hermes-data` Data Disk.
- Verified the v2 `distribution.yaml`, `SOUL.md`, and blueprint skill plus every v1 private marker and `state.db`.
- Re-verified Agent Identity access to the private incidents MCP after the upgrade.

Tasks:

- Define `junior-project-manager` blueprint distribution layout.
- Add instance manifest with blueprint source, version, and commit.
- Update Hermes sandbox startup so it does not overwrite distribution-owned files or private state unexpectedly.
- Ensure `HERMES_HOME` remains on the sandbox Data Disk.
- Add stateful Hermes invocation through `/api/sessions/{id}/chat`, `/v1/responses`, or `/v1/runs`.
- Preserve and pass stable `X-Hermes-Session-Key`.
- Evaluate what the equivalent OpenClaw profile/package/state boundary should be, or document why OpenClaw stays on a lighter runtime-package model.

Exit criteria:

- A Hermes Worker can adopt Role Release v1, chat, write local memory/skills, refresh to v2, and keep Personal Memory and Work History.
- OpenClaw has either an equivalent tested packaging/state story or an explicit documented exception.

#### A9 - Local learning and dreaming

Goal: let one worker adapt locally and produce learning packets without central consolidation.

Status: Complete and being superseded by A10. Role Release 2.3.0 proved release-scoped aggregate hot learning, secured Dreaming, runtime-local JSONL validation, deterministic redaction, private-path exclusions, and operator control.

Live evidence from 2026-07-15:

- Installed `junior-project-manager` v2.2.0 from commit `c785192de8bca5eceb243dd0f02f0f1886fdec6a` in a replacement Sandbox while retaining `hermes-data`.
- Submitted manual reflection through `POST /internal/dream` with the local operator key and stable `dream:hermes` session.
- Produced three schema v1.0 `transferable_procedural` records for commitment ownership, outcome-focused status, and risk escalation.
- Passed deterministic redaction with no rejected stored records.
- Consolidated a compact-table status preference into private instance-local memory and produced no transferable record for it.
- Returned a packet that excluded memory, raw sessions, `.env`, auth, logs, workspace, and `state.db*`.
- Avoided Hermes shell-approval dependence by submitting bounded candidate JSON from the bridge to a trusted runtime-local validation endpoint.
- Upgraded to v2.3.0 at commit `01e048d2991113aa327a827a62f78286b17206a5` without changing learning generation 1 or losing the three existing records.
- Captured a rollback-checkpoint rule during an ordinary user turn as record `lr-20260715T202440Z-d0ef1ffb`.
- Recalled the accepted rule in a new session immediately: the worker required both the previous version identifier and exact restore command without waiting for dreaming.
- Ran a subsequent dream that recognized the rule as already captured and produced zero duplicate candidates.
- Corrected `scripts.demo_ops reset-sandbox` to use the Sweden Central Sandbox data-plane endpoint rather than the North Europe application region.

Tasks:

- Add digital-worker instructions for learning classification.
- Add `learning\records.jsonl` schema.
- Add dream-run prompt/skill.
- Add bridge endpoint or internal operation to submit a dream run.
- Add redaction checks for learning packets.

Exit criteria:

- A worker can produce private memory updates and candidate transferable learning records from recent sessions.
- No private memory is exported by default.

#### A10 - Native skill evolution and Collective Learning Review

Goal: embrace Hermes-native skill evolution locally, then promote selected Candidate Improvements from multiple Workers into a reviewed Role Release.

Tasks:

- Remove the interim `local\private-cache.md` and aggregate `skills\hot-learning` design.
- Reserve Role Skill, Private Playbook, Candidate Improvement, and runtime namespaces.
- Classify Hermes-native skill writes as Private Playbook, Role Skill improvement, Candidate Improvement, or runtime-owned.
- Capture provenance for eligible `skill_manage`, `/learn`, background-review, and dream skill changes: artifact path, action, base commit, before/after hashes, rationale, redacted evidence, confidence, and source stage.
- Allow Role Skills to be patched locally and new Candidate Improvements to be created for use in the next fresh session.
- Preserve Private Playbooks while marking Candidate Improvements as scoped to one Role Release.
- Export only Candidate Improvement artifacts, Role Skill diffs, and linked provenance from multiple Workers.
- Compute exact diffs centrally against each Worker's recorded Role Release commit.
- Preserve or export Role Skill diffs before any distribution-owned file replacement; Role Release is the replacement boundary.
- Build learning packets containing both exact diffs and provenance.
- Fail closed on an explicit transferable-artifact allowlist; run source-aware DLP/redaction over records and skill content, and require human export approval before merger-model processing.
- Persist a durable export receipt before Worker Refresh.
- Reserve separate Role Skill, Private Playbook, Candidate Improvement, and runtime namespaces to prevent path collisions.
- Run merger/judge LLM.
- Open a draft PR against the blueprint repository.
- Validate redaction, skill structure, and packaging.
- Archive or retire release-local Candidate Improvements after the reviewed next Role Release is installed.

Exit criteria:

- Human reviewers can approve a next Role Release pull request with traceable skill diffs, rationale, evidence, and rejected-candidate notes.
- Personal Memory, Private Playbooks, and Work History are absent from every Learning Packet.

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
