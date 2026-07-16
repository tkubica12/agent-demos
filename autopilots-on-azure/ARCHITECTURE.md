# Autopilots on Azure architecture

This document describes the durable system architecture. Deployment commands and troubleshooting remain in `README.md`; normative requirements are in `SPEC.md`; milestone execution remains in `PLAN.md`.

## Document map

| Document | Responsibility |
| --- | --- |
| `ARCHITECTURE.md` | Current system shape, trust boundaries, identity flows, networking, and component responsibilities. |
| `SPEC.md` | Normative product requirements and constraints. |
| `PLAN.md` | Delivery status, milestones, next work, and exit criteria. |
| `README.md` | Operator quick start, deployment, validation, troubleshooting, and cleanup. |
| `docs\runbooks\` | Detailed repeatable procedures for flows with multiple control planes or authentication steps. |
| `docs\adr\` | Why consequential architectural decisions were made and what would cause them to be reconsidered. |

## Architecture principles

1. Agent 365 is the only Microsoft 365 packaging and messaging lifecycle.
2. OpenClaw and Hermes are peer runtimes behind the same bridge and Sandbox contract.
3. The public bridge handles ingress and Sandbox lifecycle, not MCP data-plane proxying.
4. Private tools stay private; network reachability and authorization are separate controls.
5. The Agent Identity is the authorization principal for autonomous work.
6. The Agent User is used only for resources owned by the digital worker.
7. Human OBO is per-user and per-turn; it is never the default for autonomous or shared-conversation work.
8. Platform-managed identity and short-lived tokens replace shared application keys.
9. Preview services are isolated behind small adapters so runtime code remains portable.

## System context

```text
Microsoft Teams / Agent 365 / operator /invoke
                    |
                    v
       runtime-specific bridge Container App
       - Microsoft 365 Agents SDK ingress
       - auth-boundary envelope
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

The split is intentional. Sweden Central remains the runtime and model region. North Europe hosts Container Apps because Azure rejected new Sweden Central managed environments with `ManagedEnvironmentCapacityHeavyUsageError`. Sandbox-to-private-MCP traffic stays private across the peered VNets. Regional selection and fallback order are recorded in [ADR 0013](docs/adr/0013-regional-placement-and-capacity-fallback.md).

### Runtime application layers

Each runtime has a separate Terraform workspace:

- `autopilot-openclaw`;
- `autopilot-hermes`.

Each workspace owns:

- one bridge Container App and bridge managed identity;
- one private incidents MCP Container App;
- one public shipments MCP Container App;
- runtime-specific settings and image digests.

The current public shipments deployment is repeated per runtime workspace. Only the OpenClaw endpoint is registered as Agent 365 BYO MCP. Moving public shared tools into a separate shared tools state is a possible future simplification, not an accepted decision yet.

## Bridge responsibility

The bridge:

- receives `/invoke` and Agent 365 `/api/messages`;
- translates Teams activities into the runtime-neutral request contract;
- adds the authorization boundary: selected identity mode, invoking human, Agent Identity/User identifiers, and conversation privacy boundary;
- creates, resumes, or reuses the runtime Sandbox;
- forwards turns to the runtime port;
- returns messages and Teams reactions through Microsoft 365 Agents SDK.

Bridge Container Apps scale to zero. An incoming HTTP request wakes a replica, and normal `/invoke`, Agent 365 message, and reaction processing remains awaited inside that request so the HTTP scaler observes the work as active. Keeping one replica permanently running does not prevent ingress timeouts and is not required for Sandbox lifecycle operations.

The bridge does not:

- proxy MCP traffic;
- hold private MCP API keys;
- impersonate the Agent User for Microsoft 365 tools;
- provide ambient human OBO.

## Sandbox runtime contract

Both runtimes receive the same categories of configuration:

- Foundry endpoint and model deployment;
- runtime image and persistent data volume;
- Sandbox customer VNet connection;
- Agent 365 tenant, blueprint, Agent Identity, and Agent User identifiers;
- fixed upstream MCP endpoints and required scopes.

The runtime connects only to loopback MCP endpoints. `autopilots_identity.mcp_proxy` acquires and refreshes upstream tokens without changing OpenClaw or Hermes authentication internals.

## Identity and authorization model

