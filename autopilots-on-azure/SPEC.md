# Autopilots on Azure specification

## Document role

This is the authoritative product and architecture specification for Autopilots on Azure.

**Evidence boundary (2026-09-06 22:33 CEST):** existing deployment, model, MCP, same-ID/Data-Disk resume, schedule, no-change Dream, and bounded evaluation results stand. All 49 reviewed native parents resolve; 105 audited spans contain metadata only. Controlled read-only requests isolate parent rewriting to the Sandbox-origin egress path; TraceId survives, but platform intermediate parents are absent from AppInsights. This is a native-platform waterfall limitation, not proof of every parent edge. Tool execution is not independent VNet-route or MI-authentication proof. Teams public `@` discovery is observed, targeted `/` is not; automatic eight-hour idle behavior and general learning quality remain unproven.

- [PLAN.md](PLAN.md) tracks delivery status, history, and future work.
- [DEPLOYMENT.md](DEPLOYMENT.md) contains deployment, update, validation, and cleanup procedures.
- [DEMO.md](DEMO.md) contains classroom and product demonstration scenarios.
- [`docs\adr`](docs/adr) records consequential decisions.
- [`docs\runbooks`](docs/runbooks) contains repeatable multi-control-plane procedures.

## Product intent

**ACR/MCP lifecycle evidence:** unauthenticated public calls return `401`; private external connections reset, not HTTP `403`. The probe is cleaned up; positive runtime-to-private-MCP VNet access remains unproven. Repeated live MCP deployment kept Sandbox IDs without registry login, verified with a fail-if-called guard. All ten workload `AcrPull` assignments were removed and ACR admin is `false`. SDK b4 native MI conversion still returns `401`; the accepted transient Entra `RegistryCredentials` path works for deployment-time conversion only. MCP lifecycle results do not prove runtime/gateway startup or full Worker parity.

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
       per-Worker gateway Sandbox
       - Microsoft 365 Agents SDK ingress
       - authorization-boundary envelope
       - Sandbox lifecycle and invocation
                    |
                    v
              ACA Sandbox
       +---------------------------+
       | OpenClaw or Hermes         |
       | native azure-foundry auth  |
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

- the Sweden Central Foundry account, project, and `gpt-5-6-terra` deployment;
- the Sweden Central Sandbox VNet and delegated subnet;
- the application VNet, linked Express ACA managed environment for private ingress, Private Endpoint, private DNS, and Azure Container Registry;
- global VNet peering between the regional VNets;
- private MCP DNS linked to both VNets;
- Log Analytics, keyless Application Insights, and the Foundry project monitoring connection.

Sweden Central remains the runtime and model region; the application-network/ACR placement remains separate. [ADR 0013](docs/adr/0013-regional-placement-and-capacity-fallback.md) records regional constraints. Express supplies the private ingress environment, not a classic Container App workload. Linking a Sandbox Group to that environment is irreversible: treat an incorrect link as a replacement operation, not a toggle.

### Worker application layers

Every deployed Worker uses a separate Terraform workspace, bridge, Agent 365 platform blueprint, messaging endpoint, identity, Data Disk, and Sandbox lifecycle. Example workspaces are:

- `autopilot-openclaw`;
- `autopilot-hermes`;
- `autopilot-hermes2`.

Multiple Workers can use the same Git Role Blueprint and Role Release without sharing private state. A shared multi-Worker bridge is deliberately deferred by [ADR 0014](docs/adr/0014-per-worker-agent365-blueprints-and-bridges.md).

Each workspace owns:

- five distinct Sandbox Groups and five user-assigned managed identities: `runtime`, `gateway`, `private-mcp`, `public-mcp`, and `generated-apps`;
- gateway and MCP service Sandboxes with native HTTPS ingress;
- a persistent runtime Data Disk and OnDemand Worker lifecycle;
- Worker-specific settings and image digests.

The public shipments service is repeated per Worker deliberately. Service-role identities must not collapse into a shared Worker/fleet identity. Infrastructure creation, OCI-to-disk conversion, service creation, and endpoint capture are explicit deployment stages; Terraform does not use `local-exec`.

