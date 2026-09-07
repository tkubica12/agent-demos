# Hermes Worker contracts

Hermes 0.19.0 implements a Junior Project Manager Worker: delivery coordination, document collaboration, scheduled work, and governed learning. [Deployment](DEPLOYMENT.md) contains operator procedures; [Demo](DEMO.md) contains prompts.

## Domain

| Term | Contract |
| --- | --- |
| Role Blueprint | Git definition of a job: instructions, reviewed skills, tools, and distribution metadata. |
| Role Release | Immutable semantic version pinned to a full Git commit. |
| Worker | Named deployment with its own Agent 365 identity, assignment, Data Disk, and local adaptations. |
| Candidate Improvement | Local reusable skill or inherited Role Skill patch; not shared until Promotion. |
| Dreaming | Fresh-session reflection over private Work History; may change memory or skills, or store nothing. |
| Learning Packet | Content-addressed bundle of eligible skill changes, provenance, and proposed tests. Export requires signed approval. |
| Collective Learning Review | Comparison of compatible Worker packets, evidence, conflicts, and role fit. |
| Promotion | Human-reviewed Git pull request producing a newer Role Release. |
| Worker Refresh | Transactional adoption of that release while preserving private state. |

The Git Role Blueprint and the Agent 365 platform blueprint are different objects. The latter binds one Worker's Microsoft 365 endpoint and permission envelope.

## Hosting and ownership

Shared Terraform infrastructure supplies Foundry, ACR, networking, private DNS, an Express managed environment for private ingress, Service Bus infrastructure, and keyless monitoring. Foundry and Sandboxes use Sweden Central; ACR and the application network use North Europe, connected through VNet peering.

Each Worker has a separate Terraform workspace, Agent 365 platform blueprint, Agent Identity, Agent User, and five Sandbox Groups with five user-assigned managed identities:

| Group | Responsibility |
| --- | --- |
| `gateway` | Agent 365 `/api/messages`, operator `/invoke`, runtime lifecycle, Teams delivery, and continuous Service Bus receive. Auto-suspend is disabled. |
| `runtime` | One Hermes writer, OnDemand compute, persistent Data Disk, native tools, memory, learning, and cron state. |
| `private-mcp` | Incidents MCP, private ingress through the linked Express environment, application-level Entra authorization. |
| `public-mcp` | Shipments MCP, public HTTPS, application-level Entra authorization. |
| `generated-apps` | Separate child Sandbox per published app; participant-authenticated ports and native lifecycle. |

The gateway does not proxy model inference, MCP traffic, or generated-app traffic. An Agent 365 HTTP acknowledgement can precede completion of detached work; gateway availability must cover that work and queue receive. Runtime and generated-app suspension are independent.

Unchanged `Stopped` or `Suspended` Sandboxes resume with the same ID. Terminal `Failed` instances are replaced. Image/configuration changes may require replacement; the runtime Data Disk remains separate. A profile is single-writer because it contains SQLite and transactional skill state.

Deployment explicitly separates Terraform resources, identity reconciliation, image conversion, service creation, and endpoint capture. It does not use Terraform `local-exec`. Linking a Group to the private-ingress environment is irreversible.

## Identity and network boundaries

| Operation | Principal and enforcement |
| --- | --- |
| Foundry inference | Runtime managed identity and model-resource RBAC; native Hermes `azure-foundry` Entra token callback. |
| Autonomous custom MCP | Managed identity → blueprint federation → Agent Identity token; server validates issuer, tenant, audience, Worker binding, and app role. |
| Worker-owned Microsoft 365 | Agent Identity exchange → `user_fic` for the fixed Agent User → delegated Work IQ/Graph token. |
| Agent 365 ingress | Microsoft 365 Agents SDK validates Activity Protocol authentication. |
| Native cron Service Bus send | Worker Agent Identity with Data Sender, federated from runtime managed identity. |
| Document-retry Service Bus send and queue receive | Gateway managed identity with Data Sender/Data Receiver; no Agent Identity exchange. |
| Generated-app lifecycle | Gateway managed identity with Data Owner on that Worker's generated-app Group. |
| Generated-app user access | Native Entra-authenticated port with explicit participant allowlist. |

`autopilots_identity.mcp_proxy` is the required loopback-only Agent Identity/Agent User federation adapter. It acquires and refreshes fixed upstream MCP tokens; it is not a model proxy or human OBO implementation.

