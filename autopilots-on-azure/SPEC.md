# Autopilots on Azure specification

## Document role

This is the authoritative product and architecture specification for Autopilots on Azure.

- [PLAN.md](PLAN.md) tracks delivery status, history, and future work.
- [DEPLOYMENT.md](DEPLOYMENT.md) contains deployment, update, validation, and cleanup procedures.
- [DEMO.md](DEMO.md) contains classroom and product demonstration scenarios.
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

## Architecture principles

1. Agent 365 is the only Microsoft 365 packaging and messaging lifecycle.
2. OpenClaw and Hermes are peer runtimes behind one bridge and Sandbox contract.
3. The public bridge handles ingress and Sandbox lifecycle, not MCP data-plane proxying.
4. Private tools stay private; network reachability and authorization are separate controls.
5. Agent Identity is the authorization principal for autonomous work.
6. Agent User is used only for resources owned by the digital Worker.
7. Human OBO is per-user and per-turn; it is never ambient authorization for autonomous or shared-conversation work.
8. Platform-managed identity and short-lived tokens replace shared application keys.
9. Preview services are isolated behind small adapters so runtime code remains portable.
10. Local learning optimizes one Worker; shared learning changes only through reviewed Promotion.

## System context

```text
Microsoft Teams / Agent 365 / operator /invoke
                    |
                    v
       per-Worker bridge Container App
       - Microsoft 365 Agents SDK ingress
       - authorization-boundary envelope
       - Sandbox lifecycle and invocation
                    |
                    v
              ACA Sandbox
       +---------------------------+
       | OpenClaw or Hermes         |
       | Foundry token adapter      |
       | Agent Identity MCP adapter |
       +---------------------------+
          |          |          |
          |          |          +--> Work IQ Mail MCP
          |          +-------------> public shipments MCP
          +------------------------> private incidents MCP
                                      through customer VNet
```

## Deployment topology

### Shared platform layer

The platform Terraform state owns:

- the Sweden Central Foundry account, project, and `gpt-5.6-terra` Global Standard deployment;
- the Sweden Central Sandbox VNet, delegated subnet, Sandbox Group, and managed identity;
- the North Europe application VNet, internal private-MCP ACA environment, public bridge ACA environment, and Azure Container Registry;
- global VNet peering between the regional VNets;
- private MCP DNS linked to both VNets.

The split is intentional. Sweden Central remains the runtime and model region. North Europe hosts Container Apps because Azure rejected new Sweden Central managed environments with `ManagedEnvironmentCapacityHeavyUsageError`. Sandbox-to-private-MCP traffic stays private across the peered VNets. [ADR 0013](docs/adr/0013-regional-placement-and-capacity-fallback.md) records the regional selection and fallback order.

### Worker application layers

Every deployed Worker uses a separate Terraform workspace, bridge, Agent 365 platform blueprint, messaging endpoint, identity, Data Disk, and Sandbox lifecycle. Example workspaces are:

- `autopilot-openclaw`;
- `autopilot-hermes`;
- `autopilot-hermes2`.

Multiple Workers can use the same Git Role Blueprint and Role Release without sharing private state. A shared multi-Worker bridge is deliberately deferred by [ADR 0014](docs/adr/0014-per-worker-agent365-blueprints-and-bridges.md).

Each workspace owns:

- one bridge Container App and bridge managed identity;
- one private incidents MCP Container App;
- one public shipments MCP Container App;
- Worker-specific settings and image digests.

The current public shipments deployment is repeated per workspace. Moving shared public tools to a separate Terraform state remains a possible future simplification rather than an accepted design.

## Component responsibilities

### Bridge

The bridge:

- receives `/invoke` and Agent 365 `/api/messages`;
- translates activities into a runtime-neutral request contract;
- adds the authorization boundary: selected identity mode, invoking human, Agent Identity/User identifiers, and conversation privacy boundary;
- creates, resumes, or reuses the Worker Sandbox;
- serializes Hermes learning operations per Worker;
- forwards turns to the runtime port;
- returns messages and Teams reactions through Microsoft 365 Agents SDK.

Bridge Container Apps scale to zero. The full `/invoke`, Agent 365 message, reaction, Dreaming, or approval operation remains awaited inside the incoming HTTP request so the scaler observes active work.

The bridge does not:

- proxy MCP data-plane traffic;
- hold private MCP API keys;
- impersonate Agent User for arbitrary Microsoft 365 tools;
- provide ambient human OBO.