## Component responsibilities

### Bridge

The bridge:

- receives `/invoke` and Agent 365 `/api/messages`;
- translates activities into a runtime-neutral request contract;
- adds the authorization boundary: selected identity mode, invoking human, Agent Identity/User identifiers, and conversation privacy boundary;
- creates, resumes, or reuses the Worker Sandbox; `Stopped` is a resumable state, not by itself a failure requiring deletion;
- serializes Hermes learning operations per Worker;
- forwards turns to the runtime port;
- returns messages and Teams reactions through Microsoft 365 Agents SDK.

The gateway Sandbox has auto-suspend disabled. It owns detached work after an Activity Protocol acknowledgement and a continuous Service Bus receiver; an HTTP response is not evidence that this work has finished. Runtime compute remains OnDemand. This is not full-system scale-to-zero, and no KEDA wake is claimed for a Sandbox gateway.

Native gateway ingress allows anonymous transport so Agent 365 SDK authentication can validate the actual activity protocol. MCP services likewise perform Entra authorization in their application. Anonymous ingress is not anonymous tool access. The bridge owns both addition and removal of temporary `eyes`; Hermes chooses semantic reactions, and the bridge executes them. Office behavior policy belongs in runtime skills, not a growing bridge prompt.

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

The runtime calls loopback MCP endpoints. `autopilots_identity.mcp_proxy` acquires and refreshes upstream tool tokens; it remains necessary and is not a human OBO implementation. Model inference is separate: Hermes 0.19.0 uses its native `azure-foundry` provider and an Entra token callback. The custom `foundry_token_proxy.py` has been removed. Live model/tool parity must pass before describing the upgrade as deployed.

### Runtime ownership

OpenClaw and Hermes own agent behavior, tools, memory, and their native runtime state. The bridge remains a thin protocol, identity-boundary, and lifecycle adapter.

Hermes additionally owns the local Role Blueprint profile, native memory, progressive-disclosure skills, Work History, learning transactions, Dreaming, and Learning Packet preparation.

## Identity and authorization model

### Workload credential

Each Worker/service-role Sandbox Group has a distinct user-assigned managed identity. ACA Sandboxes expose credentials through `IDENTITY_ENDPOINT` and `IDENTITY_HEADER`; Azure Identity selects the assigned identity. The runtime, gateway, each MCP service, and generated applications have separate permissions.

The managed identity bootstraps Azure infrastructure access and federation; it is not the business authorization principal for MCP or Microsoft 365 work. Native Foundry model inference separately uses the runtime managed identity and its model-resource RBAC.

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

The private incidents MCP is a Sandbox in a Group linked to an Express ACA managed environment. The environment's Private Endpoint and private DNS restrict ingress; no classic MCP Container App remains. Runtime VNet routing reaches that private hostname. The native port accepts anonymous transport, while the MCP application still requires the correct Entra audience, Agent Identity, and app role.

### Public MCP

The shipments MCP is a public HTTPS Sandbox service with application-level Entra authorization. It accepts:

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

1. Microsoft 365 ingress must terminate at the Worker's dedicated gateway Sandbox, with native HTTPS transport and Agent 365 SDK authentication.
2. OpenClaw and Hermes must run inside Azure Container Apps Sandboxes, not ACA Dynamic Sessions.
3. Sandbox runtime state must reside on a persistent Data Disk.
4. Gateway auto-suspend must remain disabled while detached post-ACK work and continuous Service Bus receive depend on that process. Runtime and generated-app lifecycles remain independent; full-system scale-to-zero is not a requirement of this accepted topology.
5. An incoming gateway request must wake or reuse the runtime Sandbox. Activity Protocol acknowledgement does not end the detached runtime operation or its delivery obligation.
6. Runtime-specific Terraform workspaces must prevent OpenClaw and Hermes app state from colliding.
7. Each Worker must have its own Agent 365 platform blueprint, gateway, Terraform workspace, Data Disk, and separate service-role Sandbox Groups/user-assigned identities. This does not duplicate the shared Git Role Blueprint or prove isolation between participants inside a shared group transcript.

### Microsoft 365 and Agent 365

