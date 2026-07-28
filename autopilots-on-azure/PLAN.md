# Autopilots on Azure plan

## Purpose

This file tracks delivery status and future work. Product requirements and architecture belong in [SPEC.md](SPEC.md), deployment procedures in [DEPLOYMENT.md](DEPLOYMENT.md), demonstrations in [DEMO.md](DEMO.md), and the concise project entry point in [README.md](README.md).

## Current snapshot

As of 2026-07-24:

- OpenClaw and Hermes run side by side through separate bridge Container Apps and Terraform workspaces.
- Both runtimes use Sweden Central ACA Sandboxes and Foundry `gpt-5-6-terra`.
- Agent 365 packages, Agent Users, Teams direct-message and explicit-mention routing, reactions, Agent Identity MCP access, public shipments MCP, private incidents MCP, and Work IQ Mail are implemented.
- Hermes Workers `hermes` and `hermes2` are live on Role Release 3.2.0 at commit `2156bee66cb42008a6b75296f44f0d2f9a4a85fb`.
- Ordinary foreground learning, explicit single-turn `/learn`, Dreaming, Role Skill/Candidate Improvement provenance, rollback, packet preparation, Ed25519 approval, export, merger/judge, and draft PR creation are implemented.
- Two independent Worker Learning Packets were consolidated, reviewed by five GitHub Agentic Workflow gates, and promoted through PR #6.
- Worker Refresh to 3.2.0 is complete; both Workers use `delivery-commitment-control`, retain private state, and have zero active previous-release Candidate Improvements.
- Direct Hermes CLI Candidate Improvements are automatically quarantined and provenance-bound on the next bridged turn or Dreaming run; this path is live-validated with `meeting-decision-record`.
- `hermes2` uses the same Junior Project Manager Role Blueprint but a separate Agent 365 platform blueprint and bridge under ADR 0014.
- `hermes2` retains its isolated Agent 365 platform blueprint, Agent Identity, Agent User, bridge, Terraform workspace, Sandbox volume, and approval identity.
- Both Hermes bridges use the runtime-specific wake/readiness and transient Worker Refresh preflight fixes; Terraform workspaces converge.
- The Teams Enterprise license temporarily transferred for the multi-Worker test is restored to `openclaw1`.
- Hermes 2 runs daily Dreaming through the same Service Bus/KEDA bridge wake path as user schedules; the dedicated ACA scheduled Job has been removed.
- Hermes 2 user schedules are live through Hermes cron, Service Bus, KEDA scale-from-zero, and proactive Teams continuation; recurring personal-chat delivery is validated with a visible unsolicited message.
- Hermes 2 has the hardened A13 DOCX/text attachment path and live Work IQ Word create, read, comment, and reply operations through its Agent User.
- Visual documentation now includes the main Hermes overview, a current architecture deep dive, and the source-guided memory/learning deep dive.
- Durable requirements and architecture are consolidated in `SPEC.md`; deployment and demonstration procedures are separated into focused guides.

## Milestone status

| Milestone | Status | Outcome |
| --- | --- | --- |
| A4.5 - Agent 365 packages | Complete | Runtime-specific Agent 365 packages, endpoints, and Teams validation for OpenClaw and Hermes. |
| A5 - Side-by-side deployments | Complete | Independent live OpenClaw and Hermes workspaces, bridges, identities, Sandbox state, and Agent Users. |
| A6 - Operator polish | Complete | Supported scripts for deployment, diagnostics, snapshots, logs, smoke tests, and Sandbox recovery. |
| A7 - Identity and MCP model | Complete | Agent Identity federation for autonomous MCP, Agent User Work IQ Mail, explicit OBO boundary, private and public MCP paths. |
| A8 - Role Blueprint distribution | Complete | Commit-pinned Hermes distribution, Worker manifest, persistent profile, and transactional Worker Refresh. |
| A9 - Local learning bridge | Superseded | Proved private classification, Dreaming, validated journal records, and generated hot learning before A10 native skills. |
| A10 - Native skill evolution and Collective Learning Review | Complete | Multi-Worker Private Playbooks, Role Skill patches, Candidate Improvements, schema-v2 provenance, attested packets, merger/judge, Agentic Promotion gates, merged Promotion, and Worker Refresh. |
| A11 - Scheduled Dreaming | Complete | Managed-identity ACA scheduled Job, bridge-owned classroom timer, packet preparation, observability, and disposable lifecycle reset. |
| A12 - User-scheduled Worker tasks | Complete | Hermes-native schedules, Service Bus/KEDA wake, crash-safe execution, schedule management, and visible proactive personal Teams delivery. |
| A12.1 - Unified Dreaming scheduler | Complete | Queue-driven `system.dream`, scale-to-zero wake, packet-preparation parity, durable system receipts, and removal of the A11 ACA Job. |
| A13 - Document-aware work and attachments | Complete | Secure DOCX/text attachment ingestion and live Work IQ Word create/read/comment/reply operations are validated in Teams and through the repeatable smoke. |
| A14 - Microsoft 365 knowledge and actions | Planned | SharePoint, OneDrive, Mail, Teams, Calendar, Word, and notification workload actions through Agent User identity. |
| A15 - Teams targeted private messaging | Planned | Private `/WorkerName` invocation inside supported group conversations. |

