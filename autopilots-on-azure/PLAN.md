# Autopilots on Azure plan

## Purpose

This file tracks delivery status and future work. Product requirements and architecture belong in [SPEC.md](SPEC.md), deployment procedures in [DEPLOYMENT.md](DEPLOYMENT.md), demonstrations in [DEMO.md](DEMO.md), and the concise project entry point in [README.md](README.md).

## Current snapshot

As of 2026-07-22:

- OpenClaw and Hermes run side by side through separate bridge Container Apps and Terraform workspaces.
- Both runtimes use Sweden Central ACA Sandboxes and Foundry `gpt-5-6-terra`.
- Agent 365 packages, Agent Users, Teams direct-message and explicit-mention routing, reactions, Agent Identity MCP access, public shipments MCP, private incidents MCP, and Work IQ Mail are implemented.
- Hermes Workers `hermes` and `hermes2` are live on Role Release 3.2.0 at commit `2156bee66cb42008a6b75296f44f0d2f9a4a85fb`.
- Foreground learning, Dreaming, Role Skill/Candidate Improvement provenance, rollback, packet preparation, Ed25519 approval, export, merger/judge, and draft PR creation are implemented.
- Two independent Worker Learning Packets were consolidated, reviewed by five GitHub Agentic Workflow gates, and promoted through PR #6.
- Worker Refresh to 3.2.0 is complete; both Workers use `delivery-commitment-control`, retain private state, and have zero active previous-release Candidate Improvements.
- Direct Hermes CLI Candidate Improvements are automatically quarantined and provenance-bound on the next bridged turn or Dreaming run; this path is live-validated with `meeting-decision-record`.
- `hermes2` uses the same Junior Project Manager Role Blueprint but a separate Agent 365 platform blueprint and bridge under ADR 0014.
- `hermes2` retains its isolated Agent 365 platform blueprint, Agent Identity, Agent User, bridge, Terraform workspace, Sandbox volume, and approval identity.
- Both Hermes bridges use the runtime-specific wake/readiness and transient Worker Refresh preflight fixes; Terraform workspaces converge.
- The Teams Enterprise license temporarily transferred for the multi-Worker test is restored to `openclaw1`.
- Hermes 2 runs the live bridge-owned A11 scheduler daily; an on-demand cycle successfully produced one Candidate Improvement and prepared an approval-required packet without approving or exporting it.
- Durable requirements and architecture are consolidated in `SPEC.md`; deployment and demonstration procedures are separated into focused guides.

## Completed milestones

| Milestone | Status | Outcome |
| --- | --- | --- |
| A4.5 - Agent 365 packages | Complete | Runtime-specific Agent 365 packages, endpoints, and Teams validation for OpenClaw and Hermes. |
| A5 - Side-by-side deployments | Complete | Independent live OpenClaw and Hermes workspaces, bridges, identities, Sandbox state, and Agent Users. |
| A6 - Operator polish | Complete | Supported scripts for deployment, diagnostics, snapshots, logs, smoke tests, and Sandbox recovery. |
| A7 - Identity and MCP model | Complete | Agent Identity federation for autonomous MCP, Agent User Work IQ Mail, explicit OBO boundary, private and public MCP paths. |
| A8 - Role Blueprint distribution | Complete | Commit-pinned Hermes distribution, Worker manifest, persistent profile, and transactional Worker Refresh. |
| A9 - Local learning bridge | Superseded | Proved private classification, Dreaming, validated journal records, and generated hot learning before A10 native skills. |
| A10 - Native skill evolution and Collective Learning Review | Complete | Multi-Worker Private Playbooks, Role Skill patches, Candidate Improvements, schema-v2 provenance, attested packets, merger/judge, Agentic Promotion gates, merged Promotion, and Worker Refresh. |

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

Status: In progress; the bridge-owned classroom scheduler is live on Hermes 2, while the managed-identity ACA Job and disposable demo cohort remain.

Tasks:

- Choose an Azure Container Apps scheduled Job as the production scheduler.
- Complete: retain bridge-owned scheduling as the first classroom/demo option.
- Complete: add per-Worker enablement, initial delay, interval, focus, maximum records, retry/backoff, and packet-preparation settings.
- Complete: wake or reuse Workers through the existing bridge and Worker learning transaction.
- Complete: expose sanitized status for last Dream, packet preparation, failures, counters, and current Role Release.
- Complete: ensure automation never approves, exports, promotes, or merges learning.
- Complete: live-validate an on-demand Hermes 2 cycle that produced one transferable record and prepared one approval-required packet.
- Complete: deploy the scheduler bridge with a daily interval, one-hour startup delay, retry/backoff, and Terraform convergence.
- Add managed-identity authentication for the production ACA scheduled Job bridge endpoint without stored keys.
- Provision the ACA scheduled Job with Terraform/azapi and validate an on-demand execution.
- Add disposable `demo-*` Worker/Data Disk reset automation pinned to an immutable baseline.
- Add a disposable Git Promotion lane so the full lifecycle can be replayed without rewriting `main`.
- Add optional queue-driven fan-out for larger Worker populations.

Exit criteria:

- Dreaming runs on schedule for enabled Workers.
- Failures are visible and retry safely.
- Candidate Improvements can reach approval preparation without interactive Sandbox access.
- A disposable demo cohort can be reset and replayed without touching long-lived Worker state.

### A12 - Agent 365 workload notifications

Goal: add non-Teams Microsoft 365 event inputs while retaining Teams Activity Protocol for chat.

Tasks:

- Validate Agent 365 Email and Word comment notifications first.
- Add bridge sources such as `a365_email` and `a365_word_comment`.
- Use stable Work History keys based on message, document, thread, or comment identifiers.
- Reply through the originating workload rather than Teams.
- Add Excel and PowerPoint comments after Email and Word.
- Preserve runtime selection, identity, privacy, and learning boundaries.

Exit criteria:

- Email and Word notifications reach the selected Worker.
- The Worker responds or acts through the correct Microsoft 365 workload.
- Required permissions remain least privilege.

## Deferred

- Human OBO with explicit per-turn consent.
- Complete OpenClaw Role Blueprint and Collective Learning Review parity.
- Full Teams thread-follow or unmentioned-channel delivery unless Agent 365 adds support.
- Hermes dashboard exposure.
- Hermes-native Teams mode.
- Deeper multi-user profile isolation.
- An administrative dashboard backed by GitHub rather than a parallel Role Skill source of truth.
- Foundry Hosted Agents as an optional thin adapter, not the default OpenClaw or Hermes host.