1. Agent 365 is the only Microsoft 365 packaging and messaging lifecycle.
2. Agent 365 Agent Users are provisioned digital teammates, not classic installed Teams bots.
3. The bridge must process Agent 365 Activity Protocol traffic through Microsoft 365 Agents SDK.
4. Direct messages and explicit mentions are the primary Teams entry points. The September 6 UI recheck found public group `@` discovery but not Hermes `/`; no private content was sent. Targeted activity handling remains conditional on a verified Agent User inbound/outbound contract. This does not establish universal platform incompatibility or a pure UI bug.
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
11. Existing Office body edits must prefer ETag-protected same-item publishing so sharing, comments, mentions, tracked changes, and version history remain attached to one document.
12. A validated edit blocked by a transient Microsoft 365 lock may remain in Worker-private storage under an opaque operation ID for at most 24 hours and one hour by default. The identifier and private path must never be exposed to the user or persisted in memory or learning artifacts.
13. Lock recovery must retry only the retained publish in short bounded attempts. A reconstructible guarded edit may be reapplied to a newer source ETag; an opaque local transformation must fail closed when the source changed.
14. After bounded retries, the Worker must ask whether to retry the original, return an edited copy, or cancel. It must not silently create a copy or use checkout to block coauthors. For Word comment-originated work, the comment thread remains the primary review surface: a failed publish must return the exact proposed content, concise rationale, explicit not-applied status, and a fresh-mention retry instruction in that thread.
15. A fallback copy must be created under Agent User identity and returned through the originating Teams context with an explicit warning that it has independent sharing, comments, and version history.
16. Pending publishes must be bound to a hashed stable conversation scope. A new native transcript may recover matching internal operation IDs through that scope, but operation IDs and scope values must never appear in user-visible output, memory, Work History, learning records, or diagnostics.
17. Activity Protocol may acknowledge a Teams attachment before its agent callback finishes, so HTTP concurrency alone is not a processing lease. The bridge scale-down cooldown must exceed the maximum runtime turn timeout; the default is 900 seconds for a 600-second Hermes timeout. The bridge still scales to zero after the bounded cooldown.
18. Open XML validation must target Microsoft 365 rather than the Open XML SDK's Office 2007 default. Modern schema extensions are valid input, while genuine package or schema errors remain hard failures.
19. If native Hermes session finalization returns HTTP 5xx after tool execution, the bridge must poll the existing transcript for up to 120 seconds and deliver only a newly persisted assistant response. It must not rerun tools.
20. Existing Office files may contain Word-tolerated schema defects. The Worker must record a private validation baseline before editing and reject newly introduced errors; unchanged source errors may pass with explicit diagnostics and must not be silently repaired.
21. Learning transaction leases must identify the runtime process that owns them. A live process rejects concurrent turns; a replacement Sandbox restores and releases an interrupted predecessor transaction immediately instead of waiting for lease expiry.
22. An Agent 365 Activity Protocol endpoint must keep at least one lightweight bridge replica ready. ACA cold start can exceed the workload response window before application acknowledgement code runs. This does not keep the Worker Sandbox running; expensive agent compute still starts on demand and scales independently.
23. Teams transcript IDs must rotate hourly by default while the stable Hermes memory key remains unchanged. Large private document/tool results must not accumulate for a full day, and fixed document wrappers must not load redundant upstream skill text into every turn.
24. A fallback file created in Agent User storage must be explicitly shared with the invoking user before its URL is sent through Teams. Existing-file URL delivery is not evidence of access. The copy workflow must verify a user-specific Graph permission and remove an unshareable copy.
25. Persistent Office locks must expose sanitized Graph error classification and request correlation. Retry policy must distinguish known transient lock responses from unexplained or effectively permanent write failures.
26. User-facing lock guidance must state that WOPI locks can persist for 30 minutes and be refreshed by Microsoft 365 clients or services. A Graph `423 notAllowed` without holder metadata must be reported as holder unavailable, never attributed to a person.
27. A persistent document lock must produce predefined Teams suggested actions with two `Action.Submit` choices: background original retry or an immediate shared copy. Action tokens must be encrypted, authenticated, user/conversation/Worker-bound, expiring, and idempotent.
28. Background document retries must use a fixed runtime state machine and the existing per-Worker Service Bus queue, not repeated LLM turns. Queue messages must not contain document bytes, URLs, edit text, or delivery credentials.
29. Background state must remain private on the Worker Data Disk, safely rebase guarded edits on changed ETags, retry for at most 24 hours, then create and explicitly share a copy.
30. Document mutation is terminal only after a receipt is durable; user delivery is terminal only after Teams returns and the runtime persists an activity ID. A durable delivery lease must prevent concurrent or immediate duplicate sends across queue redelivery and repeated card actions.

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
3. Local governed changes require either an approved state-bound Learning Packet or an explicit, signed `reject_and_refresh` disposition before replacement. Rejection authorizes discard and refresh; it is not export approval and must not create a fake approved packet.
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
8. Governed artifacts contain only `SKILL.md`; scripts, auxiliary references, executable payloads, and unsupported files cannot enter this Promotion lane. Private Playbooks have a separate private contract.

