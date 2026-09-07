# Autopilots on Azure plan

## Purpose

Track current delivery evidence and remaining work. Contracts belong in [SPEC.md](SPEC.md), operations in [DEPLOYMENT.md](DEPLOYMENT.md), and classroom scenarios in [DEMO.md](DEMO.md).

## Current snapshot — 2026-09-06 22:33 CEST

The approved direction is an all-Sandbox ACA deployment, not a Foundry Hosted Agent migration. Hermes remains the Worker runtime; Foundry provides the model and external-agent observability.

The parent's latest test run passed **351 tests**. Earlier counts below describe separately scoped runs, not additional tests to sum. The parent also live-verified the direct-owner preflight's nonowner block.

| Workstream | Status | Evidence and remaining gate |
| --- | --- | --- |
| Hermes 0.19.0 | Both Workers deployed; model invocations live-verified | Both gateway/runtime paths returned HTTP `200` / `Hermes bridge OK` through native Entra model authentication without a model token proxy. All four images are built. Broader tool/Teams/trace parity remains separate. |
| Reproducible dependencies | Implemented | Frozen Python `uv.lock`, npm lock, and weekly Dependabot. Public PyPI is blocked; the actual source is `https://packagefeedproxy.microsoft.io/pypi/simple`. |
| Shared Azure platform | Live-verified September 5 | Foundry `gpt-5-6-terra` and project, network foundation, keyless Application Insights/Log Analytics and project connection deployed. |
| Per-Worker application infrastructure | Live-verified | Both `hermes` and `hermes2` applies succeeded with distinct Groups/user-assigned identities for runtime, gateway, private MCP, public MCP, and generated apps. |
| Application workload deployment | Both gateway/runtime deployments live-verified | Four MCP services remain healthy. Both Workers served successful real model invocations. First Hermes gateway is `09e77569-bba7-4b05-add6-0c8e6772f557`, runtime `66c77cbf-9654-47a8-84cb-1fa6d69c5be1`. |
| Image-auth alternative | Accepted; all four image conversions and both Workers deployed | SDK b4 with transient deployer Entra tokens prepared all four image types, followed by both existing Workers' workload deployments. The first private-MCP `Ready` image was `deb1880f-c394-406e-9d02-760840e1cfd7`, not the final extent of the proof. Gateway uses `AGENT_RUNTIME_DISK_IMAGE_ID`; no workload ACR roles/credentials, admin password, or renewal/cache/service. Direct CLI requires `--disk-image-id`. See Accepted ADR 0021. |
| MCP images and tests | Both MCP types deployed and healthy on both Workers | Four live services, in addition to the private-MCP ACR build and private/public local tests (6/1). Both Docker paths use frozen corporate-feed locks. Label length and `500m` CPU / `10Gi` root-disk limits are handled. Authenticated/negative MCP cases from the Worker remain separate gates. |
| Native model authentication | Live-verified for both Workers | Actual invocations prove native `azure-foundry` Entra model access without a model token proxy. Hermes private/public MCP passed. Native model/tool ingestion is verified; the Sandbox-origin egress path preserves TraceId but changes a parent, leaving a platform waterfall limitation. |
| Private MCP ingress | Both private services healthy; Hermes tool attribution verified | Express reports `Succeeded` / public access `Disabled`; linked private Groups report `Disabled`. The exact private MCP execution span is ingested. Independent DNS/routing diagnostics, MI-authentication evidence, and broader negative authorization cases remain separate checks. |
| Gateway lifetime | Accepted | Auto-suspend disabled for detached post-ACK work and continuous Service Bus receive. Only runtime compute is OnDemand; do not claim full-system scale-to-zero. |
| External-agent registration | Live-verified; repeat unchanged | `autopilots-hermes` external definition was created/read back with matching kind, name, and `otel_agent_id`; repeat unchanged. No Hosted Agent compute. Registration and separately verified live telemetry ingestion are distinct proofs. |
| Native tracing integration | Workload attribution verified; egress-path parent rewrite isolated | All 49 native parents across smoke, MCP, user cron, and ad-hoc Dream resolve. Two real read-only requests show local `urllib` preserves its supplied parent but fresh uninstrumented `urllib` inside the gateway Sandbox changes it, retaining TraceId. Platform intermediate parents are absent from AppInsights; exact proxy implementation is unidentified. No fake parent spans, custom trace headers, or egress bypass. `/v1/responses` limitations remain. |
| Trace privacy sample | 105 spans audited read-only | Allowed metadata keys only; zero raw identity fields, zero nonopaque IDs, no Data/Url/Message content, and no AppTraces/AppExceptions rows in reviewed scope. Sample evidence, not a universal privacy guarantee. |
| Learning provenance | Integrated; 29 focused parent tests passed; live gate pending | Schema 3.0 cumulative evidence and learning routes are integrated; packet schema 2.0, signed agent-proposed scenarios, `SKILL.md`-only governance. These checks do not prove learning quality. |
| Refresh rejection | Implemented; live gate pending | Signed `reject_and_refresh` authorizes discard, not export; no fake approved packet. |
| Learning evaluation | Real response-only comparison completed | Hermes 0.19 CLI / `azure-foundry` / `gpt-5-6-terra`: Role 3.2 baseline `3/4`, Role 3.3 candidate `4/4`, zero regressions. Four manually/operator-authored cases; `independence=operator_declared`, `packetDigest=null`. Local evidence: `.artifacts\role-330-evaluation-verified.json`. Not agent-proposed packet, statistical generalization, tool-workflow, native-discovery, or autonomous-learning proof. |
| User scheduling | Live Service Bus delivery passed on Hermes 2 | Delivered receipt, verified output SHA, DLQ `0`; gateway Running/auto-suspend `false`. Runtime was already Running, so this is not wake/resume proof. |
| Scheduler recovery / Dreaming | Ad-hoc no-change Dream completed; crash recovery remains separate | Hermes 2 completed at phase `prepared`, `success=true`, `recordCount=0`, `packet=null`. Production cron unchanged, scheduled count `1`, DLQ `0`. Legitimate no changes, not learning improvement. Fencing/replay/ambiguous-start recovery and Teams send/receipt crash window retain their separate evidence requirements. |
| Native runtime resume | Direct SDK same-ID resume live-verified | Runtime ID beginning `4358e341` went `Running → Stopped → Running`, preserving the ID and persistent Data Disk marker. Reported resume timing: 1.3 s; marker removed afterward. This does not prove automatic eight-hour idle suspension. |
| Application runtime resume | Deployed same-ID/Data-Disk resume live-verified | Runtime `65db4109-ee34-4b52-a296-31d5499a8f3e` and Data Disk marker survived the final application path. It took 28.5 s including model execution, not directly comparable to the 1.3 s SDK test. Only `Failed` is recycled. Automatic eight-hour idle behavior remains unverified. |
| Teams targeted Agent User inbound | UI rechecked; no targeted availability observed | Hermes is discoverable through public group `@`, not `/`. Generated 1.1.7 `devPreview` has `agenticUserTemplates` only; Learn receive opt-in still uses `bots[].supportsTargetedMessages`. Installed hosting-core 1.1.0 lacks `send_targeted_activity`; plain send does not set targeting. No private content sent, bot fallback, universal-incompatibility claim, or proven UI-only cause. |
| Dependency audit | Open gap | Two inherited high-severity `image-size` findings; no fix published for the reviewed latest `pptxgenjs` 4.0.1 / `image-size` 2.0.2 tree. |
| MCP image/credential lifecycle | Live-verified | Repeated deployment retained Sandbox IDs without registry login, verified with a fail-if-called guard. All ten workload `AcrPull` assignments were removed and ACR admin is `false`. Native SDK b4 MI conversion still fails `401`; the accepted short-lived Entra credential path works for deployment-time conversion only. |
| MCP endpoint boundary probes | Negative checks and exact Hermes MCP tool spans verified | Public unauthenticated requests return `401`. Private external connections reset, not HTTP `403`. Native spans now attribute both actual MCP tools; independent route and MI-authentication diagnostics remain separate. |
| Existing-Worker identity reconciliation | Live-verified | Both Workers' gateway/runtime federation credentials were reconciled after CAE reauthentication, preserving Agent365 identities/grants. Scheduling/Dreaming remains `false/false` for Hermes, `true/true` for Hermes 2. Hermes private/public MCP passed; this does not establish every identity scope or negative authorization case. |
| Gateway/MCP terminal failure and reuse | Operator fix: 19 targeted tests; healthy reuse live-verified | Terminal `Failed` instances are excluded from matching-image reuse and can be replaced, including stale failed instances. Unchanged matching `Stopped`/`Suspended` services retain IDs. A real private-MCP redeployment retained its healthy ID and health `200`; this is not a live failed-replacement test. No image/endpoint rollout needed. Four MCP bootstrap logs remain available. |
| Operator modernization chain | Source/CLI-validated; full sequence not live-proven | 79 operator tests and eight CLI help checks passed. Explicit per-Worker workspace, infrastructure-only apply, identity reconciliation, second apply/service deployment, then endpoint update preserve existing identities. Fresh Agent365 bootstrap remains unresolved and is not covered. |
| Role Blueprint 3.3.0 publication | Published, pinned, bounded comparison completed | Four explicitly approved Role Blueprint files were committed as `b6b7f64d8ee92b1f1d1fd8b1023ce0f0b486123c` and pushed to `origin/main`. Hermes requires `>=0.19.0`. Both Workers serve model requests; the real four-case 3.2/3.3 comparison completed at `3/4` versus `4/4`, zero regressions. Other modernization code/docs remain uncommitted. |
| First-Worker MCP scenario | Live response and exact tool attribution verified | Expected service IDs and `SHIP1001`–`SHIP1003`; full-day query matched the morning MCP trace with `skill_view`, private `list_services`, and public `list_demo_shipments` execution spans. The earlier last-four-hours query missed it, not an ingestion blocker. Tool execution does not independently establish VNet route, MI authentication, or wider parity. |
| Agent365 endpoint reconciliation | Final registrations, operator GETs, and nonowner preflight block live-verified | Both final gateway endpoints registered, both model calls `200`, both operator pending GETs without a key `401`. Direct-owner preflight blocked a nonowner live. Explicitly authorized owner/admin cache removed; `tomas` remains default. Public `@` discovery is observed; targeted `/` availability is not. |