### Workload credential

The Sandbox Group system-assigned managed identity proves where code is executing. ACA Sandboxes expose it through `IDENTITY_ENDPOINT` and `IDENTITY_HEADER`; Azure Identity uses that endpoint.

The managed identity is not the agent authorization principal.

### Agent Identity federation

```text
Sandbox Group managed identity
  -> api://AzureADTokenExchange token
  -> Agent 365 blueprint federated identity credential
  -> blueprint token with fmi_path=<Agent Identity client ID>
  -> Agent Identity token for the target resource
```

Custom MCP servers authorize:

- tenant and issuer;
- exact target audience;
- Agent Identity client/object identifier when the server is runtime-specific;
- application role such as `Incidents.Read.All` or `Shipments.Read.All`.

### Agent User

For worker-owned Microsoft 365 data:

```text
managed identity -> blueprint -> Agent Identity exchange token
  -> user_fic for the fixed Agent User
  -> delegated Work IQ token with idtyp=user
```

Work IQ Mail uses `Tools.ListInvoke.All` and acts as the Agent User mailbox, not as the invoking human.

### Human OBO

OBO requires a token and consent for the invoking human. It is valid only for an explicit user-owned-resource request. Group chat does not make one user's delegated token group-wide, and Teams channel scope does not provide normal Teams SSO.

OBO is not implemented in A7.

## Network boundaries

### Private MCP

`Microsoft.App/sandboxGroups/vnetConnections` attaches Sandbox network interfaces to the delegated subnet. It provides routing and private DNS only.

The private incidents MCP runs in an internal ACA environment with public network access disabled. The Sandbox calls it directly through the VNet. Entra authorization remains mandatory even though the network is private.

### Public MCP

The shipments MCP is a public HTTPS ACA endpoint with scale-to-zero. It accepts:

- Agent Identity application role `Shipments.Read.All` for direct runtime access;
- delegated `Shipments.Read` for Agent 365 BYO OAuth.

### Egress proxy

Sandbox egress inspection remains enabled. Python HTTP clients use the system certificate store through `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` because the Sandbox egress proxy terminates and re-establishes inspected TLS.

## MCP strategy

| Tool category | Preferred integration |
| --- | --- |
| Microsoft 365 | Agent 365 Tooling catalog and `ToolingManifest.json`; Agent User or explicit OBO. |
| Private custom MCP | Direct Sandbox VNet access plus Agent Identity application authorization. |
| Public custom MCP used by OpenClaw/Hermes | Direct Agent Identity authorization through the Sandbox-local adapter. |
| Public custom MCP governance demo | Agent 365 BYO registration, admin approval, supported-client invocation, and Defender telemetry. |

Agent 365 BYO currently requires a public endpoint and supported client connection/OAuth handling. Approval and gateway initialization were proven; a raw MCP client receives an empty tool catalog because it does not perform the supported-client handshake.

## State ownership

| State | Owner |
| --- | --- |
| Azure resources | Terraform platform/apps states. |
| Runtime filesystem | Sandbox Data Disk volume. |
| Role Blueprint source | Commit-pinned Git repository and `distribution.yaml`. |
| Active Worker profile | `/data/hermes/profiles/<role-blueprint>` on the Sandbox Data Disk. |
| Worker manifest | `<profile>/local/worker.json`; records Role Blueprint source, Role Release, commit, Worker identity, and assignment scope. |
| Personal Memory | `<profile>/memories/USER.md` and `MEMORY.md`; bounded Hermes-native memory injected as a frozen snapshot at session start. Permanent for the Worker and never exported. |
| Private Playbooks | `<profile>/skills/private/`; Hermes-native progressive-disclosure skills and references. Permanent for the Worker and never exported. |
| Work History | `<profile>/state.db`; messages, tool calls, and FTS indexes used by `session_search`. Private operational evidence and never exported raw. |
| Role Skills | `<profile>/skills/role/`; inherited from the Role Release and locally patchable. |
| Candidate Improvements | `<profile>/skills/candidates/`; Worker-authored reusable skills scoped to the current Role Release. |
| Learning provenance | `<profile>/learning/records.jsonl`; linked rationale and evidence for Role Skill diffs and Candidate Improvements. |
| Agent 365 blueprint/package files | `.local\<runtime>\agent365`. |
| Agent instance identifiers | `.local\<runtime>\agent365\instance.*.json`. |
| Entra MCP API discovery state | `.local\a7-*-mcp-api.json`, recoverable by display name. |
| BYO registration state | `.local\<runtime>\agent365\byo.public-shipments.json`, recoverable from Entra and Agent 365 catalog. |
| Durable design rationale | `docs\adr`. |