### Learning provenance

Every governed Role Skill or Candidate Improvement change must have a schema-3.0 provenance record containing:

- record identity and timestamp;
- classification and source stage;
- exact skill-directory identifier and action;
- before and after content hashes;
- changed files;
- Role Release and Worker identity;
- generalized learning and rationale;
- redacted evidence summaries;
- confidence and privacy result;
- declarative `agentProposedScenarios` with synthetic input, setup assumptions, and `response.text` criteria.

`artifactPath` identifies the skill directory: `skills/role/<name>` or `skills/candidates/<name>`, not its `SKILL.md` file. `artifact.path` must equal that directory identifier. The runtime requires `artifact.changedFiles` to equal exactly `["<artifactPath>/SKILL.md"]`, and the governed artifact bundle contains only that one file.

The canonical journal is:

```text
<profile>\learning\records.jsonl
```

The journal explains *why* behavior changed. The skill tree is the actual Worker behavior.

Packet schema 2.0 retains the cumulative provenance chain from the Role Release baseline to the final artifact. Keeping only the newest matching hash would discard the reasons for earlier edits. Scenarios travel in that signed chain, but remain agent-authored proposals rather than independent proof.

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
3. Packet preparation must fail when an artifact lacks a valid cumulative provenance chain reaching its current hash.
4. Privacy checks must scan every artifact and every field sent to the merger/judge.
5. Human approval must bind the exact packet digest, Worker, Role Release commit, and governed-state hash.

### Approval and attestation

1. Workers must not possess the approval private key.
2. The bridge holds an Ed25519 approval private key.
3. Workers and central review use trusted public keys only.
4. Central review must reject unsigned, modified, stale, unapproved, or unknown-Worker packets.
5. Worker Refresh must verify the matching approval or signed rejection disposition before replacing governed state; only approval permits export.

### Behavioral evaluation

`scripts.evaluate_learning` invokes actual Hermes 0.19.0 for both baseline and candidate in isolated fresh sessions. The profiles must have identical configuration and private state outside the governed skill delta. A supplied approved packet must cover that exact delta. An independently supplied regression or holdout suite is mandatory; agent-proposed cases are reported separately.

The current runner is response-only: literal `response.text` assertions, skill bodies included in the prompt, and no tool, hook, plugin, MCP, or background-learning execution. It does not measure native skill discovery, tool outcomes, repeated-trial variance, or prove independent authorship. Summaries omit raw private responses. The completed local `.artifacts\role-330-evaluation-verified.json` records real Hermes 0.19 / `gpt-5-6-terra` Role 3.2-versus-3.3 results: baseline `3/4`, candidate `4/4`, zero regressions. All four cases are manually/operator-authored independent regression cases, with `independence=operator_declared` and `packetDigest=null`; they are not agent-proposed packet scenarios. This bounded response result is not statistical generalization or evidence of autonomous learning improvement.

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
7. A bridge-owned timer is permitted for classroom demonstrations; the gateway Sandbox is already non-suspending.
8. The production scheduler is the per-Worker Service Bus queue under ADR 0015; a reserved Hermes Platform Dreaming schedule emits `system.dream` messages consumed by the gateway's continuous receiver.
9. Queue-driven Dreaming uses the same Worker learning transaction and packet-preparation coordinator as manual Dreaming.
10. The former ACA scheduled Job and its dedicated authentication surface are removed.

