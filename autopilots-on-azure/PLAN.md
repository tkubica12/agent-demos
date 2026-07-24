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
- Hermes 2 runs the managed-identity ACA scheduled Job daily; an on-demand execution succeeded and reached approval-required packet preparation without approving or exporting it.
- Hermes 2 user schedules are live through Hermes cron, Service Bus, KEDA scale-from-zero, and proactive Teams continuation; recurring personal-chat delivery is validated with a visible unsolicited message.
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
| A12.1 - Unified scheduling hardening | Next | Move Dreaming onto the Worker queue, remove the A11 Job after parity, live-validate channel/group delivery, and add explicit schedule quotas and DLQ operations. |
| A13 - Document-aware work and attachments | Planned | Secure Teams/Microsoft 365 attachment ingestion and Work IQ Word operations. |
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

Status: Complete; Hermes 2 uses the managed-identity ACA scheduled Job, and guarded disposable Worker/Data Disk/Git reset automation is available for classroom replays.

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

### A12.1 - Unified scheduling hardening

Goal: finish platform-wide schedule consolidation after the user-scheduling milestone.

Status: Next.

Tasks:

- Schedule `system.dream` messages through the same per-Worker queue and bridge dispatcher.
- Validate Dreaming schedule, retry, scale-to-zero, packet preparation, and no-duplicate parity with A11.
- Remove the dedicated A11 ACA scheduled Job only after queue-driven Dreaming passes parity.
- Live-validate proactive continuation in an existing Teams channel and group chat, including thread placement and privacy boundaries.
- Add explicit per-Worker and per-user schedule quotas plus operator DLQ inspect/replay/remove commands.
- Add deterministic management commands for schedule listing when model inference is transiently unavailable.

Exit criteria:

- Dreaming and user schedules share one queue-driven wake path.
- No dedicated A11 scheduled Job remains.
- Existing personal, group, and channel conversations receive scheduled output through their original boundary.
- Operators can inspect and safely replay or remove dead-lettered schedule messages.

### A13 - Document-aware work and attachments

Goal: let Workers safely receive, open, reason over, create, and comment on Microsoft 365 documents while preserving identity and privacy boundaries.

Tasks:

- Preserve attachment metadata from Teams and Agent 365 activities instead of reducing every turn to plain text.
- Validate secure file download for Teams/Microsoft 365 attachments; investigate Python SDK parity with the documented JavaScript `M365AttachmentDownloader`.
- Add MIME type, extension, size, and count allowlists plus explicit failure messages for unsupported or inaccessible files.
- Prefer Work IQ Word `WordGetDocumentContent` for OneDrive/SharePoint sharing URLs so the Worker receives extracted text, comments, and document IDs rather than raw DOCX bytes.
- Validate Work IQ Word document creation, content retrieval, comment creation, and comment replies.
- Confirm the current preview limitation: Work IQ Word does not expose arbitrary in-place document-body editing; do not claim unsupported editing.
- Define when access uses Agent User, an attachment-scoped Agents SDK token, or explicit human OBO.
- Keep downloaded documents, extracted text, and comments in private Worker context; never emit document excerpts into Candidate Improvements or Learning Packets.
- Add a Sandbox attachment contract for content that cannot be handled solely through a sharing URL and Work IQ MCP.
- Validate Word first, then assess Excel, PowerPoint, PDF, images, and other Office attachments.

Exit criteria:

- A Teams or Agent 365 turn with a Word attachment reaches the selected Worker with stable metadata and authorized content access.
- The Worker can summarize a document and its comments, create a new Word document, add a comment, and reply to a comment through Work IQ.
- Unsupported or unauthorized attachments fail explicitly without leaking content.
- Document content remains private and excluded from Collective Learning Review.

### A14 - Microsoft 365 knowledge and actions

Goal: prove the Agent User can find organizational knowledge and perform governed Microsoft 365 actions through Microsoft-managed Work IQ MCP servers while retaining Teams Activity Protocol for existing chat conversations.

Tasks:

- Expand the Agent 365 tooling manifest from Mail-only to the currently supported Microsoft-managed Work IQ servers for Mail, Calendar, SharePoint, OneDrive, Teams, User, Word, and Copilot.
- Validate SharePoint and OneDrive search, result grounding, metadata retrieval, and authorized file operations with explicit preview-size limits.
- Validate Mail search, draft, reply, and send through the Agent User; distinguish a proposed draft from an externally visible send.
- Validate Teams `createChat` and `postMessage` for proactive one-to-one and group outreach. Keep this separate from Activity Protocol continuation into an existing bot conversation.
- Validate Calendar free/busy, meeting-time suggestions, event creation, update, cancellation, and proposal flows with explicit confirmation before consequential writes.
- Reuse A13 for Word content, comments, document IDs, and sharing URLs.
- Validate Agent 365 Email and Word comment notifications as event inputs, using stable Work History keys based on message, document, thread, or comment identifiers.
- Reply through the originating workload rather than silently redirecting output to Teams.
- Preserve runtime selection, Agent Identity/Agent User distinction, privacy boundaries, and learning exclusions.
- Record the current product gap: no dedicated Microsoft-managed Work IQ Excel or PowerPoint MCP server is available; do not simulate or claim those capabilities.

Exit criteria:

- The Worker can search SharePoint/OneDrive, send a reviewed email, create a Teams chat/message, and propose or create a calendar event through Microsoft-managed tools.
- Email and Word notifications reach the selected Worker and resolve referenced documents through A13.
- Reads and writes use the correct workload, identity, and least-privilege scopes.
- Consequential actions are explicit and auditable; preview limitations and unsupported Excel/PowerPoint operations fail clearly.

### A15 - Teams targeted private messaging

Goal: let a user privately invoke a Worker inside a channel, group chat, or meeting chat with `/WorkerName` while preserving the surrounding conversation context and strict user-only visibility.

Tasks:

- Confirm whether Agent 365 AI teammate packaging can emit the Teams manifest `bots[].supportsTargetedMessages: true` capability or requires an additional supported package surface.
- Add discoverable agent slash commands only after targeted-message opt-in is confirmed.
- Live-validate `/Hermes` and `/Hermes 2` in a channel, group chat, and meeting chat.
- Verify inbound activities set `recipient.is_targeted` and map to a private-user bridge boundary.
- Ensure a targeted request always receives a targeted response unless the user explicitly approves public sharing.
- Keep targeted turns out of public bridge context, public Work History keys, and public channel replies.
- Validate that files and Adaptive Cards preserve expected privacy; account for card actions that can generate public activities.
- Handle preview constraints: targeted messages expire after 24 hours and don't support reactions, replies, or forwarding.
- Add explicit fallback to a 1:1 chat when targeted send/receive isn't supported by the installed Agent 365 package.
- Test update/delete behavior and confirm expired targeted messages fail explicitly.

Exit criteria:

- A user can invoke each Worker privately with `/WorkerName` inside a supported group conversation.
- No untargeted participant can see the request, response, attachment, or derived context.
- The bridge and runtime retain the correct private-user authorization boundary.
- Unsupported clients or package configurations fall back safely to 1:1 chat.

## Deferred

- Human OBO with explicit per-turn consent.
- Complete OpenClaw Role Blueprint and Collective Learning Review parity.
- Full Teams thread-follow or unmentioned-channel delivery unless Agent 365 adds support.
- Hermes dashboard exposure.
- Hermes-native Teams mode.
- Deeper multi-user profile isolation.
- An administrative dashboard backed by GitHub rather than a parallel Role Skill source of truth.
- Foundry Hosted Agents as an optional thin adapter, not the default OpenClaw or Hermes host.