Local state accelerates operation but must not be the only source of truth for cloud object discovery.

## Hermes memory and Collective Learning Review architecture

### Design stance

Hermes is the local learning engine. Autopilots should use its native memory, progressive skill disclosure, `/learn`, `skill_manage`, background review, and curator rather than replacing them with a parallel learning runtime.

Autopilots adds the boundaries Hermes does not provide across Workers:

- durable classification of private versus potentially transferable adaptation;
- provenance and rationale linked to local skill changes;
- deterministic redaction and export allowlists;
- Role Release lifecycle for Role Skills and Candidate Improvements;
- Collective Learning Review across Workers into a reviewed GitHub pull request.

Local learning and Collective Learning Review are intentionally different:

- Local learning optimizes one Worker and may be wrong, narrow, or private.
- Collective Learning Review compares many Workers, generalizes patterns, rejects outliers, and proposes the next Role Release.

### Memory and learning planes

| Plane | Store | Loaded or used | Role Release behavior | Collective Learning Review |
| --- | --- | --- | --- | --- |
| Personal Memory: user profile | `memories/USER.md` | Frozen into every fresh session prompt | Survives every Role Release | Never exported |
| Personal Memory: compact notes | `memories/MEMORY.md` | Frozen into every fresh session prompt | Survives every Role Release | Never exported |
| Private Playbook | Hermes-native skill under the reserved private namespace, with optional references | Progressive disclosure by skill name and description | Survives Worker Refresh; user-directed `/learn` skills are curator-exempt | Never exported |
| Work History | `state.db` sessions, messages, tool calls, and FTS | `session_search`, foreground recall, and Dreaming evidence | Survives every Role Release | Raw content never exported |
| Role Skill | Git-backed skill inherited from the Role Release | Progressive disclosure during normal work | Locally mutable within one Role Release; replaced during Worker Refresh | Export local diff plus provenance |
| Candidate Improvement | Hermes-native skill under the reserved candidate namespace | Progressive disclosure in a fresh session | Scoped to one Role Release; archived or retired during Worker Refresh | Export artifact plus provenance |
| A9 legacy learning record v1 | `learning/records.jsonl` | Operator inspection and central candidate input; not ordinary task context | Scoped to the A9 release and archived | Exported during migration |
| A10 target: artifact provenance v2 | Extended learning journal linked to skill artifacts | Dreaming, operator inspection, and Collective Learning Review | Scoped to one Role Release and archived | Exported with artifact |

`USER.md` and `MEMORY.md` are official bounded Hermes memory. Rich private content belongs in a Private Playbook rather than an additional custom cache. A playbook can include `SKILL.md` for discovery and procedure plus reference files for customer, account, project, manager, or team facts.

All private stores remain private even when Dreaming uses them as evidence. Dreaming may derive a separate generalized Candidate Improvement, but it never moves, promotes, deletes, or exposes the Private Playbook source.

### Skills are the Worker's local behavior

The intended worker behavior is its effective skill tree, not the JSONL journal.

A10 allows Hermes to:

- patch an inherited Role Skill after discovering a better procedure;
- create a Candidate Improvement with `/learn`, `skill_manage`, or background review;
- create a Private Playbook containing assignment-specific knowledge or procedure;
- patch an existing Private Playbook as the assignment evolves.

An inherited Role Skill patch takes effect in the next fresh session. The Role Release commit remains the immutable baseline, so central tooling can compute an exact diff later. A new Candidate Improvement is also active locally after fresh-session discovery and is scoped to that Role Release. A Private Playbook is excluded from export and survives Worker Refresh.

Role Release is the only public replacement boundary. Before any Worker Refresh replaces Role Skills, eligible local diffs and provenance must have a durable export receipt.

The lifecycle requires explicit classification metadata outside free-form skill content. Each locally created or modified skill must be classified as one of:

- `private_playbook`;
- `candidate_improvement`;
- `role_skill`;
- `runtime_owned`.

Private details must not be patched into Role Skills or Candidate Improvements. If a procedure depends on a named customer or assignment, Hermes creates or patches a Private Playbook instead.

### What `learning/records.jsonl` means

The journal is not primary behavior memory and not a replacement for skill diffs. It is the provenance envelope explaining why a Candidate Improvement or Role Skill changed.

A9 schema v1 currently contains classification, title, generalized learning, rationale, redacted evidence summaries, confidence, a proposed target, record identity, timestamp, and privacy result. It has no exact artifact path binding, action, base commit, or before/after hash. A9 also uses the records as input to the generated aggregate `hot-learning` skill.

A10 provenance schema v2 identifies:

- record ID and timestamp;
- classification and confidence;
- affected skill or knowledge paths;
- create, patch, or delete action;
- Role Release commit and before/after content hashes;
- generalized learning and rationale;
- redacted evidence summaries and source types;
- privacy/redaction result;
- originating hot turn or dream run;
- optional relationships to earlier records or superseded attempts.

Collective Learning Review therefore receives both:

1. **What changed:** exact Candidate Improvement artifacts and Role Skill diffs against the recorded Role Release.
2. **Why it changed:** learning records with rationale, evidence, confidence, and provenance.

This lets the merger judge distinguish repeated successful adaptations from accidental edits, private overfitting, and unsupported local preferences.

Export is fail closed. A10 must:

- export only artifacts explicitly classified `candidate_improvement` or diffs of recorded Role Skill paths;
- exclude Private Playbooks, Personal Memory, Work History, credentials, logs, and workspace data before invoking any merger model;
- run source-aware DLP and redaction over both records and changed artifacts, not only regex checks over the journal;
- require a human-visible export summary and approval before packets leave the worker privacy boundary;
- never use `privatePathsExcluded` metadata alone as proof that exclusion occurred.

### Dreaming outputs

Dreaming is a regular offline reflection pass over Work History, outcomes, corrections, tool traces, Personal Memory, Private Playbooks, Role Skills, and Candidate Improvements. It can discover patterns that were not obvious during one foreground turn.

For every observation, dreaming chooses one output:

| Observation | Dream output |
| --- | --- |
| Critical personal preference or compact private fact | Add or consolidate Personal Memory |
| Rich assignment-specific fact or procedure | Create or patch a Private Playbook and its references |
| Reusable correction to inherited role behavior | Patch a Role Skill and append linked provenance |
| New reusable procedure or domain capability | Create a Candidate Improvement and append linked provenance |
| Already represented learning | No artifact change; record or report duplicate suppression |
| Secret, raw customer data, noise, or weak speculation | Do not store |

Foreground learning can produce the same artifact types. The difference is evidence horizon: a foreground turn sees an immediate explicit lesson, while Dreaming can compare several sessions and identify recurrence, failure patterns, and Promotion opportunities.

### Role Release and Collective Learning Review lifecycle

```text
reviewed Role Release N
              |
              v
Worker receives Role Skills
              |
              v
Hermes uses, creates, and patches skills locally
       |                         |
       | private                 | transferable candidate
       v                         v
Personal Memory/Playbooks      Candidate Improvement + provenance
       |                         |
       | excluded                v
       |                 central A10 exporter
       |                         |
       |               diffs + rationale from many Workers
       |                         |
       |               Collective Learning Review
       |                         |
       +-------------------------v
                   reviewed Role Release N+1
```

Before adopting Role Release N+1, the Worker exports Candidate Improvements and persists an approved export receipt. Worker Refresh then:

1. preserves Personal Memory, Private Playbooks, and Work History;
2. archives Role Release N provenance and Candidate Improvements;
3. replaces Role Skills with the reviewed Role Release N+1 baseline;
4. removes superseded release-local improvements;
5. starts fresh sessions against the new skill index.

The installer must reject Role Release rollback and must not replace release-local artifacts without a durable export/archive receipt. Reserved Role Skill, Private Playbook, Candidate Improvement, and runtime namespaces prevent a later Role Blueprint path from colliding with Worker-owned state.