### User-scheduled Worker tasks

1. Hermes native cron jobs and execution ledger are the canonical schedule and history.
2. User schedule prompts, skills, and delivery content remain on the Worker Data Disk and never enter Service Bus.
3. An Azure Hermes `CronScheduler` provider schedules only the next occurrence as a minimal Service Bus message.
4. The non-suspending gateway receives each due message using managed identity and wakes the OnDemand runtime; no Sandbox KEDA trigger is assumed.
5. The bridge must use PeekLock, automatic lock renewal, bounded concurrency, and explicit settlement.
6. Hermes job claims, execution ledger, and schedule revision checks must prevent duplicate execution under at-least-once delivery.
7. Updates and cancellation must invalidate stale messages even when Service Bus cancellation races with activation.
8. Scheduled results preserve their originating private/public delivery boundary.
9. Initial hosted schedules support autonomous Agent Identity/Agent User work, prompt jobs, and reviewed Role Skills; arbitrary scripts and durable human OBO are excluded.
10. Worker Refresh preserves schedule state, provider reconciliation metadata, and execution history.
11. Proactive Teams output requires a persisted filtered conversation reference from an existing installed conversation. Hermes execution is at-most-once per revision, while the final Teams send is at-least-once because Teams and Service Bus cannot share one transaction.
12. Dreaming checkpoints and fencing separate invocation, durable response, reconciliation, packet preparation, and completion. Replay a completed response instead of calling Hermes again. An ambiguous `dream_started` interruption stops for inspection rather than blindly rerunning side effects.
13. Operator run-now occurrences have a distinct identity and cannot consume or advance the production occurrence. The Teams send-to-receipt crash window remains; neither checkpointing nor Service Bus duplicate detection establishes exactly-once external delivery.

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
7. Foundry external-agent registration describes an externally hosted Worker; it creates no Hosted Agent compute. App Insights local-key authentication is disabled. The project connection uses `authType: ProjectManagedIdentity` and exact metadata key `ApplicationInsightsConnectionString`.
8. Default traces contain metadata, correlation, timings, model/tool identifiers, and available usage, not prompt or tool payloads. SDK, HTTP, model, tool-subprocess, and scheduler trace continuity must be measured live; infrastructure provisioning alone does not prove it.
9. `bridge.telemetry` exports metadata-only OpenTelemetry through Azure Monitor with Entra CLI/managed-identity authentication. External registration uses `ExternalAgentDefinition(otel_agent_id)`, preview-enabled project access, and a `get()` read-back; it creates no hosted runtime.
10. The Hermes 0.19.0 native plugin uses supported `llm_execution` and `tool_execution` middleware around the actual callbacks, including streaming model execution. Usage counts are metadata; prompts, tool arguments/results, and raw error payloads must not be exported.
11. Native gateway `run_in_executor` loses context variables and has no `traceparent` ingress hook. The wrapper therefore lends a short-lived, local metadata-only trace context keyed by a hashed explicit native session. `/api/sessions/{id}/chat` and `/v1/chat/completions` with `X-Hermes-Session-Id` can correlate through this handoff. `/v1/responses` creates its own native session and currently cannot use that mapping; missing handoff must be visible as `autopilots.trace.correlation=missing`, not presented as linked tracing.
12. An interrupted request retains its handoff lease because the old native executor may still be running. Context expiry ends attribution but does not release that lease for another request. Restart the Hermes runtime wrapper and native gateway before reusing the affected session; restarting only the public bridge does not stop that native executor. Never attribute old work to a new request merely to restore a continuous-looking trace.

Local integration now includes plugin activation at startup, fresh adapter/wrapper headers, native context handoff, HTTP `409` conflicts, and uncertain-`5xx` lease retention. The combined 87 adapter/runtime/bootstrap/native-helper tests passed, including installed Hermes 0.19.0 discovery, an OpenAI SDK callback with offline HTTP transport, and real native `read_file_tool` execution in another process. This proves local wiring/privacy, not live model inference or deployed trace continuity.

