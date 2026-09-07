# Autopilots on Azure

Run durable digital Workers in Azure Container Apps Sandboxes and expose them through Microsoft Agent 365.

Hermes is the primary Worker runtime. It demonstrates Role Blueprints, Personal Memory, Private Playbooks, native skill evolution, Dreaming, and Collective Learning Review. The existing OpenClaw adapter is not being expanded.

**Modernization status, 2026-09-06 22:33 CEST:** both model paths, application same-ID/Data-Disk resume, Hermes MCP, schedule delivery, and no-change Dream passed. All 49 reviewed native parents resolve; 105 audited spans contain metadata only. Two controlled requests isolate parent rewriting to the Sandbox-origin egress path: TraceId survives, but platform intermediate parents are absent from AppInsights. This is a native-platform waterfall limitation, not a complete parent tree. Terminal `Failed` service replacement is corrected; unchanged `Stopped`/`Suspended` services retain identity. Four response-only cases scored **3/4 versus 4/4**, not general learning proof. Teams shows public `@`, not Hermes `/`; targeted inbound and automatic eight-hour idle behavior remain unverified.

## Current system

**ACR/MCP lifecycle evidence:** public MCP endpoints return unauthenticated `401`; private external connections reset, not HTTP `403`. Real runtime-to-private-MCP calls succeeded; independent network-route evidence was not collected. Repeated live MCP deployment reused Sandbox IDs without registry login, verified with a fail-if-called guard. All ten workload `AcrPull` assignments were removed; ACR admin is `false`. SDK b4 native MI conversion still fails; the accepted short-lived Entra credential path is deployment-time conversion only.

The supported operator path is [modernization of existing Workers](DEPLOYMENT.md), preserving their Agent365 blueprint, AgentIdentity, AgentUser, and local state. First-time Agent365 bootstrap has an unresolved endpoint/identity dependency cycle and is not a verified deployment path.

```text
Teams / Agent 365 / direct invoke
                |
                v
      per-Worker gateway Sandbox
      Agent 365 SDK authentication
      auto-suspend disabled
                |
                v
         ACA Sandbox + Data Disk
        /                       \
 existing adapter        Hermes 0.19.0 Worker
                                  |
                   loopback identity adapter
                    /           |          \
          private incidents   shipments   Work IQ
```

| Capability | Current implementation |
| --- | --- |
| Microsoft 365 installation | Agent 365 only |
| Hosting | Separate Sandbox Groups and user-assigned managed identities per Worker and service role |
| Runtime hosting | OnDemand ACA Sandbox with persistent Data Disk |
| Gateway hosting | Sandbox with auto-suspend disabled for post-ACK work and Service Bus receive |
| Models | Sweden Central Foundry `gpt-5-6-terra` |
| Model authentication | Native Hermes `azure-foundry` Entra token callback; no custom model proxy |
| Private networking | Linked Express ACA managed environment, Private Endpoint and private DNS; no classic MCP Container App |
| Autonomous authorization | Agent Identity federation from Sandbox managed identity |
| Worker-owned Microsoft 365 data | Agent User |
| Private tools | VNet-only MCP plus Entra app roles |
| Public tools | Entra-protected HTTPS MCP |
| Worker learning | Personal Memory, Private Playbooks, Role Skills, Candidate Improvements |
| Shared learning | Ed25519-attested Learning Packets and reviewed GitHub Promotion |
| Dependencies | Hermes 0.19.0, frozen Python and npm locks, weekly Dependabot |
| Observability | Foundry external registration and live metadata-only model/tool spans verified; TraceId correlation survives a Sandbox-egress platform-parent gap |

Workers such as `hermes` and `hermes2` keep separate `runtime`, `gateway`, `private-mcp`, `public-mcp`, and `generated-apps` Sandbox Groups. Their Agent 365 platform blueprints and identities remain isolated under [ADR 0014](docs/adr/0014-per-worker-agent365-blueprints-and-bridges.md). Foundry provides models and external-agent visibility, not Hosted Agent compute.

## Quick check

```powershell
Set-Location .\autopilots-on-azure
uv sync --frozen --index-url https://packagefeedproxy.microsoft.io/pypi/simple

uv run python -m scripts.demo_ops status --runtime openclaw
uv run python -m scripts.demo_ops status --runtime hermes --state-name hermes
uv run python -m scripts.demo_ops status --runtime hermes --state-name hermes2
```

Run a fresh direct smoke:

```powershell
uv run python -m scripts.demo_ops smoke `
  --runtime hermes `
  --state-name hermes2 `
  --message "Reply exactly: hermes2 ready"
```

These operator commands require deployed application outputs. See [DEPLOYMENT.md](DEPLOYMENT.md) for the supported corporate package feed and the remaining deployment gates.

## Documentation

| Document | Use it for |
| --- | --- |
| [SPEC.md](SPEC.md) | Product requirements, terminology, architecture, security, and lifecycle contracts |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Existing-Worker updates, validation, cleanup, and the unresolved first-time bootstrap boundary |
| [DEMO.md](DEMO.md) | Teams demonstrations, memory/skills, Dreaming, and Collective Learning Review |
| [Hermes on Azure visual overview](docs/hermes-on-azure-overview.html) | Offline presentation-ready HTML explanation of architecture, identity, memory, learning, Promotion, and Worker Refresh |
| [Hermes learning source deep dive](docs/hermes-learning-deep-dive.html) | Interactive, source-cited explanation of memory, skills, Dreaming, learning governance, packets, and Worker Refresh |
| [Hermes architecture deep dive](docs/hermes-architecture-deep-dive.html) | Sandbox roles, private ingress, identity, scheduling, and explicit failure boundaries |
| [PLAN.md](PLAN.md) | Delivery status, history, next work, and deferred items |
| [`docs\adr`](docs/adr) | Decisions and reconsideration triggers |
| [`docs\runbooks`](docs/runbooks) | Detailed identity or preview-service procedures |

## Repository map

```text
bridge\                    Agent 365 and direct-invoke bridge
bridge\runtime\            OpenClaw and Hermes adapters
runtimes\openclaw\         OpenClaw Sandbox image
runtimes\hermes\           Hermes Sandbox image and learning governance
blueprints\                Git-backed Role Blueprints
autopilots_identity\       Agent Identity and Agent User token exchange
private-incidents-mcp\     VNet-only Entra-protected MCP
public-shipments-mcp\      Public Entra-protected MCP
agent365\                  Agent 365 Tooling manifest
terraform\platform\        Shared Azure platform
terraform\apps\            Per-Worker application workspaces
scripts\                   Repeatable operator automation
tests\                     Runtime, identity, learning, and deployment tests
```

## Important boundaries

- A **Role Blueprint** is the shared Git definition of a job.
- An **Agent 365 platform blueprint** is the Microsoft 365 endpoint and permission envelope for a deployed Worker.
- Personal Memory, Private Playbooks, and Work History never enter Collective Learning Review.
- Candidate Improvements remain local until a human-reviewed Promotion.
- `/learn <instruction>` enters one explicit, transactional Hermes learning turn; ordinary prose is never keyword-scanned into a hidden second model call.
- Teams UI recheck found Hermes in public group `@` mentions, not `/` discovery. Targeted inbound remains unverified for this deployment—not universally unsupported or a proven UI-only bug. The current `agenticUserTemplates`-only package has no `bots[]` opt-in; no companion bot or private-to-public fallback is used.
- Direct `hermes --cli` skill writes receive provenance on the next bridged turn or Dreaming run.
- Governed changes are `SKILL.md` only. Provenance 3.0 and packets 2.0 retain cumulative evidence and signed agent-proposed scenarios; those scenarios do not replace independent regression or holdout tests.
- Shared group transcripts remain shared. A stable per-user memory key is not proof of full private memory isolation; [ADR 0017](docs/adr/0017-messaging-session-lifecycle.md) records the open boundary.
- Scheduler checkpoints avoid blind replay of ambiguous Dreaming. Teams send-to-receipt crashes can still duplicate delivery; this is not exactly-once messaging.
- Old Sandbox conversion used the ACR admin password; classic ACA pulls used MI. SDK b4 native MI conversion still fails. The approved transient Entra token is **deployment-only** and prepared all four image roles for both existing Workers. Prepare runtime, gateway, private-MCP, and public-MCP disk images before any workload creation. The gateway receives `AGENT_RUNTIME_DISK_IMAGE_ID`; workloads receive no ACR roles or credentials. No admin password, renewal/cache service, or runtime/gateway conversion. See [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md).
- Trace correlation currently requires an explicit native session. `/v1/responses` creates its own session and is marked `autopilots.trace.correlation=missing`; interrupted context leases require Worker restart before reusing that session.
- `npm audit` reports two inherited high-severity `image-size` findings with no published fix at the September 6 review; the current locked tree is not claimed vulnerability-free.