Private network access and authorization are independent. Runtime VNet attachment supplies routing/DNS; the Express environment's Private Endpoint restricts incidents ingress. Gateway and MCP native ports allow anonymous HTTPS transport, but their applications authenticate requests. Shipments accepts `Shipments.Read.All` application access or the separate `Shipments.Read` delegated BYO flow.

Sandbox egress inspection remains enabled. Python clients trust the system certificate store through `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`; disabling TLS checks is not an authentication remedy.

Registry conversion is deployment-only: the deployer obtains a short-lived Entra-derived ACR token, prepares runtime/gateway/private-MCP/public-MCP disk images, and passes ready image IDs to workloads. Gateway runtime startup uses `AGENT_RUNTIME_DISK_IMAGE_ID`. Workloads have no ACR credentials, conversion code path, or registry RBAC. ACR admin stays disabled. [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md) records this credential exception.

## Messaging, events, and sessions

- Agent 365 Agent Users are the only Teams packaging path; no companion bot is installed.
- Personal messages and explicit mentions are invocation surfaces. Delivered replies, reaction events, and message updates are handled; SDK handlers do not imply that Teams delivers every event.
- When observation is enabled, added reactions to remembered agent-authored messages may invoke Hermes. Removed reactions do not invoke it. Message updates record diagnostics only; there is no delete-event handler.
- The bridge adds/removes temporary `eyes` on eligible public group requests and sends typing indicators in personal/group chats. Hermes requests semantic reactions through `TEAMS_REACTION: eyes|like|heart|smile|surprised|check`; the bridge strips control lines and executes them. Undirected thanks after an agent response can receive a direct `like` acknowledgement. `NO_RESPONSE` suppresses visible text.
- Bridge context is an in-process bounded record of delivered events, not a channel subscription or durable history service.
- Hermes transcript IDs rotate hourly by default. `HERMES_API_SESSION_ROTATION_HOURS` supports 1–24 hours; `HERMES_API_SESSION_RESET_HOUR_UTC` aligns buckets.
- Stable `X-Hermes-Session-Key` includes Worker, source, authenticated user, and conversation/thread hash. `X-Hermes-Session-Id` is conversation/thread-based and time-bucketed. Group transcripts are shared despite user-specific memory keys.
- `/new` and `/reset` create a distinct native session generation; native inventory preserves that selection across gateway restarts. Personal Memory and Work History remain on disk.
- Email and Word/Excel/PowerPoint comment notifications use `/api/messages` and reply through the originating workload, not an arbitrary Teams channel.

## Persistent state

The active profile is `/data/hermes/profiles/<role-blueprint>`.

