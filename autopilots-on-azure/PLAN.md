# Autopilots on Azure plan

## Purpose

This file tracks delivery status and future work. Product requirements belong in [SPEC.md](SPEC.md), architecture in [ARCHITECTURE.md](ARCHITECTURE.md), and operator procedures in [README.md](README.md).

## Current snapshot

As of 2026-07-16:

- OpenClaw and Hermes run side by side through separate bridge Container Apps and Terraform workspaces.
- Both runtimes use Sweden Central ACA Sandboxes and Foundry `gpt-5-6-terra`.
- Agent 365 packages, Agent Users, Teams direct-message and explicit-mention routing, reactions, Agent Identity MCP access, public shipments MCP, private incidents MCP, and Work IQ Mail are implemented.
- Hermes Worker `hermes` is live on Role Release 3.0.1 with preserved Personal Memory, Private Playbooks, and Work History.
- Foreground learning, Dreaming, Role Skill/Candidate Improvement provenance, rollback, packet preparation, Ed25519 approval, export, merger/judge, and draft PR creation are implemented.
- Collective Learning Review PR #3 was approved and merged, publishing Role Release 3.1.0.
- The live Worker has not yet refreshed from 3.0.1 to 3.1.0.
- Multi-Worker packet fan-in remains untested because only one Hermes Worker is currently available.

## Completed milestones

| Milestone | Status | Outcome |
| --- | --- | --- |
| A4.5 - Agent 365 packages | Complete | Runtime-specific Agent 365 packages, endpoints, and Teams validation for OpenClaw and Hermes. |
| A5 - Side-by-side deployments | Complete | Independent live OpenClaw and Hermes workspaces, bridges, identities, Sandbox state, and Agent Users. |
| A6 - Operator polish | Complete | Supported scripts for deployment, diagnostics, snapshots, logs, smoke tests, and Sandbox recovery. |
| A7 - Identity and MCP model | Complete | Agent Identity federation for autonomous MCP, Agent User Work IQ Mail, explicit OBO boundary, private and public MCP paths. |
| A8 - Role Blueprint distribution | Complete | Commit-pinned Hermes distribution, Worker manifest, persistent profile, and transactional Worker Refresh. |
| A9 - Local learning bridge | Superseded | Proved private classification, Dreaming, validated journal records, and generated hot learning before A10 native skills. |
| A10 - Native skill evolution and Collective Learning Review | Complete for one Worker | Private Playbooks, Role Skill patches, Candidate Improvements, schema-v2 provenance, attested packets, merger/judge, and merged Promotion PR. |

## Immediate work

### Refresh the live Worker to Role Release 3.1.0

Status: Ready after user testing of the current 3.0.1 Worker.

Tasks:

- Prepare and approve a current 3.0.1 Learning Packet if governed state changes after the last approval.
- Refresh the Worker to merged Role Release 3.1.0.
- Confirm Personal Memory, Private Playbooks, and Work History survive.
- Confirm promoted Role Skill behavior is present.
- Confirm previous-release Candidate Improvements and provenance are archived.
- Re-run foreground learning, fresh-session recall, and Dreaming smoke tests.

Exit criteria:

- Worker health reports Role Release 3.1.0 and its merged commit.
- Private state is unchanged.
- Promoted role behavior works without loading previous Candidate Improvements.

### Multi-Worker Collective Learning Review

Status: Pending a second Worker.

Tasks:

- Provision a second Worker from the same Role Release.
- Produce independent, attested Learning Packets from both Workers.
- Exercise repeated support, conflicting evidence, outliers, and rejection rationale.
- Verify central review rejects duplicate Worker IDs and mixed Role Releases.
- Create and review a multi-Worker draft Promotion PR.

Exit criteria:

- At least two independent Worker packets contribute to one Collective Learning Review.
- The decision reports support counts, conflicts, and rejected records.
- No Personal Memory, Private Playbook, or Work History content appears in the PR.

## Next milestones

### A11 - Scheduled Dreaming and collective-learning automation

Goal: run recurring Dreaming and packet preparation without manual Sandbox access.

Tasks:

- Choose an Azure Container Apps scheduled Job as the production scheduler.
- Retain bridge-owned scheduling only as a simple demo option.
- Add per-Worker cadence, enablement, timeout, retry, and backoff settings.
- Wake or reuse Workers through the bridge.
- Track last Dream, last successful packet, last approval, and current Role Release.
- Add optional queue-driven fan-out for larger Worker populations.

Exit criteria:

- Dreaming runs on schedule for enabled Workers.
- Failures are visible and retry safely.
- Candidate Improvements can reach approval preparation without interactive Sandbox access.

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
