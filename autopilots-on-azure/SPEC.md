# Autopilots on Azure specification

## Document role

This is the normative product specification for Autopilots on Azure.

- [ARCHITECTURE.md](ARCHITECTURE.md) describes component boundaries, trust boundaries, and implemented design.
- [PLAN.md](PLAN.md) tracks milestones, current status, and future work.
- [README.md](README.md) contains deployment, operation, validation, and demonstration procedures.
- [`docs\adr`](docs/adr) records consequential decisions.
- [`docs\runbooks`](docs/runbooks) contains repeatable multi-control-plane procedures.

## Product intent

Autopilots on Azure hosts durable digital Workers behind Microsoft Agent 365. A Worker:

- has a Microsoft 365 identity and user-facing Teams presence;
- runs OpenClaw or Hermes inside an Azure Container Apps Sandbox;
- uses managed identity and Agent Identity federation for autonomous tools;
- preserves private state on a Sandbox Data Disk;
- can adapt locally without immediately changing the shared role;
- can submit governed Candidate Improvements for human-reviewed Promotion.

Hermes is the primary implementation for durable Worker memory, native skills, Dreaming, and Collective Learning Review. OpenClaw remains a peer runtime behind the same bridge and identity contract but does not yet implement the complete Role Blueprint lifecycle.

## Vocabulary

These terms are part of the product contract and must be used consistently in user experience, documentation, APIs, manifests, scripts, and new code. Azure or Agent 365 APIs may retain platform-specific terms such as *instance* or *blueprint* where required.

| Term | Meaning |
| --- | --- |
| **Role Blueprint** | Reviewed central definition of a job such as Junior Project Manager or Customer Support Specialist. Contains role instructions, Role Skills, tool configuration, and release metadata. |
| **Role Release** | Numbered, immutable semantic version of a Role Blueprint. |
| **Worker** | Named digital teammate created from a Role Release, with its own identity, assignment, Personal Memory, Work History, and local adaptations. |
| **Personal Memory** | Small private facts in Hermes `USER.md` and `MEMORY.md`, injected into every fresh session. |
| **Private Playbook** | Hermes-native private skill, optionally with references, containing rich assignment-, customer-, account-, manager-, or team-specific knowledge and procedures. |
| **Work History** | Private Hermes SQLite sessions, messages, tool calls, and search indexes. |
| **Role Skill** | Skill inherited from a Role Release. A Worker may improve it locally. |
| **Candidate Improvement** | Local Role Skill patch or new reusable skill that may benefit other Workers. |
| **Dreaming** | Offline reflection over Work History, outcomes, corrections, memory, and skills. |
| **Collective Learning Review** | Multi-Worker review of Candidate Improvements, evidence, provenance, and conflicts. |
| **Promotion** | Human-reviewed acceptance of a Candidate Improvement into a new Role Release. |
| **Worker Refresh** | Adoption of a newer Role Release while preserving private Worker state. |
| **Learning Packet** | Fail-closed, approved export containing allowed skill artifacts or diffs plus provenance and privacy checks. |

Do not describe Candidate Improvements as *public memory*. They remain local until Promotion.

## System requirements

### Hosting

1. Public ingress must terminate at a runtime-specific Azure Container Apps bridge.
2. OpenClaw and Hermes must run inside Azure Container Apps Sandboxes, not ACA Dynamic Sessions.
3. Sandbox runtime state must reside on a persistent Data Disk.
4. Bridge Container Apps may scale to zero.
5. An incoming bridge request must wake or reuse the selected Sandbox and await the runtime operation.
6. Runtime-specific Terraform workspaces must prevent OpenClaw and Hermes app state from colliding.

### Microsoft 365 and Agent 365

1. Agent 365 is the only Microsoft 365 packaging and messaging lifecycle.
2. Agent 365 Agent Users are provisioned digital teammates, not classic installed Teams bots.
3. The bridge must process Agent 365 Activity Protocol traffic through Microsoft 365 Agents SDK.
4. Direct messages, explicit mentions, and targeted activities are supported.
5. Unmentioned Teams channel messages are not assumed to be delivered.
6. Agent 365 notification workloads must use workload-appropriate response channels rather than being converted into Teams messages.

### Identity and authorization