## Immediate work

### Refresh the live Worker to Role Release 3.1.0

Status: Complete.

Tasks:

- Complete: approved the current 3.0.1 Learning Packet.
- Complete: refreshed the Worker to merged Role Release 3.1.0.
- Complete: confirmed Personal Memory, Private Playbooks, and Work History survived.
- Complete: confirmed promoted Role Skill behavior is present.
- Complete: confirmed previous-release Candidate Improvements and provenance are archived.
- Complete: confirmed Terraform convergence and promoted Role Skill inference.

Exit criteria:

- Met: Worker health reports Role Release 3.1.0 at commit `60b8e7ef3fb594f386d5177032df434eb4e62917`.
- Met: private state is preserved.
- Met: promoted role behavior works with zero active Candidate Improvement files.

### Multi-Worker Collective Learning Review

Status: Complete.

Tasks:

- Complete: provision a second Worker from the same Role Release.
- Complete: provision Hermes 2 Agent Identity, Agent User, Agent 365 platform blueprint, consent, bridge, Data Disk, and Role Release 3.1.0.
- Complete: assign Agent 365, Flow, and a temporarily transferred Teams Enterprise license to Hermes 2.
- Complete: confirm Hermes 2 is discoverable and responsive in Teams after service propagation.
- Complete: teach Hermes 2 the divergent `dependency-handoff-contract` Candidate Improvement.
- Complete: produce independent, operator-approved, attested Learning Packets from both Workers.
- Complete: merge complementary evidence from both Workers into one `delivery-commitment-control` Role Skill proposal with explicit support and empty conflict/rejection sets.
- Complete: enforce rejection of duplicate Worker IDs, mixed Role Releases, malformed envelopes, unknown Workers, and unsafe proposal paths.
- Complete: close superseded PR #5 and create one-commit Promotion PR #6 for Role Release 3.2.0.
- Complete: add strict GitHub Agentic Workflow gates for Promotion triage, privacy, Role Blueprint alignment, learning evidence, and skill quality using `GITHUB_TOKEN` inference and permission-separated safe outputs.
- Complete: align the merger/judge and Skill Quality contracts so generation sees existing Role Skills and semantic review blocks concrete operational defects rather than editorial preferences.
- Complete: replace agent-driven PR discovery with one deterministic PR-head snapshot shared by every Promotion reviewer.
- Complete: pass all Agentic Promotion gates, merge PR #6, and refresh both Workers to Role Release 3.2.0.
- Complete: preserve Personal Memory `LOTUS-81`, Private Playbook `CEDAR-42`, Work History, Worker IDs, assignments, and Data Disks.
- Complete: archive previous-release Candidate Improvements and provenance; both Workers report zero active learning records.
- Complete: deploy the wake/readiness and transient refresh-preflight bridge fixes and confirm Terraform convergence.
- Complete: restore the Teams Enterprise license to `openclaw1`.

Exit criteria:

- Met: two independent Worker packets contributed to one Collective Learning Review.
- Met: the decision reports supporting Workers and records, conflicts, and rejected records.
- Met: no Personal Memory, Private Playbook, or Work History content appears in the Promotion.
- Met: privacy, Role Blueprint alignment, evidence, and skill quality gates all passed against one deterministic PR-head snapshot.