The external `autopilots-hermes` registration was created/read back and unchanged on repeat; registration alone is not ingestion proof. Full-day read-only Log Analytics verification now establishes exact smoke, MCP, cron, and ad-hoc Dream attribution. The earlier last-four-hours query excluded the morning MCP invocation; no access or ingestion blocker was found.

MCP trace `9e02cd67edc33364ab97058d1c3af139` contains six successful native model spans and three tool spans: `skill_view`, `mcp__private_incidents__list_services`, and `mcp__public_shipments__list_demo_shipments`. All nine native children share ingested runtime parent `e54a407438d6aca3` with propagated correlation. Hermes 2 user cron trace `04dcef1944ae211549ca1893cdd52309` separately links Service Bus schedule/process, `/cron/fire`, native chat, and ACK.

Ad-hoc Dream trace `7e85aa2783ca35e2779b39197d26aa1b` contains seven successful native model spans and 31 successful tool-execution spans (18 propagated, 13 nested in-process), not 31 proven unique calls or the production cron occurrence. All 38 Dream native parents resolve. Including initial smoke, MCP, and user cron, all 49 reviewed native parents are ingested. The latest 105-span privacy audit found only allowed metadata keys, zero raw identity fields or nonopaque IDs, no Data/Url/Message content, and no AppTraces/AppExceptions rows in scope.

Two real read-only requests isolate the transport-parent limitation. Local `urllib` to the runtime preserved supplied parent `f466c48ce55f3340` in trace `31a14ce5fa8362e199a951e20b09cb36`. Fresh uninstrumented `urllib` inside the gateway Sandbox supplied `6c756943513aba2b`, but the runtime recorded `e27aaa33d331e626` in unchanged trace `584cabe44d94e00671fa98ec16c11d82`; server span `6775da76297a6d45` returned `200` at `18:33:25 UTC` on September 6.

The changed parent is isolated to the Sandbox-origin egress path, not ingress alone or application-exporter span loss. The exact proxy implementation is unidentified. Application execution remains correlated through TraceId and opaque IDs, while platform intermediate parents are absent from AppInsights; the later normal invoke had 10 unresolved wrapper parents. No custom trace headers, fake parent spans, or egress-security bypass were added. Preserve this current native-platform waterfall limitation rather than presenting a complete parent tree. Live MCP attribution and native linkage do not independently establish VNet routing, MI authentication, or all-time privacy. Exact evidence is in [DEPLOYMENT.md](DEPLOYMENT.md).

Operator service deployment must exclude terminal `Failed` gateway/MCP instances from matching-image reuse and replace matching or stale failed instances. Unchanged matching `Stopped` or `Suspended` services remain eligible for same-ID resume; changed configurations may require replacement. Nineteen targeted Sandbox tests cover the operator correction. A real private-MCP redeployment retained its healthy ID and health `200`, which proves reuse rather than live failed-instance recovery. No image or endpoint rollout was needed for this operator-only change.

Existing-Worker modernization preserves the Agent365 blueprint, AgentIdentity, AgentUser, grants, and local state. Infrastructure-only apply precedes identity reconciliation; the next apply must propagate updated identity tfvars into `deployment_config` before service creation. First-time bootstrap remains unresolved: identity setup requires existing Agent365 state, while Agent365 setup requires a real endpoint absent from infrastructure-only output. Placeholder endpoints and copied identities are not supported substitutes.

## Reproducibility and known dependency risk

Hermes is pinned to 0.19.0 with committed Python `uv.lock` and npm lockfiles. Weekly Dependabot updates are reviewed, not deployed automatically. Public PyPI is blocked in this environment; the actual Hermes install and ACR build used `https://packagefeedproxy.microsoft.io/pypi/simple`. Frozen resolution must fail explicitly rather than silently changing source or version.