For governed Hermes learning, the bridge owns the Ed25519 approval private key. Workers and central review receive public keys only.

### Sandbox runtime

Both runtimes receive the same categories of configuration:

- Foundry endpoint and model deployment;
- runtime image and persistent Data Disk volume;
- Sandbox customer VNet connection;
- Agent 365 tenant, platform blueprint, Agent Identity, and Agent User identifiers;
- fixed upstream MCP endpoints and required scopes.

The runtime calls only loopback MCP endpoints. `autopilots_identity.mcp_proxy` acquires and refreshes upstream tokens without changing OpenClaw or Hermes authentication internals.

### Runtime ownership

OpenClaw and Hermes own agent behavior, tools, memory, and their native runtime state. The bridge remains a thin protocol, identity-boundary, and lifecycle adapter.

Hermes additionally owns the local Role Blueprint profile, native memory, progressive-disclosure skills, Work History, learning transactions, Dreaming, and Learning Packet preparation.

## Identity and authorization model

### Workload credential

The Sandbox Group system-assigned managed identity proves where code is executing. ACA Sandboxes expose it through `IDENTITY_ENDPOINT` and `IDENTITY_HEADER`; Azure Identity uses that endpoint.

The managed identity is not the business authorization principal.

### Agent Identity federation

```text
Sandbox Group managed identity
  -> api://AzureADTokenExchange token
  -> Agent 365 platform blueprint federated identity credential
  -> blueprint token with fmi_path=<Agent Identity client ID>
  -> Agent Identity token for the target resource
```

Custom MCP servers authorize:

- tenant and issuer;
- exact target audience;
- Agent Identity client or object identifier when the server is Worker-specific;
- application role such as `Incidents.Read.All` or `Shipments.Read.All`.

### Agent User

For Worker-owned Microsoft 365 data:

```text
managed identity -> platform blueprint -> Agent Identity exchange token
  -> user_fic for the fixed Agent User
  -> delegated Work IQ token with idtyp=user
```

Work IQ Mail acts as the Agent User mailbox, not as the invoking human. The Agent User needs an individual service license for every Microsoft 365 workload it accesses.

### Human OBO

OBO requires a token and consent for the invoking human. It is valid only for an explicit user-owned-resource request. A group chat does not make one user's delegated token group-wide, and Teams channel scope does not provide normal Teams SSO.

Human OBO is intentionally not implemented yet.

## Network boundaries

### Private MCP

`Microsoft.App/sandboxGroups/vnetConnections` attaches Sandbox network interfaces to the delegated subnet. It provides routing and private DNS only.

The private incidents MCP runs in an internal ACA environment with public network access disabled. The Sandbox calls it through the VNet. Entra authorization remains mandatory.

### Public MCP

The shipments MCP is a public HTTPS ACA endpoint with scale-to-zero. It accepts:

- Agent Identity application role `Shipments.Read.All` for direct runtime access;
- delegated `Shipments.Read` for Agent 365 BYO OAuth.

### Egress proxy

Sandbox egress inspection remains enabled. Python HTTP clients use the system certificate store through `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` because the Sandbox egress proxy terminates and re-establishes inspected TLS.

## MCP integration strategy

| Tool category | Preferred integration |
| --- | --- |
| Microsoft 365 | Agent 365 Tooling catalog and `ToolingManifest.json`; Agent User or explicit OBO. |
| Private custom MCP | Direct Sandbox VNet access plus Agent Identity application authorization. |
| Public custom MCP used by runtimes | Direct Agent Identity authorization through the Sandbox-local adapter. |
| Public custom MCP governance demonstration | Agent 365 BYO registration, admin approval, supported-client invocation, and Defender telemetry. |

Agent 365 BYO currently requires a public endpoint and supported-client connection/OAuth behavior. Approval and gateway initialization were proven; a raw generic MCP client does not implement the supported-client handshake.

## State ownership