## Next milestones

### A11 - Scheduled Dreaming and collective-learning automation

Goal: run recurring Dreaming and packet preparation without manual Sandbox access.

Status: Complete; A11 proved the managed-identity ACA scheduled Job and guarded disposable lifecycle. A12.1 later retired the Job after queue-driven Dreaming reached parity.

Tasks:

- Complete: choose an Azure Container Apps scheduled Job as the production scheduler.
- Complete: retain bridge-owned scheduling as the first classroom/demo option.
- Complete: add per-Worker enablement, initial delay, interval, focus, maximum records, retry/backoff, and packet-preparation settings.
- Complete: wake or reuse Workers through the existing bridge and Worker learning transaction.
- Complete: expose sanitized status for last Dream, packet preparation, failures, counters, and current Role Release.
- Complete: ensure automation never approves, exports, promotes, or merges learning.
- Complete: live-validate an on-demand Hermes 2 cycle that produced one transferable record and prepared one approval-required packet.
- Complete: deploy the scheduler bridge with a daily interval, one-hour startup delay, retry/backoff, and Terraform convergence.
- Complete: add managed-identity authentication and `ScheduledLearning.Run.All` for the production ACA scheduled Job bridge endpoint without stored keys.
- Complete: model the ACA scheduled Job with Terraform/azapi and add on-demand execution/status commands.
- Complete: add fail-closed disposable `demo-*` Worker/Data Disk reset automation pinned to an immutable baseline.
- Complete: add a guarded disposable `demo/*` Git base lane for demonstrations that include real Promotion merge and Worker Refresh.
- Complete: provision the Entra resource API, deploy the ACA scheduled Job, and validate a successful on-demand execution.
- Complete: verify the Job-produced Dreaming result reaches approval-required packet preparation without automatic approval or export.
- Complete: live-validate fail-closed rejection of long-lived Worker reset and create/delete a disposable `demo/*` baseline branch.
- Complete: confirm the Hermes 2 Job execution reports `Succeeded` and Terraform converges with the bridge returned to scale-to-zero.
- Deferred until fleet scale requires it: queue-driven fan-out for larger Worker populations.

Exit criteria:

- Dreaming runs on schedule for enabled Workers.
- Failures are visible and retry safely.
- Candidate Improvements can reach approval preparation without interactive Sandbox access.
- A disposable demo cohort can be reset and replayed without touching long-lived Worker state.

### A12 - User-scheduled Worker tasks

Goal: let users create, inspect, pause, resume, cancel, and run scheduled Worker tasks through Hermes while preserving scale-to-zero, privacy, delivery context, and serverless reliability.

Decision: [ADR 0015](docs/adr/0015-service-bus-backed-hermes-cron.md) selects one Service Bus queue per Worker, direct KEDA scaling of the per-Worker bridge, and a Hermes Azure `CronScheduler` provider. A11's scheduled ACA Job remains until the unified queue path passes parity validation.

Status: Complete. Hermes 2 has the live Service Bus queue, KEDA-scaled bridge consumer, Azure cron provider, durable execution/delivery receipts, conversational management, and proactive Teams continuation. Automated one-shot scale-to-zero and recurring personal-chat delivery are live-validated, including a concrete Teams activity ID and visible unsolicited message.

Tasks:

- Complete: keep Hermes native cron jobs and execution history as canonical state on the Worker Data Disk.
- Complete: replace the unusable suspended-Sandbox ticker with the runtime-owned Azure `CronScheduler` plugin.
- Complete: provision a shared managed-identity-only Service Bus namespace and one duplicate-detecting, dead-letter-enabled queue per Worker.
- Complete: schedule only one next occurrence containing Worker ID, message type, job ID, revision, and due time; private prompts never enter Service Bus.
- Complete: scale the existing isolated bridge directly from zero through KEDA, with no separate event-driven execution app.
- Complete: consume under PeekLock with lock renewal, bounded concurrency, explicit settlement, retries, and DLQ behavior.
- Complete: wake or reuse the Sandbox and invoke protected profile-aware fire/reconcile endpoints.
- Complete: prevent duplicate execution with deterministic message IDs, Hermes claims, revision checks, pre-fire receipts, execution leases, and crash recovery.
- Complete: bind schedules durably before arming and preserve personal/group/channel delivery boundaries without private-to-public fallback.
- Complete: require a real Teams activity ID before acknowledging proactive delivery.
- Complete: block hosted `script`, `no_agent`, and durable human-OBO schedules; allow autonomous prompt jobs and reviewed Role Skills.
- Complete: support conversational create/list/pause/resume/remove/run-now operations and reconcile every schedule revision change.
- Complete: provide bounded audit receipts, sanitized persistent diagnostics, queue counters, and explicit failed-delivery behavior.
- Complete: validate one-shot scale-from-zero, recurring re-arm, Worker Data Disk persistence, DLQ handling, Terraform convergence, and visible personal-chat delivery.