1. The Sandbox Group managed identity proves the workload location but is not the business authorization principal.
2. Autonomous private and service-to-service tool access must use Agent Identity application authorization.
3. Worker-owned Microsoft 365 data must use the Worker Agent User.
4. Human delegated or OBO access must be explicit, per-user, and per-turn.
5. Group or channel context must never cause one human's delegated token to become ambient shared authorization.
6. Application secrets and static MCP keys must not be stored in Worker skills or memory.
7. Private network reachability must not replace Entra authorization.

### Tool integration

1. Private custom MCP servers must be reachable from the Sandbox through the customer VNet connection.
2. Public custom MCP servers must use HTTPS and Entra authorization.
3. Sandbox runtimes must call loopback identity adapters rather than managing Agent Identity token exchange themselves.
4. Work IQ Mail acts as the Worker Agent User mailbox.
5. Agent 365 BYO MCP registration is a governance and supported-client surface; runtimes may use direct Agent Identity access where appropriate.

## Role Blueprint and Worker lifecycle

### Role Blueprint distribution

A Role Blueprint distribution must contain only reviewed releasable state:

```text
distribution.yaml
SOUL.md
config.yaml
mcp.json
skills\role\
schemas\
cron\            optional
```

It must not contain:

```text
.env
auth.json
memories\
state.db*
sessions\
logs\
workspace\
skills\private\
skills\candidates\
learning\
```

`distribution.yaml` must identify:

- `role_blueprint`;
- immutable semantic `role_release`;
- exact `distribution_owned` paths;
- minimum supported Hermes version when applicable.

### Worker manifest

Each Hermes Worker must record:

- Role Blueprint name, source, and repository-relative path;
- Role Release and immutable Git commit;
- Worker ID and assignment scope;
- exact distribution-owned paths;
- baseline hashes for Role Skills.

The canonical manifest path is:

```text
<profile>\local\worker.json
```

### Worker Refresh

1. A Worker Refresh may only move to a strictly newer semantic Role Release.
2. The current Worker must remain available when refresh preflight fails.
3. Local Role Skill changes and Candidate Improvements must have an approved, state-bound Learning Packet before replacement.
4. Refresh must be transactional and recover the previous profile after an interrupted copy or manifest update.
5. Role Skills are replaced by the new Role Release.
6. Previous-release Candidate Improvements and provenance are archived.
7. Personal Memory, Private Playbooks, and Work History are preserved.
8. A fresh session must use the new Role Release skill index.

## Worker memory and learning

### Personal Memory

1. `USER.md` stores identity, preferences, communication style, and expectations.
2. `MEMORY.md` stores compact environment facts, conventions, and critical durable notes.
3. Both stores are bounded Hermes-native memory and are injected as a frozen snapshot at fresh-session start.
4. Personal Memory survives every Worker Refresh.
5. Personal Memory is never included in a Learning Packet.

### Private Playbooks

1. Rich private context must use Hermes-native skills under `skills\private\<name>`.
2. A Private Playbook may include `references`, templates, or scripts when needed.
3. Private Playbooks provide progressive disclosure and must not consume every prompt.
4. Private Playbooks survive Worker Refresh.
5. Private Playbooks are never included in a Learning Packet.

### Work History

1. Raw sessions, messages, and tool calls remain in Hermes SQLite state.
2. Work History supports `session_search`, ordinary recall, and Dreaming.
3. Work History survives Worker Refresh.
4. Raw Work History is never exported to Collective Learning Review.

### Role Skills and Candidate Improvements

1. Inherited skills reside under `skills\role\<name>`.
2. New reusable local skills reside under `skills\candidates\<name>`.
3. Skill basenames must be globally unique across namespaces.
4. Hermes may patch Role Skills and create Candidate Improvements through native `skill_manage`, `/learn`, foreground adaptation, or Dreaming.
5. Private details must never be written into Role Skills or Candidate Improvements.
6. A fresh session is the guaranteed activation boundary for changed skill content.
7. Candidate Improvements are scoped to one Role Release.

### Learning provenance

Every governed Role Skill or Candidate Improvement change must have one schema-v2 provenance record containing:

- record identity and timestamp;
- classification and source stage;
- exact artifact path and action;
- before and after content hashes;
- changed files;
- Role Release and Worker identity;
- generalized learning and rationale;
- redacted evidence summaries;
- confidence and privacy result.

The canonical journal is:

```text
<profile>\learning\records.jsonl
```

The journal explains *why* behavior changed. The skill tree is the actual Worker behavior.

### Learning transactions