| State | Owner |
| --- | --- |
| Azure resources | Terraform platform and application states. |
| Runtime filesystem | Sandbox Data Disk volume. |
| Role Blueprint source | Commit-pinned Git repository and `distribution.yaml`. |
| Active Hermes Worker profile | `/data/hermes/profiles/<role-blueprint>` on the Data Disk. |
| Worker manifest | `<profile>\local\worker.json`. |
| Personal Memory | `<profile>\memories\USER.md` and `MEMORY.md`. |
| Private Playbooks | `<profile>\skills\private\`. |
| Work History | `<profile>\state.db` plus Hermes session data. |
| Role Skills | `<profile>\skills\role\`, inherited from the Role Release and locally patchable. |
| Candidate Improvements | `<profile>\skills\candidates\`, scoped to the current Role Release. |
| Learning provenance | `<profile>\learning\records.jsonl`. |
| Agent 365 platform blueprint files | `.local\<worker>\agent365`. |
| Agent User and identity discovery state | `.local\<worker>\agent365\instance.*.json`. |
| Durable design rationale | `docs\adr`. |

Local state accelerates operation but must not be the only source of truth for cloud-object discovery.

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
7. The current implementation isolates each Worker behind its own Agent 365 platform blueprint, bridge, Terraform workspace, and Data Disk; this does not duplicate the shared Git Role Blueprint.

### Microsoft 365 and Agent 365

1. Agent 365 is the only Microsoft 365 packaging and messaging lifecycle.
2. Agent 365 Agent Users are provisioned digital teammates, not classic installed Teams bots.
3. The bridge must process Agent 365 Activity Protocol traffic through Microsoft 365 Agents SDK.
4. Direct messages, explicit mentions, and targeted activities are supported.
5. Unmentioned Teams channel messages are not assumed to be delivered.
6. Agent 365 notification workloads must use workload-appropriate response channels rather than being converted into Teams messages.
7. Teams targeted messaging is a distinct private-user boundary inside a group conversation and must not be treated as a public group turn.
8. A targeted request must produce a targeted response unless the user explicitly approves publication.
9. Targeted requests, responses, attachments, and derived context must not enter public conversation memory or be revealed to untargeted participants.
10. Targeted messaging must not be claimed until the Agent 365 package is confirmed to opt in with the supported equivalent of `supportsTargetedMessages`.

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

### Documents and attachments

1. Teams and Agent 365 attachment metadata must survive bridge normalization, including name, content type, content URL, document ID, comment ID, and originating workload when supplied.
2. Attachment access must use the narrowest valid identity boundary:
   - attachment-scoped Agents SDK authorization for files supplied in the current turn;
   - Agent User for Worker-owned or Worker-shared Microsoft 365 content;
   - explicit human OBO for private human-owned resources when implemented.
3. One user's delegated document access must never become ambient authorization in a group or channel.
4. OneDrive or SharePoint sharing URLs should use managed Work IQ document tools before introducing custom DOCX parsing or storage.
5. Work IQ Word preview capabilities are limited to creating documents, retrieving extracted content/comments, adding comments, and replying to comments. The product must not claim arbitrary existing-document body editing until a supported tool exists.
6. The bridge must enforce file count, size, MIME type, and extension limits before forwarding or staging content.
7. Unsupported, inaccessible, oversized, or unsafe documents must fail explicitly.
8. Raw documents and extracted excerpts are private Worker context and are excluded from Role Skills, Candidate Improvements, provenance, and Learning Packets.
9. Temporary attachment files must use Worker-private storage with bounded retention and must not be committed to the Role Blueprint.
10. Office comment notifications must reuse the same document access contract rather than creating a second attachment path.

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

### Design stance

Hermes is the Worker's local learning engine. Autopilots uses its native memory, progressive skill disclosure, `skill_manage`, background review, and curator rather than replacing them with a parallel learning runtime. The bridge exposes `/learn <instruction>` as an explicit constrained mode over those native tools.

Autopilots adds the boundaries Hermes does not provide across Workers:

- durable classification of private and potentially transferable adaptation;
- provenance linked to actual local skill changes;
- deterministic allowlists, privacy checks, and redaction;
- Role Release lifecycle for inherited and Worker-authored skills;
- Collective Learning Review across Workers;
- human-reviewed Git Promotion.

Local learning and Collective Learning Review have different goals:

- local learning optimizes one Worker and may be narrow, wrong, or private;
- Collective Learning Review compares Workers, generalizes patterns, rejects outliers, and proposes a future Role Release;
- only Worker Refresh replaces local release-scoped adaptations with reviewed shared behavior.

### Memory and learning planes

| Plane | Canonical store | Use | Worker Refresh | Learning Packet |
| --- | --- | --- | --- | --- |
| Personal Memory | `memories\USER.md`, `memories\MEMORY.md` | Frozen fresh-session injection | Preserve | Never |
| Private Playbook | `skills\private\<name>` | Progressive disclosure or slash command | Preserve | Never |
| Work History | Hermes SQLite sessions and indexes | Recall, search, and Dreaming evidence | Preserve | Never raw |
| Role Skill | `skills\role\<name>` | Inherited, progressive-disclosure behavior | Replace with next Role Release | Recorded local diff only |
| Candidate Improvement | `skills\candidates\<name>` | New reusable local behavior | Archive or retire | Artifact plus provenance |
| Learning provenance | `learning\records.jsonl` | Why governed behavior changed | Archive per Role Release | Matching eligible records |

All private stores remain private even when Dreaming uses them as evidence. Dreaming may derive a separate generalized Candidate Improvement, but it never moves, promotes, deletes, or exposes the private source.

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
4. Hermes may patch Role Skills and create Candidate Improvements through native `skill_manage` during ordinary foreground adaptation, explicit bridge `/learn`, or Dreaming.
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

### Explicit foreground learning

1. Ordinary turns may use Hermes-native memory and skill tools during the same model invocation.
2. Only an exact `/learn <instruction>` command or trusted `learningIntent: explicit` request metadata enters constrained learning mode.
3. The bridge strips the command prefix, marks the request as explicit learning, and runs one Hermes invocation inside the normal learning transaction.
4. The bridge must not infer learning intent from keywords in ordinary prose and must not launch a completed-turn second model pass.
5. Explicit learning remains blocking until reconciliation succeeds or fails so the response accurately reports persistence.
6. Failed governed learning is rolled back and reported; Dreaming remains the later multi-session opportunity to derive missed learning from Work History.

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

For every observation, Dreaming chooses one outcome:

| Observation | Allowed outcome |
| --- | --- |
| Critical personal preference or compact private fact | Add or consolidate Personal Memory |
| Rich assignment-specific fact or procedure | Create or patch a Private Playbook |
| Reusable correction to inherited behavior | Patch a Role Skill and append linked provenance |
| New reusable procedure or capability | Create a Candidate Improvement and append linked provenance |
| Already represented learning | No artifact change or duplicate suppression |
| Secret, raw customer data, noise, or weak speculation | Do not store |

## Collective Learning Review

```text
reviewed Role Release N
           |
           v