Both MCP Docker builds now use frozen `uv` locks through that feed. The private MCP image actually built in ACR; targeted local tests passed for private MCP (6) and public MCP (1). These are build/test results, not proof of deployed MCP authorization. SDK 0.1.0b3 `managedIdentityResourceId`, SDK 0.1.0b4, and REST `managedIdentityClientId` conversion attempts returned registry authentication failures. The reviewed upstream issue had no fix as of September 4; that does not establish that every remaining failure is SDK-only.

Historical classic ACA image pulls used MI, but historical Sandbox conversion used the ACR admin username/password through `RegistryCredentials`; the recorded historical `ensure_agent_sandbox` retrieves `passwords[0].value` through `az acr credential show`. On 2026-09-06 the user approved a deployer-held, short-lived Entra-derived registry token for deployment-time conversion only. It remains an explicit bearer credential with the deployer's effective registry permissions, not native MI conversion or the earlier admin-password mechanism.

The deployment helper prepares all four disk images—runtime, gateway, private MCP, and public MCP—before creating service workloads. The gateway receives `AGENT_RUNTIME_DISK_IMAGE_ID` to start/resume runtime from the prepared image. Direct `scripts.sandbox_run_runtime` callers supply `--disk-image-id`; the registry-managed-identity option has been removed.

Runtime/gateway neither acquire/store ACR credentials nor perform image conversion, and workload identities have no ACR role assignments. Deployment requires `Ready` images before workload changes and must not persist tokens in logs, arguments, files, Terraform state, or runtime settings. No renewal daemon, token cache/service, credential broker, or admin-password fallback is permitted. [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md) records the boundary. All four MCP services are now live and healthy; runtime/gateway deployment and authenticated Worker behavior still require verification.

As of 2026-09-06, `npm audit` reports two inherited high-severity findings through `image-size`. The reviewed latest versions are `pptxgenjs` 4.0.1 and `image-size` 2.0.2 with no published fixed version. Do not claim the image is vulnerability-free or force an older major dependency merely to suppress the audit result.

## Current product constraints

- ACA Sandboxes, Agent 365 AI teammates, reactions, and BYO MCP include preview surfaces.
- Direct messages and explicit mentions were demonstrated on the prior topology; targeted Agent User inbound remains unverified, and unmentioned channel delivery is not available.
- Agent 365 BYO MCP requires supported-client connection and OAuth behavior; a raw generic MCP client is insufficient.
- Human OBO is not implemented.
- OpenClaw does not yet implement the complete Hermes Role Blueprint and Collective Learning Review lifecycle.
- Prior multi-Worker Promotion and Microsoft 365 notification demonstrations do not prove parity on the new Sandbox workloads.
- Scheduled Dreaming and user schedules share the per-Worker Service Bus queue and always-running Sandbox gateway receiver; the former ACA scheduled Job is removed.
- Teams targeted private messaging remains deferred until receive/send support is verified for this Agent User package and SDK. Public `@` discovery but absent `/` discovery is a scoped observation, not universal incompatibility. A companion bot package is out of scope.
- Interactive UI is split by host contract: governed Adaptive Cards for Teams, and Hermes-generated authenticated web apps in child ACA Sandboxes for rich team experiences. MCP Apps remain deferred until the Hermes/Agent User client path supports the extension directly.
- Hermes supports bounded Teams DOCX and UTF-8 text attachment ingestion; OpenClaw rejects attachments until it has an equivalent private learning transaction.
- PDF and image attachment ingestion remain unsupported. A14 supports shared Excel workbook range collaboration and bounded shared PowerPoint text extraction, not direct Teams attachment ingestion for those formats.
- Work IQ Word is preview and currently lacks arbitrary in-place Word body editing.
- Group transcript sharing remains unchanged. The stable memory key includes the authenticated user, but the transcript does not gain a per-user split. Full private continuity isolation is an open design boundary under ADR 0017, not a proven consequence of key construction.

## Non-goals

- General-purpose autonomous runtime swarms.
- m:n group-chat agent behavior.
- Distributed skill learning without human review.
- Direct Worker writes to the central Role Blueprint.
- A custom central database as the source of truth for Role Skills.
- Replacing managed identity and Entra authorization with stored credentials.