Exit criteria:

- A user can schedule a one-shot or recurring task conversationally and manage it later.
- The Worker and bridge can scale to zero between occurrences.
- A due Service Bus message directly scales the per-Worker bridge, which executes the canonical Hermes cron job.
- Scheduled prompts and outputs retain the correct private/public delivery boundary.
- Duplicate Service Bus delivery cannot rerun the Hermes task; Teams continuation is explicitly at-least-once across a send/receipt crash boundary.
- Worker Refresh preserves active schedules and their execution history.
- A recurring scheduled result can be proactively delivered into the originating personal Teams conversation.

### A12.1 - Unified Dreaming scheduler

Goal: move platform Dreaming onto the same queue-driven wake path as user schedules and remove the dedicated A11 scheduler.

Status: Complete. Hermes 2 has a reserved daily Platform Dreaming job whose `system.dream` message wakes the bridge through KEDA. Live operator-triggered occurrences produced one Dreaming record, prepared an approval-required packet, persisted successful system receipts without changing the daily schedule, and left the queue and DLQ clean. The A11 ACA scheduled Job is deleted.

Tasks:

- Complete: provision a reserved Hermes Platform Dreaming schedule on the Worker Data Disk.
- Complete: emit one next `system.dream` Service Bus message without a prompt or private learning content.
- Complete: claim under a durable system receipt, run the existing Dreaming coordinator, record success/failure, and re-arm the next occurrence.
- Complete: validate scale-from-zero, retry ownership, one Dreaming record, approval-required packet preparation, next-occurrence restoration, and empty DLQ.
- Complete: disable and delete the dedicated A11 ACA scheduled Job and its obsolete auth/client tooling.
- Complete: add a repeatable operator-triggered Service Bus Dreaming smoke that leaves the configured production schedule unchanged.

Exit criteria:

- Met: Dreaming and user schedules share one queue-driven wake path.
- Met: no dedicated A11 scheduled Job remains.
- Met: Dreaming preserves its learning transaction and human approval boundary.
- Met: the production daily schedule is restored after repeatable live validation.

### A13 - Document-aware work and attachments

Goal: let Workers safely receive, open, reason over, create, and comment on Microsoft 365 documents while preserving identity and privacy boundaries.

Status: Complete. The Python bridge preserves and validates Teams attachment metadata, mirrors Microsoft’s JavaScript/.NET bot-token download pattern, extracts bounded private DOCX/text context, and transactionally blocks attachment-derived learning. Work IQ Word is deployed for Hermes 2; create, read, comment, and reply pass the repeatable live smoke, and a real DOCX attachment was successfully analyzed in the Hermes 2 personal Teams chat.

Tasks:

- Complete: preserve attachment metadata from Teams and Agent 365 activities instead of reducing every turn to plain text.
- Complete: confirm the Python Agents SDK has no `M365AttachmentDownloader`; mirror the supported JavaScript/.NET bot-token pattern with stricter URL, MIME, count, and size controls.
- Complete: research Microsoft-managed document MCP coverage. Agent 365 currently catalogs Word, OneDrive, SharePoint, and Excel; PowerPoint is not present. Word provides semantic DOCX text/comments and document/comment actions, while OneDrive/SharePoint provide permission-trimmed file operations up to 5 MB.
- Complete: add MIME type, extension, size, count, decompression-ratio, XML-size, and extracted-text limits plus explicit failure messages for unsupported or inaccessible files.
- Complete: prefer Work IQ Word content retrieval for OneDrive/SharePoint sharing URLs when direct attachment download is unavailable.
- Complete: add Work IQ Word to the Agent 365 Tooling manifest, Agent User consent, Sandbox identity proxy, and Hermes MCP configuration.
- Complete: live-validate Work IQ Word document creation, content retrieval, comment creation, and comment replies.
- Complete: add `scripts.document_smoke` to repeat the real create/read/comment/reply flow with a unique read-back marker and strict result validation.
- Complete: confirm the current preview limitation: Work IQ Word does not expose arbitrary in-place document-body editing; do not claim unsupported editing.
- Complete: define attachment-scoped connector authorization for current-turn downloads, Agent User authorization for Work IQ content, and explicit human OBO as future work.
- Complete: keep downloaded documents, extracted text, and comments in private Worker context and transactionally restore Personal Memory plus every skill namespace after attachment turns.
- Complete: keep bounded DOCX/text content in the private turn context without persistent temporary files; reject unsupported formats explicitly.
- Complete: scope A13 to DOCX and UTF-8 text. Work IQ Excel moves to A14; PowerPoint has no catalog server, and PDF/image ingestion remains future work.
- Complete: upload a real DOCX in the Hermes 2 personal Teams chat and verify document understanding.

Exit criteria:

- A Teams or Agent 365 turn with a Word attachment reaches the selected Worker with stable metadata and authorized content access.
- The Worker can summarize a document and its comments, create a new Word document, add a comment, and reply to a comment through Work IQ.
- Unsupported or unauthorized attachments fail explicitly without leaking content.
- Document content remains private and excluded from Collective Learning Review.

### A14 - Microsoft 365 knowledge and actions

Goal: prove the Agent User can find organizational knowledge and perform governed Microsoft 365 actions through Microsoft-managed Work IQ MCP servers while retaining Teams Activity Protocol for existing chat conversations.

Status: In progress. Hermes 2 now has live Agent User Mail, Teams, Calendar, OneDrive, SharePoint, Word, tenant-preview Excel, and M365 Copilot servers. Proactive one-to-one chat, Teams file delivery, directory resolution, same-item tracked Word updates, Agent User version attribution, and Excel range writes are live-validated. Email and Office notification handlers reuse `/api/messages`; live mention delivery remains to validate.

Tasks:

- Complete: expand the Agent 365 tooling manifest and Agent User proxy to Mail, Calendar, SharePoint, OneDrive, Teams, Word, tenant-preview Excel, and M365 Copilot.
- In progress: validate SharePoint and OneDrive search, result grounding, metadata retrieval, and authorized file operations with explicit preview-size limits.
- Complete: validate a real Mail send through the Agent User; retain explicit confirmation before externally visible send operations.
- Complete: validate Teams `CreateChat` and `SendMessageToChat` for proactive one-to-one outreach, including independent message read-back. Keep this separate from Activity Protocol continuation into an existing bot conversation.
- Complete: select ADR 0017's Hermes-native messaging lifecycle: bounded transcript IDs created through `/api/sessions`, stable per-conversation memory keys, and native compression/Work History instead of one lifetime Teams transcript or a parallel continuity store. The initial daily bucket was tightened to hourly after document-heavy validation.
- Validate Calendar free/busy, meeting-time suggestions, event creation, update, cancellation, and proposal flows with explicit confirmation before consequential writes.
- Reuse A13 for Word content, comments, document IDs, and sharing URLs.
- Complete: select ADR 0016's thin Agent User Graph file bridge plus the MIT MiniMax document skills pinned to a reviewed commit. Word uses MiniMax's Microsoft Open XML CLI and hard validation gates; a small original `office-collaboration` policy skill orchestrates identity-safe download, fixed tool wrappers, upload, and cleanup without copying Anthropic's restricted document skills.
- Complete: validate tenant-preview Excel workbook creation and Agent User range writes/read-back through the native Graph workbook API.
- Complete: return generated or modified files directly through Work IQ Teams `SendFileToUser`; retain governed links for group/channel flows when direct delivery is unsuitable.
- Complete: keep same-item Office collaboration as the default while retaining validated edits across persistent `423 Locked` responses. Retry only the publish, rebase guarded Word patches on newer ETags, and offer explicit retry-original, send-copy, or cancel outcomes without using checkout.
- Complete: protect acknowledged Activity Protocol attachment turns from premature KEDA termination. The 900-second cooldown exceeds the bounded 600-second Hermes turn while preserving eventual scale-to-zero.
- Complete: validate Office packages against the Microsoft 365 schema instead of the Open XML SDK's Office 2007 default, and recover delayed persisted Hermes responses for up to 120 seconds after a native session 5xx.
- Complete: use baseline-delta Open XML validation for existing Office files so unchanged Word-tolerated source defects do not block unrelated edits while new defects remain hard failures.
- Complete: make learning leases process-owned so controlled Sandbox replacement immediately recovers interrupted turns from the persistent Data Disk without weakening same-process concurrency protection.
- Complete: keep one lightweight Agent 365 bridge replica ready because ACA cold start can exceed the Activity Protocol response window before acknowledgement. Hermes Sandbox compute remains independently scale-to-zero.
- Complete: update ADR 0001 to treat ACA Express subsecond startup as the preferred future scale-to-zero bridge candidate, gated on managed identity, networking, health/scale controls, region, TLS, SLA posture, and repeatable Agent 365 p95 acknowledgement.
- Complete: tighten ADR 0017 from daily to hourly native transcript rotation after a same-day document session reached 499k cumulative input tokens; stop loading redundant MiniMax skill text when fixed wrappers already provide the operation.
- Complete: route Teams `/new`, `/reset`, and `/learn` as raw commands. `/new` now creates a durable unique native session generation and subsequent turns resolve the newest generation from Hermes session inventory.
- Complete: require explicit Graph permission before delivering an Agent User fallback copy, and retain sanitized Graph 423 diagnostics so persistent source locks can be classified instead of guessed.
- Complete: document the observed WOPI behavior: lock refresh resets a 30-minute expiry, Graph `423 notAllowed` does not reveal the holder, and Teams/Word/service activity may refresh the lock without a desktop file being open.
- Complete: select ADR 0019's predefined document-lock Teams suggested actions and deterministic 24-hour Service Bus retry workflow with ETag rebase, receipts, proactive completion, and explicitly shared copy fallback. A live probe showed proactive Agent 365 delivery strips Adaptive Card attachments, so Adaptive Cards remain an A16 investigation.
- Complete: resolve the invoking human's mail/UPN from the Teams Entra object ID so delivery offers can name the known destination instead of asking for an address already available in Microsoft 365.
- Implemented, pending live validation: route Agent 365 Email and Word/Excel/PowerPoint comment notifications through the existing `/api/messages` endpoint with stable workload/item IDs and persistence-disabled turns.
- Reply through the originating workload rather than silently redirecting output to Teams.
- Preserve runtime selection, Agent Identity/Agent User distinction, privacy boundaries, and learning exclusions.
- Complete: record and enforce the current product gap: this tenant catalogs a preview Excel MCP with create/read/comment tools, but no dedicated PowerPoint MCP. The original runtime skill can read and narrowly edit local PPTX files through Microsoft Open XML after Agent User download; PowerPoint has no tracked-changes model.

Exit criteria:

- The Worker can search SharePoint/OneDrive, send a reviewed email, create a Teams chat/message, and propose or create a calendar event through Microsoft-managed tools.
- Email and Word notifications reach the selected Worker and resolve referenced documents through A13.
- Reads and writes use the correct workload, identity, and least-privilege scopes.
- Consequential actions are explicit and auditable; preview limitations and unsupported Excel/PowerPoint operations fail clearly.

### A15 - Teams targeted private messaging

Goal: let a user privately invoke a Worker inside a channel, group chat, or meeting chat with `/WorkerName` while preserving the surrounding conversation context and strict user-only visibility.

Tasks:

- Post-process the Agent 365 Teams package with `bots[].supportsTargetedMessages: true`; the Agent 365 CLI does not add this capability or command lists automatically.
- Add the Worker-name slash entry that switches a group compose box into targeted-message mode.
- Add a targeted-private `/learn` command only after private transcript isolation and targeted response delivery are enforced.
- Do not expose `/new` in group/channel/meeting scopes: public reset semantics are ambiguous, and a private per-user reset adds unnecessary session semantics inside a shared thread.
- Keep personal-chat `/learn` and `/new` as documented text commands. Teams personal chats expose manifest prompt starters through the persistent View Prompts flyout, not custom `/` autocomplete; do not add prompt starters unless user testing shows the flyout improves discovery.
- Live-validate `/Hermes` and `/Hermes 2` in a channel, group chat, and meeting chat.
- Live-validate scheduled proactive continuation in an existing Teams channel and group chat, including thread placement.
- Verify inbound activities set `recipient.is_targeted` and map to a private-user bridge boundary.
- Ensure a targeted request always receives a targeted response unless the user explicitly approves public sharing.
- Give each targeted user-agent interaction a private transcript identity distinct from the public thread transcript; never place targeted prompts or tool results into later public model context.
- Keep targeted turns out of public bridge context, public Work History keys, public summaries, and public channel replies.
- Preserve a stable private memory key only for that authenticated user, Worker, and group conversation.
- Validate that files and Adaptive Cards preserve expected privacy; account for card actions that can generate public activities.
- Handle preview constraints: targeted messages expire after 24 hours and don't support reactions, replies, or forwarding.
- Add explicit fallback to a 1:1 chat when targeted send/receive isn't supported by the installed Agent 365 package.
- Test update/delete behavior and confirm expired targeted messages fail explicitly.

Exit criteria:

- A user can invoke each Worker privately with `/WorkerName` inside a supported group conversation.
- A user can invoke targeted-private `/learn`; `/new` is unavailable and explicitly rejected in group scopes.
- No untargeted participant can see the request, response, attachment, or derived context.
- The bridge and runtime retain the correct private-user authorization boundary.
- Unsupported clients or package configurations fall back safely to 1:1 chat.

### A16 - Interactive and generative UI

Goal: establish a governed cross-host UI model for agent interactions beyond plain text while preserving identity, accessibility, localization, privacy, and deterministic consequential actions.

Status: Pending. A14 introduces predefined document-lock suggested actions as a deliberately narrow precursor. A live probe showed proactive Agent 365 delivery strips Adaptive Card attachments, making host-specific cards versus MCP Apps an explicit A16 question.

Tasks:

- Inventory Teams Adaptive Cards, Microsoft 365 Copilot UI capabilities, Agent 365 host extensions, MCP Apps, and text-only fallbacks.
- Decide the boundary between server-owned interaction contracts, agent-supplied safe content, and fully generated UI.
- Prefer typed predefined cards for consequential actions; do not let a model invent operation identifiers, authorization data, callback URLs, or unbounded card JSON.
- Evaluate MCP Apps for portable interactive views and determine how they coexist with native Teams cards rather than assuming one replaces the other.
- Define signed/encrypted action tokens, user/conversation binding, expiry, replay protection, idempotency, confirmation, cancellation, and audit requirements.
- Define update/replace semantics for cards and long-running operations, including progress, completion, failure, and stale-action UX.
- Cover personal chat, targeted private messages, group/channel visibility, mobile clients, accessibility, localization, and unsupported-host fallbacks.
- Add schema validation, render snapshots, action simulations, host compatibility tests, security tests, and interaction-quality evaluations.
- Measure card delivery/action latency, abandonment, duplicate actions, and model/UI token cost.
- Decide whether Hermes receives a curated UI skill, a typed UI MCP, MCP Apps resources, or a combination only after prototypes are compared.

Exit criteria:

- At least two representative workflows run through the selected typed UI contract in Teams and one additional supported host.
- Consequential actions are deterministic, authenticated, idempotent, accessible, localized, and auditable.
- Unsupported hosts receive an equivalent safe text interaction.
- The chosen relationship between Adaptive Cards, agent-generated content, and MCP Apps is recorded in an ADR.

## Deferred

- Human OBO with explicit per-turn consent.
- Per-user schedule quotas, deterministic management fallback, and operator DLQ replay/remove commands.
- Complete OpenClaw Role Blueprint and Collective Learning Review parity.
- Full Teams thread-follow or unmentioned-channel delivery unless Agent 365 adds support.
- Hermes dashboard exposure.
- Hermes-native Teams mode.
- Deeper multi-user profile isolation.
- An administrative dashboard backed by GitHub rather than a parallel Role Skill source of truth.
- Foundry Hosted Agents as an optional thin adapter, not the default OpenClaw or Hermes host.