Workers receive Role Skills
           |
           v
Hermes learns locally in each Worker
       | private                    | reusable
       v                            v
Personal Memory,              Candidate Improvement
Private Playbooks,            or Role Skill patch
Work History                         |
       | excluded                    v
       |                    approved Learning Packet
       |                            |
       +----------------------------v
                   Collective Learning Review
                              |
                              v
                      Promotion pull request
                              |
                              v
                    reviewed Role Release N+1
                              |
                              v
                       Worker Refresh
```

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
7. The merger/judge must receive the reviewed current `SOUL.md`, distribution metadata, and Role Skills so it can patch existing behavior or avoid semantic duplication.
8. A new Role Skill must have a concrete progressive-disclosure trigger, remain executable when loaded alone, and define every field, state, verification gate, failure condition, and domain-specific escalation output it introduces.

### Promotion

1. Promotion must create a Git branch and pull request against the Role Blueprint source.
2. The pull request must be draft by default.
3. Human expert review is required before merge.
4. The next Role Release must be a strictly newer semantic version.
5. Git remains the source of truth for promoted Role Skills.
6. Automated Promotion review must keep agent reasoning read-only and use permission-separated safe outputs for labels, comments, and reviews.
7. Privacy, Role Blueprint alignment, learning evidence, and skill quality are independent semantic gates; they complement rather than replace deterministic signature, hash, schema, namespace, and version validation.
8. Automated reviewers must not mark a Promotion ready, merge it, or perform Worker Refresh.
9. Every semantic reviewer must consume the same deterministically prefetched PR-head metadata, diff, review history, and Role Blueprint snapshot; the base-branch checkout is not valid evidence of the proposed Role Release.

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

### Scheduled learning

1. Recurring Dreaming must execute outside the Hermes Sandbox session loop and enter through the bridge so a suspended Sandbox can be woken safely.
2. A scheduled cycle may run Dreaming and prepare a Learning Packet when transferable records exist.
3. A scheduled cycle must never approve, attest, export, promote, or merge a Learning Packet.
4. Per-Worker configuration must control enablement, initial delay, interval, focus, maximum records, retry limit, retry backoff, and packet preparation.
5. Scheduled cycles must serialize through the same Worker learning transaction as foreground work and manual Dreaming.
6. Status must expose timestamps, counts, current Role Release, last Dream summary, last prepared packet digest, and sanitized failures without private content.
7. A bridge-owned timer is permitted for classroom demonstrations but requires a non-zero bridge replica.
8. The current production scheduler is an Azure Container Apps scheduled Job calling a managed-identity-protected bridge endpoint; it receives no stored Worker or bridge keys.
9. A12 migrates Dreaming to a per-Worker Service Bus queue that directly scales the bridge under ADR 0015.
10. The scheduled ACA Job must remain until Service Bus Dreaming passes parity validation, then be removed.

### User-scheduled Worker tasks

1. Hermes native cron jobs and execution ledger are the canonical schedule and history.
2. User schedule prompts, skills, and delivery content remain on the Worker Data Disk and never enter Service Bus.
3. An Azure Hermes `CronScheduler` provider schedules only the next occurrence as a minimal Service Bus message.
4. A due message directly scales the isolated per-Worker bridge through a managed-identity KEDA rule.
5. The bridge must use PeekLock, automatic lock renewal, bounded concurrency, and explicit settlement.
6. Hermes job claims, execution ledger, and schedule revision checks must prevent duplicate execution under at-least-once delivery.
7. Updates and cancellation must invalidate stale messages even when Service Bus cancellation races with activation.
8. Scheduled results preserve their originating private/public delivery boundary.
9. Initial hosted schedules support autonomous Agent Identity/Agent User work, prompt jobs, and reviewed Role Skills; arbitrary scripts and durable human OBO are excluded.
10. Worker Refresh preserves schedule state, provider reconciliation metadata, and execution history.

### Repeatable demonstration cohorts

1. A full lifecycle replay must use dedicated disposable Workers, identities, Terraform workspaces, Sandboxes, and Data Disks whose names begin with `demo-`.
2. Demo Workers pin an immutable baseline Role Release and never share private state with long-lived Workers.
3. Reset deletes only the disposable demo Sandboxes and Data Disks, then recreates them from the baseline on the next invocation.
4. Reset must fail closed unless every selected Worker, volume, and workspace is explicitly marked as demo-owned.
5. A demo Promotion uses a disposable Git branch or is closed without merge; main Role Release history must not be rewritten for repeatability.
6. Long-lived Workers use normal forward-only Worker Refresh and are never reset to an older Role Release.

## Observability

1. Bridge diagnostics record the selected authorization mode and privacy boundary, never tokens.
2. Agent 365 Observability permission is configured on every platform blueprint.
3. ACA and Sandbox diagnostics cover bridge, runtime, networking, and MCP behavior.
4. Agent 365 BYO gateway execution is observable through Microsoft Defender when invoked from a supported client.
5. System snapshots must redact Azure, Entra, Agent 365, and local secrets before comparison or retention.
6. Learning diagnostics expose transaction state, quarantine, reconciliation, provenance, packet validation, and Worker Refresh receipts without revealing private artifact content.

## Current product constraints

- ACA Sandboxes, Agent 365 AI teammates, reactions, and BYO MCP include preview surfaces.
- Teams delivers direct messages, explicit mentions, and targeted activities, not every unmentioned channel message.
- Agent 365 BYO MCP requires supported-client connection and OAuth behavior; a raw generic MCP client is insufficient.
- Human OBO is not implemented.
- OpenClaw does not yet implement the complete Hermes Role Blueprint and Collective Learning Review lifecycle.
- Multi-Worker Collective Learning Review is live-validated with two independent Worker packets.
- Scheduled Dreaming runs through a managed-identity ACA scheduled Job; migration to the unified Service Bus bridge trigger is not yet implemented.
- Agent 365 Email and Office comment notifications are not yet implemented.
- Teams and Agent 365 document attachment ingestion is not yet implemented.
- Work IQ Word is preview and currently lacks arbitrary in-place Word body editing.

## Non-goals

- General-purpose autonomous runtime swarms.
- m:n group-chat agent behavior.
- Distributed skill learning without human review.
- Direct Worker writes to the central Role Blueprint.
- A custom central database as the source of truth for Role Skills.
- Replacing managed identity and Entra authorization with stored credentials.