| State | Profile-relative location | Refresh | Packet |
| --- | --- | --- | --- |
| Personal Memory | `memories\USER.md`, `memories\MEMORY.md` | Preserve | Exclude |
| Private Playbooks | `skills\private\<name>\` | Preserve | Exclude |
| Work History | `state.db` and native session data | Preserve | Exclude raw content |
| Role Skills | `skills\role\<name>\SKILL.md` | Replace | Recorded local diff |
| Candidate Improvements | `skills\candidates\<name>\SKILL.md` | Archive/retire | Artifact and provenance |
| Provenance | `learning\records.jsonl` | Archive per release | Cumulative eligible chain |
| Worker manifest | `local\worker.json` | Update atomically | Identity/release bindings |
| Cron and delivery state | Native cron files and execution ledger | Preserve | Exclude |

Personal Memory is a bounded frozen snapshot injected at fresh-session start. Its files and Private Playbooks belong to the Worker profile, not separate per-caller profiles. A user-specific session key does not split these stores. Skills use progressive disclosure; transferable skills are restricted to one `SKILL.md`. Skill basenames are unique across namespaces.

The distribution includes only declared role-owned files: `distribution.yaml`, `SOUL.md`, configuration, MCP definitions, Role Skills, schemas, and optional reviewed cron definitions. Credentials, memories, private/candidate skills, sessions, workspace, caches, and learning state are excluded. The Worker manifest records the source, commit, release, owned paths, and Role Skill baseline hashes.

## Learning and review

### Local transaction

1. Snapshot governed skills and serialize changes per Worker.
2. Run one native Hermes turn. Ordinary turns may learn; exact `/learn <instruction>` or trusted explicit-learning metadata selects constrained mode without a keyword-triggered second model call.
3. Validate changed artifacts, privacy, and provenance. Any rejection accompanying governed changes restores the entire governed snapshot and appends no records; accepted changes append provenance atomically. Ordinary private writes remain separate, while a namespace-validation failure restores all skill namespaces.
4. Recover interrupted transactions atomically. Quarantine asynchronous/direct-CLI drift, restore committed state, then reconcile safe generalized changes through a bridged turn or Dreaming.

The runtime process owns the lease: concurrent live writers are rejected; a replacement runtime recovers interrupted predecessor state. Private memory/playbook changes stay local. Attachment-derived learning is separately blocked and restored.

### Transferable contract

- Provenance **3.0** records classification, stage, action, Worker/release, before/after hashes, generalized rationale, redacted evidence, confidence/privacy outcome, and synthetic `agentProposedScenarios`.
- `artifactPath` names `skills/role/<name>` or `skills/candidates/<name>`. `artifact.path` matches it; `changedFiles` is exactly `["<artifactPath>/SKILL.md"]`.
- Learning Packet **2.0** contains only allowed skill creates/patches and the cumulative provenance chain from release baseline to current hash. No scripts, references, symlinks, binary payloads, credentials, private source, or other artifacts.
- Agent-proposed scenarios use declarative `response.text` criteria. Signed scenarios retain their authorship; they are not independent holdouts.
- Human approval binds the exact digest, Worker, release commit, and governed-state hash. The gateway holds the Ed25519 private key; runtime and review receive public keys only.
- Central review rejects modified, stale, unsigned, unknown-Worker, or incompatible-release packets. It privacy-scans every model-visible field and decision.
- The judge receives reviewed role context, identifies support/conflicts/outliers, and proposes only `skills\role\<name>\SKILL.md` changes.
- Promotion is a draft Git pull request by default. Deterministic validation and independent semantic privacy, role-alignment, evidence, and skill-quality gates support human review; automation does not merge or refresh Workers.

The evaluation runner invokes real Hermes in isolated baseline/candidate profiles with identical non-skill state and model settings. An independent suite is mandatory. It checks literal response text with skill content supplied in prompts; tools, hooks, plugins, MCP, and background learning are disabled. Report independent cases separately from agent-proposed cases.

### Refresh

Refresh is forward-only to a newer semantic release and immutable commit. Local governed changes require either signed approval permitting export or a signed `reject_and_refresh` disposition permitting discard, not export. Preflight validates that exact state before replacing compute. An interrupted profile update restores the previous profile; private state survives and old release-scoped candidates leave the active skill tree.

## Scheduling and Dreaming

Hermes cron state is canonical on the Data Disk. Its Azure `CronScheduler` provider schedules only the next occurrence in a per-Worker Service Bus queue. Messages contain type, Worker/job identity, revision, and due time—not prompts, skill text, document content, tokens, or delivery credentials.

The awake gateway uses PeekLock, lock renewal, bounded concurrency, and explicit settlement. It wakes/reuses runtime compute; revision checks and durable execution receipts prevent a stale/redelivered occurrence from rerunning the task. Hosted jobs allow prompts and reviewed Role Skills, not arbitrary scripts or durable human delegation.

Proactive Teams work needs an existing bound conversation reference. A durable activity ID acknowledges delivery. Execution and external delivery are separate: Teams can accept a message before its local receipt is persisted, so crash recovery can duplicate delivery.

Reserved `system.dream` uses fenced checkpoints for invocation, durable response, reconciliation, packet preparation, and completion. Persisted completed responses replay without new model/tool execution. An ambiguous `dream_started` stops for inspection. Operator run-now has a distinct occurrence and does not advance the production schedule. A no-change Dream can complete with `recordCount=0` and `packet=null`.

Dreaming can prepare but cannot approve, export, promote, or merge. `hermes2` enables user scheduling and Service Bus Dreaming; `hermes` disables both.

## Documents and interactive output

### Office and attachments

- Teams ingestion accepts up to 20 DOCX/UTF-8 TXT files, 50 MiB each, 300 MiB per turn, and 1,000,000 extracted characters. Connector credentials remain on the original connector origin; inaccessible or unsupported content fails explicitly.
- Attachment turns use private context and transactionally restore memory, private playbooks, and governed skills. Raw bytes/excerpts never enter learning exports.
- Work IQ supplies discovery, creation, semantic reads, comments, replies, sharing, Mail, Teams, and Calendar. Graph under Agent User supplies deterministic access, ETags, same-item publication, attribution, and Excel range writes.
- Reviewed, image-pinned MiniMax Office tooling performs local package edits. The loopback collaboration MCP wraps tools rather than implementing a second Office editor.
- Download privately, inspect, apply the requested edit, verify semantics, validate Open XML against Microsoft 365, then publish with the source ETag. Existing validation defects form a private baseline; new defects block publication. Cleanup follows completion.
- Retained locked publishes are private, conversation-scoped operations. Staging TTL defaults to one hour; configuration is bounded to 5 minutes–24 hours. Internal IDs never become user-visible content or learning.
- Persistent locks offer **Keep trying original** or **Send shared copy now**. Authenticated encrypted action tokens bind Worker, user, conversation, operation, and expiry.
- Choosing background retry starts a 24-hour retry deadline and extends staged-file expiry to one hour beyond that deadline for completion/cleanup. Deterministic `document.publish.retry` queue work uses no repeated model turns. Guarded edits may rebase; opaque transformations fail closed on changed sources.
- At the deadline or a terminal rebase conflict, the background choice produces an explicitly shared copy. Copy delivery verifies a user-specific write permission and removes an unshareable copy. A copy has independent comments/sharing/history.
- Mutation receipts retained seven days, delivery leases, and persisted Teams activity IDs handle redelivery; they do not make external sends exactly-once.
- Word comment requests stay in the comment thread. Failed body publication returns the exact proposed text, rationale, not-applied status, and a fresh-mention retry instruction.

### Cards and generated apps

Consequential Teams cards use reviewed typed actions; informational cards use a bounded display DSL. Hermes cannot invent raw action payloads. The gateway validates bindings/expiry, consumes actions idempotently, and passes only the visible choice back to Hermes.

Generated apps use one child Sandbox per app, deny-default egress, at most five active apps per Worker, and artifacts limited to 80 files/2 MiB. Native Entra participant ports are OnDemand; five idle minutes suspend compute and default deletion is 24 hours after suspension. Owner-bound **Keep 1h/6h/24h/72h** and **Delete** actions change native lifecycle directly, without an LLM or Service Bus. Updates preserve the logical app ID; failed updates preserve the working deployment. App traffic never traverses the gateway.

## Observability

Foundry external-agent registration identifies externally hosted compute. Azure Monitor export uses Entra authentication. Default spans contain timing, opaque correlation, model/tool names, and available usage—not prompts, arguments, results, documents, raw identities, or error payloads.

Native `llm_execution` and `tool_execution` callbacks are instrumented. A local metadata-only handoff keyed by explicit native session bridges executor-thread context loss. Session chat and explicit-session chat completions support this handoff. Ambiguous interrupted leases return `409`; recovery restarts **both runtime wrapper and native gateway**, not only the public gateway. Expiry stops attribution but does not prove the old executor stopped.

## Current limitations

| Area | Boundary |
| --- | --- |
| Fresh Worker bootstrap | Endpoint creation requires identity state while identity setup requires Agent 365 state. Existing-Worker updates work; fresh provisioning is not turnkey. |
| Registry conversion | SDK 0.1.0b4 managed-identity conversion returns `401 RegistryAuthFailed`; [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md) defines the deployment-only token workaround. |
| Teams privacy | Public group `@` discovery is available; Hermes `/` discovery is absent in this Agent User package. `agenticUserTemplates` has no `bots[]`; targeted private handling and human OBO are not implemented. Do not put private facts in shared group context. |
| Teams routing | Do not assume unmentioned channel messages, thread subscriptions, full history, or every supported SDK event reaches the Agent User. |
| Native tracing | `/v1/responses` creates its own session and is marked `autopilots.trace.correlation=missing`. Sandbox-origin egress preserves W3C TraceId but rewrites the parent through platform hops absent from Application Insights. |
| Runtime idle policy | Same-ID/Data-Disk resume works; automatic eight-hour idle suspension has not been independently validated. |
| Office | Work IQ Word lacks arbitrary existing-body editing; the reviewed local/Graph path supplies it. Direct PDF/image/XLSX/PPTX attachment ingestion is absent. Shared PowerPoint supports bounded text reads, not creation/body editing. |
| UI hosts | MCP Apps is not implemented in the Hermes Agent User conversation. Proactive card attachments and generated-app action replacement rendering have host limitations; use text/suggested actions and request fresh app inventory. |
| BYO MCP | Agent 365 registration/approval requires a supported client's connection/OAuth flow; raw generic MCP initialization is insufficient. |
| Evaluation | Response-only assertions do not measure native skill discovery, tool workflows, repeated-trial variance, or general learning quality. |

No swarms, direct Worker writes to shared Role Blueprints, automatic Promotion, or parallel central memory store.