Prior demonstrations established Agent 365 identity, direct Teams messages/mentions, Microsoft 365 collaboration, governed cards, generated applications, and multi-Worker Promotion on the earlier deployment. They are not evidence that the modernized applications are deployed or behaviorally equivalent.

## Finish the approved modernization

### Close remaining deployed-behavior gaps

- Preserve model `200`s, endpoint registrations, operator pending GET `401`s, Hermes MCP success, and native telemetry. Record the isolated Sandbox-origin egress parent rewrite as a current native-platform limitation; do not promise a complete waterfall or bypass security to draw one. Preserve the scoped Teams discovery result and wider authorization gates.
- Preserve the completed MCP, logs, and identity-reconciliation milestones. Use live authenticated calls to verify the reconciled configuration without treating health or federation-credential creation as authorization proof.
- Preserve the completed four-image builds and deployment-time transient Entra-token conversion. Native MI remains blocked under the reported [upstream issue](https://github.com/microsoft/azure-container-apps/issues/1768).
- Verify the gateway receives `AGENT_RUNTIME_DISK_IMAGE_ID`, direct CLI startup uses `--disk-image-id`, and workloads need no ACR roles/credentials. Confirm no admin-password fallback, token persistence, renewal/cache service, or runtime/gateway conversion.
- Preserve the deployed service Sandboxes and captured real endpoints when validating subsequent configuration changes.
- Verify SDK-authenticated Agent 365 ingress and application-authenticated MCP, including negative authorization cases.
- Verify private MCP DNS/routing and refusal of public access; retain the private-origin no-fallback boundary.
- Preserve Hermes private/public MCP response and exact tool-trace attribution; continue licensed Agent User scenarios separately.
- Confirm the gateway remains alive after ACK and while the runtime suspends/resumes.
- Capture Terraform convergence and remove superseded application resources only after replacement behavior is proven.

### Prove recovery, not merely a successful first run

- Preserve user-schedule delivery/SHA/DLQ, ad-hoc no-change Dream, direct SDK resume, and final deployed application same-ID/Data-Disk resume as distinct proofs. The application measurement includes model execution. Automatic eight-hour idle suspension remains unverified.
- Inject interruption after a completed Dream response; verify replay without another model/tool invocation.
- Verify an ambiguous `dream_started` fails explicitly and cannot be blindly rerun.
- Preserve the ad-hoc Dream's unchanged production cron, scheduled count `1`, and DLQ `0`; verify stale-owner fencing through a separate interruption test.
- Demonstrate the remaining Teams send-to-receipt duplicate window accurately; do not label delivery exactly-once.

### Prove learning governance and bound the evaluation claim

- Exercise multiple edits of the same skill and retain cumulative baseline-to-final provenance.
- Validate synthetic `agentProposedScenarios` and their signed packet binding.
- Reject non-`SKILL.md` governed artifacts and private-content leakage.
- Inspect, approve/export, and reject/refresh through distinct signed operator paths.
- Preserve the completed operator-authored four-case response-only result (`3/4` baseline, `4/4` candidate, zero regressions). Broader independent holdouts, repeated trials, tool workflows, native discovery, and real agent-proposed packet cases remain separate work.
- Report model, prompt/output usage where available, latency, exact assertions, failures, and runner limitations. A proposed scenario or semantic review pass is not a measured improvement.

### Verify useful, privacy-preserving telemetry

- Register the external Worker in Foundry without changing its compute host.
- Verify the keyless project connection uses `ProjectManagedIdentity` and `ApplicationInsightsConnectionString`.
- Correlate inbound request, runtime/model/tool subprocess work, and scheduled continuation.
- Validate explicit-session native APIs separately from `/v1/responses`, which invents its own session and currently cannot take the context handoff. Inspect `autopilots.trace.correlation=missing` rather than hiding unlinked spans.
- Verify that interrupted requests retain their context lease; an affected session requires Worker restart before reuse, not silent reassignment to a new request.
- Confirm prompts, private documents, tool payloads, credentials, and tokens are excluded by default.
- Preserve live ingestion/native-parent evidence separately from local instrumentation tests. The controlled requests isolate the changed parent to Sandbox-origin egress, not ingress alone or application-exporter loss. Seek native platform clarification for the unidentified proxy/intermediate parents; preserve TraceId/opaque-ID correlation without fake spans or custom headers.
- Preserve the offline test's scope: real native plugin/SDK callback and file-tool wiring, with controlled SDK transport, are not a live Foundry model or ingestion result.

## Open design boundaries

- **Conversation memory:** shared group transcripts stay as implemented. Stable per-user memory keys do not prove isolation of Personal Memory, Work History, or transcript content. [ADR 0017](docs/adr/0017-messaging-session-lifecycle.md) contains the unresolved requirement/implementation tension; no per-user transcript redesign is approved.
- **Targeted Teams messages:** public group `@` discovery is present but targeted `/` discovery was absent in the user's recheck. Investigate the supported Agent User receive/send contract without assuming universal incompatibility or a UI-only defect. No private content was sent; no bot or public fallback is authorized.
- **Vulnerable transitive dependency:** keep audit results visible, monitor a supported upstream fix, and do not force an older dependency tree merely to make the audit green.
- **Preview guarantees:** an irreversible Group/environment link and native ingress behavior need operational checks. A platform apply does not prove cold-start behavior, application authorization, or private routing.

## Deferred capability work

| Milestone | Intent | Boundary |
| --- | --- | --- |
| A18 — External tool access | Registry-driven Azure DevOps, Foundry IQ, GitHub, and Clarity integration | Prefer managed MCP and existing Agent Identity / Agent User / explicit human OBO boundaries. No stored application secrets or general-purpose orchestrator. |
| A19 — External event ingress | Converge native notifications, webhooks, and justified polling on the per-Worker queue | Preserve originating privacy, durable cursors, deduplication, and explicit delivery receipts. |
| MCP Apps | Render tool-provided interactive experiences | Wait for native support in the Hermes/Agent User client; do not create another UI relay. |
| Human OBO | Explicit per-user resource access | Never ambient in autonomous schedules or shared conversations. |
| Fully suspending gateway | Lower idle gateway cost | Requires a real wake mechanism and proof that post-ACK work and queue receive survive; not part of current claims. |

## Rejected directions

- Foundry Hosted Agent compute migration, hosted resilience/reminder replacement, and compatibility relays.
- A companion Teams bot to work around Agent User targeted-message limitations.
- Public fallback for private input or tools.
- Self-approved learning packets, agent-proposed tests presented as independent evidence, and hidden success-shaped recovery.
- Runtime swarms, m:n group-chat orchestration, and distributed learning without human Promotion.

## Completion gate

The no-argument classroom path must use the actual deployed Worker, with truthful health, private/public tool access, identity, learning, recovery, observability, and cost evidence. Until those live checks pass, this modernization remains in progress.