1. Governed skill writes must be serialized per Worker.
2. The runtime must snapshot governed skills before each foreground or Dreaming turn.
3. Unprovenanced or privacy-rejected governed changes must be rolled back.
4. Interrupted transactions must recover atomically without retaining malformed provenance.
5. Asynchronous governed drift must be quarantined and restored to the last committed state.
6. Personal Memory and Private Playbook writes remain local and do not require transferable provenance.
7. Governed skills written directly through `hermes --cli` are not immediately provenance-bound; the next bridged turn or Dreaming run must quarantine, classify, safely recreate, and reconcile them before Collective Learning Review.

### Dreaming

Dreaming may:

- consolidate Personal Memory;
- create or improve Private Playbooks;
- patch Role Skills;
- create Candidate Improvements;
- attach provenance;
- suppress duplicates;
- intentionally store nothing.

Dreaming must use a fresh isolated session and must not expose private source content in transferable artifacts or provenance.

## Collective Learning Review

### Learning Packet preparation

1. Packets may contain only current Role Skill diffs, Candidate Improvement artifacts, and matching provenance.
2. Packets must exclude Personal Memory, Private Playbooks, Work History, credentials, logs, caches, and workspace data.
3. Packet preparation must fail when an artifact lacks matching current-hash provenance.
4. Privacy checks must scan every artifact and every field sent to the merger/judge.
5. Human approval must bind the exact packet digest, Worker, Role Release commit, and governed-state hash.

### Approval and attestation

1. Workers must not possess the approval private key.
2. The bridge holds an Ed25519 approval private key.
3. Workers and central review use trusted public keys only.
4. Central review must reject unsigned, modified, stale, unapproved, or unknown-Worker packets.
5. Worker Refresh must verify the same attestation before replacing governed state.

### Merger/judge

1. Collective Learning Review accepts packets only from the same Role Release.
2. Independent Worker evidence increases confidence but does not automatically force Promotion.
3. The merger/judge must identify support, conflicts, outliers, and rejected provenance.
4. Only privacy-scanned minimized packet content may be sent to the model.
5. Proposed files must be restricted to `skills\role\<name>\SKILL.md`.
6. The complete decision, including summary, conflicts, and rejection reasons, must pass privacy checks.

### Promotion

1. Promotion must create a Git branch and pull request against the Role Blueprint source.
2. The pull request must be draft by default.
3. Human expert review is required before merge.
4. The next Role Release must be a strictly newer semantic version.
5. Git remains the source of truth for promoted Role Skills.

## Security and privacy requirements

1. Secrets, access tokens, credentials, raw private messages, customer details, tenant identifiers, internal URLs, and user-specific paths must not enter transferable artifacts.
2. Learning export is fail closed.
3. Artifact allowlists and namespace boundaries must be enforced in code, not only prompt instructions.
4. Private paths listed in packet metadata are informational; actual exclusion must occur before packet construction.
5. Sandbox and bridge diagnostic output must redact tokens and secrets.
6. Symlinks and unsupported binary files are not allowed in governed skill artifacts.
7. Worker state is single-writer because Hermes uses SQLite and transactional skill state.

## Operability requirements

1. Setup, build, deployment, validation, Dreaming, packet approval, export, and cleanup must be scriptable.
2. Operator smoke and Dreaming sessions must use fresh conversation IDs.
3. Model failures must abort and release learning transactions for immediate retry.
4. API-key rotation must force controlled Sandbox replacement and may use the previous key only for refresh preflight.
5. Terraform must converge after deployment.
6. Runtime health must report Worker ID, Role Blueprint, Role Release, release commit, and gateway status.

## Current product constraints

- ACA Sandboxes, Agent 365 AI teammates, reactions, and BYO MCP include preview surfaces.
- Teams delivers direct messages, explicit mentions, and targeted activities, not every unmentioned channel message.
- Agent 365 BYO MCP requires supported-client connection and OAuth behavior; a raw generic MCP client is insufficient.
- Human OBO is not implemented.
- OpenClaw does not yet implement the complete Hermes Role Blueprint and Collective Learning Review lifecycle.
- Multi-Worker Collective Learning Review is implemented but has only been live-validated with one Worker packet.
- Scheduled Dreaming automation is not yet implemented.
- Agent 365 Email and Office comment notifications are not yet implemented.

## Non-goals

- General-purpose autonomous runtime swarms.
- m:n group-chat agent behavior.
- Distributed skill learning without human review.
- Direct Worker writes to the central Role Blueprint.
- A custom central database as the source of truth for Role Skills.
- Replacing managed identity and Entra authorization with stored credentials.