Loss or modification of a Candidate Improvement is acceptable at this boundary: Collective Learning Review may reject it, generalize it differently, or replace it with stronger evidence. Loss of Personal Memory, Private Playbooks, or Work History is not acceptable.

### A9 migration into A10

A9 implemented a safe interim subset:

- foreground and dream turns emit bounded generalized candidate records;
- the runtime validates, redacts, deduplicates, and appends `learning/records.jsonl`;
- the runtime renders all accepted records into one generated `skills/hot-learning/SKILL.md`;
- Role Blueprint instructions keep private content out of the journal.

A10 in Role Release 3 retires the generated aggregate skill and connects Hermes-native skill creation and Role Skill patches to provenance and export. It provides:

- skill classification and provenance metadata;
- interception or auditing of `skill_manage`, `/learn`, and background-review writes;
- exact diff export against the Role Release commit;
- Private Playbook exclusion and redaction;
- release retirement for Candidate Improvements;
- preservation or mandatory export of Role Skill diffs before any distribution-owned replacement;
- fail-closed artifact allowlisting, source-aware DLP, human export approval, and durable export receipts;
- reserved Private Playbook, Candidate Improvement, Role Skill, and runtime namespaces;
- multi-worker merger/judge and reviewed GitHub pull requests.

Learning transactions are serialized both in the bridge and by a profile-local lease. The runtime snapshots governed skills before each turn, verifies post-turn artifacts against returned provenance, and atomically commits the journal plus governed-state ledger. Invalid or unprovenanced Role Skill and Candidate Improvement changes are rolled back. Asynchronous governed drift is quarantined for later Dreaming and restored to the last committed state.

Learning Packet approval is independent of the Worker. The bridge owns an Ed25519 private key and signs the exact prepared packet digest only after explicit operator approval. The Worker and central Collective Learning Review hold trusted public keys only. Worker Refresh validates the signed packet and current governed-state hash before the old Sandbox is deleted.

### Role Blueprint and Worker state boundary

Hermes uses one named profile per Worker. Git owns only Role Blueprint distribution paths, while the Sandbox Data Disk owns Personal Memory, Private Playbooks, Work History, Candidate Improvements, workspace, `.env`, and Worker metadata. Worker Refresh changes the pinned Role Release commit, recreates the Sandbox container, reuses the same Data Disk, and synchronizes only Role Blueprint-owned paths after an approved export receipt.

OpenClaw remains on its runtime-image package plus persistent data directories. It has no equivalent Git profile-distribution lifecycle in A8, so the project documents that exception rather than adding a parallel custom package manager.

## Observability

- Bridge diagnostics record the selected authorization mode and privacy boundary, never tokens.
- Agent 365 Observability permission is configured on each blueprint.
- ACA and Sandbox diagnostics cover runtime and network behavior.
- Agent 365 BYO gateway execution is observable through Microsoft Defender when invoked from a supported client.
- `scripts.snapshot_system` captures redacted Azure, Entra, Agent 365, and local state for comparison.

## Current constraints

- ACA Sandboxes and BYO MCP are preview surfaces.
- OpenClaw and Hermes do not natively implement Agent 365 client-assertion authentication.
- Agent 365 BYO invocation is not supported directly from these runtimes.
- BYO registration may require repair of CLI-generated service principals, grants, public-client settings, and admin assignments.
- Agent 365 admin consent must use the intended tenant account; Windows account broker can select the wrong tenant.
- The platform Terraform configuration contains a pending naming migration from legacy `openclaw-*` resources to `autopilots-*`; it is intentionally not applied as part of A7.

## Related decisions

- [ADR 0001](docs/adr/0001-standard-aca-bridge.md): standard ACA bridge.
- [ADR 0002](docs/adr/0002-teams-event-routing-and-reactions.md): Teams delivery boundaries.
- [ADR 0004](docs/adr/0004-agent-identity-before-workiq.md): identity before broad Work IQ use.
- [ADR 0007](docs/adr/0007-side-by-side-autopilot-deployments.md): runtime-specific workspaces.
- [ADR 0010](docs/adr/0010-candidate-improvements-and-collective-learning-review.md): Candidate Improvements and Collective Learning Review.
- [ADR 0012](docs/adr/0012-mcp-identity-and-governance.md): MCP identity and governance.
