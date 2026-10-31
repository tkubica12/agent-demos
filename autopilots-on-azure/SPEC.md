# Hermes Worker contracts

Hermes 0.22.5, built from the official image pinned by digest, implements a Junior Project Manager Worker: delivery coordination, document collaboration, scheduled work, and governed learning. [Deployment](DEPLOYMENT.md) contains operator procedures; [Demo](DEMO.md) contains prompts.

## Domain

| Term | Contract |
| --- | --- |
| Role Blueprint | Git definition of a job: instructions, reviewed skills, tools, and distribution metadata. |
| Role Release | Immutable semantic version pinned to a full Git commit. |
| Worker | Named deployment with its own Agent 365 identity, assignment, Hermes home with durable backups, and local adaptations. |
| Candidate Improvement | Local reusable skill or inherited Role Skill patch; not shared until Promotion. |
| Dreaming | Fresh-session reflection over private Work History; may change memory or skills, or store nothing. |
| Learning Packet | Content-addressed bundle of owner-shared skill changes with their native ledger chain. Export requires signed approval. |
| Collective Learning Review | Comparison of compatible Worker packets, evidence, conflicts, and role fit. |
| Promotion | Human-reviewed Git pull request producing a newer Role Release. |
| Worker Refresh | Transactional adoption of that release while preserving private state. |

The Git Role Blueprint and the Agent 365 platform blueprint are different objects. The latter binds one Worker's Microsoft 365 endpoint and permission envelope.

## Hosting and ownership

Shared Terraform infrastructure supplies Foundry, ACR, networking, private DNS, an Express managed environment for private ingress, Service Bus infrastructure, and keyless monitoring. Foundry and Sandboxes use Sweden Central; ACR and the application network use North Europe, connected through VNet peering.

Each Worker has a separate Terraform workspace, Agent 365 platform blueprint, Agent Identity, Agent User, and five Sandbox Groups with five user-assigned managed identities:

| Group | Responsibility |
| --- | --- |
| `gateway` | Agent 365 `/api/messages`, operator `/invoke`, Teams delivery, continuous Service Bus receive, and generated-app publication. It calls the runtime only through `AGENT_RUNTIME_URL`. Auto-suspend is disabled. |
| `runtime` | One Hermes writer, OnDemand compute suspended to memory, Hermes home on the Sandbox disk with native backups on a Blob volume, native tools, memory, learning, and cron state. |
| `private-mcp` | Incidents MCP, private ingress through the linked Express environment, application-level Entra authorization. |
| `public-mcp` | Shipments MCP, public HTTPS, application-level Entra authorization. |
| `generated-apps` | Separate child Sandbox per published app; participant-authenticated ports and native lifecycle. |

The gateway does not proxy model inference, MCP traffic, or generated-app traffic. An Agent 365 HTTP acknowledgement can precede completion of detached work; gateway availability must cover that work and queue receive. Runtime and generated-app suspension are independent.

Unchanged `Stopped` or `Suspended` Sandboxes resume with the same ID. The runtime suspends to **Memory** after five idle minutes, so resume continues the running Hermes processes instead of starting them again, and mints fresh identity tokens on demand. The Hermes home (`/data/hermes`) lives on the Sandbox root disk, which suspension, resume and platform maintenance preserve. Native backups on a platform-managed Blob volume make it survive replacement or loss of the Sandbox ([Persistent state](#persistent-state)). A Data Disk volume would force Disk mode, which restarts Hermes on every wake. An image change replaces gateway and MCP Sandboxes, which changes their native URLs. A profile is single-writer because it contains SQLite and transactional skill state.

The `autopilots` Hermes plugin's keepalive wraps actual model and tool execution, including background review and Curator forks, in `try/finally` activity scopes. The runtime wrapper uses the same supervisor for mutating HTTP requests, including deterministic cron/document work. While active, each process sends periodic GETs at roughly 60-second intervals to its own published `/health`, using platform-provided `ADC_SANDBOX_ID` and `ADC_PROXY_DNS_SUFFIX`. Loopback requests, outbound model calls and local CPU work do not reset platform idle time. There is no keepalive queue, external controller, durable busy ledger or additional Azure permission. An operation expires after 30 minutes or when its owning thread exits; expiry and ingress failures are logged. This limit applies to one uninterrupted operation, not the total multi-step agent task. Detached subprocesses outside a tracked operation and future schedules do not keep compute awake.

Deployment owns runtime compute. After the gateway Sandbox publishes its native URL, deployment creates or reuses the runtime with that URL, the Worker backup volume and a fingerprint of every compute-defining setting. A changed fingerprint or Role Release replaces compute: a Role Release change first requires Worker Refresh readiness, then the old runtime publishes a final backup before it is deleted, and its replacement restores that backup. Deployment also replaces terminal `Failed` compute, which restores the newest automatic backup, then verifies `/health` through the public OnDemand ingress before writing `AGENT_RUNTIME_URL` into gateway settings.

The gateway has no runtime Sandbox Group role, Sandbox SDK runtime path, or runtime provisioning settings. Every gateway request, including Teams, document retry, Service Bus delivery and Dreaming, simply calls the runtime URL. OnDemand ingress wakes `Stopped` or `Suspended` compute; the adapter waits for wrapper `/health` and native `/v1/models` readiness. Missing or `Failed` compute returns an explicit error until deployment is rerun. Keepalive does not fix a long HTTP request timeout, guarantee crash recovery, or change Teams delivery.

Gateway, runtime and MCP ports all use `OnDemand`. A request to a stopped `Manual` port returns `403` instead of waking compute; this previously disabled both MCP servers whenever they had been idle for 30 minutes. Measured on 2026-10-31 in Sweden Central (2 vCPU, 2 GiB): the platform resumed a Sandbox in under a second in either mode, and a Memory resume reached the native Hermes API in 1.5–2.7 s, including three cycles on the deployed `hermes2` Worker. Disk mode needed 17–25 s, because every wake repeated the launcher, the official bootstrap, imports and plugins, and MCP discovery of eleven servers, which Hermes completes before opening its API. A new runtime Sandbox pays that full start once: the `hermes2` replacement restored a 24 MB backup and passed `/health` 12 s after creation.

A Service Bus message does not wake a Sandbox. The awake gateway is the queue consumer: it holds the PeekLock, renews it, checks schedule revisions and receipts, settles or dead-letters messages and posts Teams output through Agent 365. Its runtime call then wakes compute through ingress. Platform connector triggers are preview, do not list Service Bus as a Sandbox trigger source and do not provide this lock/settlement coupling, so none is attached to this queue.

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
| Runtime compute lifecycle | Deployer only. The gateway managed identity has no role on the runtime Sandbox Group. |
| Generated-app lifecycle | Gateway managed identity with Data Owner on that Worker's generated-app Group. |
| Generated-app user access | Native Entra-authenticated port with explicit participant allowlist. |

`autopilots_identity.mcp_proxy` is the required loopback-only Agent Identity/Agent User federation adapter. It acquires and refreshes fixed upstream MCP tokens, reusing one blueprint exchange and prefetching all scopes at startup; it is not a model proxy or human OBO implementation.

Private network access and authorization are independent. Runtime VNet attachment supplies routing/DNS; the Express environment's Private Endpoint restricts incidents ingress. Gateway and MCP native ports allow anonymous HTTPS transport, but their applications authenticate requests. Shipments accepts `Shipments.Read.All` application access or the separate `Shipments.Read` delegated BYO flow.

Sandbox egress inspection remains enabled. Python clients trust the system certificate store through `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`; disabling TLS checks is not an authentication remedy.

Registry conversion is deployment-only: the deployer submits native-v2 disk-image conversions that the runtime/gateway/private-MCP/public-MCP Sandbox Groups authenticate with their own managed identities (`AcrPull`), then passes ready image IDs to workloads. Runtime deployment uses `AGENT_RUNTIME_DISK_IMAGE_ID`. No registry token or admin credential exists; workloads have no conversion code path. [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md) records the decision.

## Messaging, events, and sessions

- Agent 365 Agent Users are the only Teams packaging path; no companion bot is installed.
- Personal messages and explicit mentions are invocation surfaces. Delivered replies, reaction events, and message updates are handled; SDK handlers do not imply that Teams delivers every event.
- When observation is enabled, added reactions to remembered agent-authored messages may invoke Hermes. Removed reactions do not invoke it. Message updates record diagnostics only; there is no delete-event handler.
- The bridge adds/removes temporary `eyes` on eligible public group requests and sends typing indicators in personal/group chats. Hermes requests semantic reactions through `TEAMS_REACTION: eyes|like|heart|smile|surprised|check`; the bridge strips control lines and executes them. Undirected thanks after an agent response can receive a direct `like` acknowledgement. `NO_RESPONSE` suppresses visible text.
- Bridge context is an in-process bounded record of delivered events, not a channel subscription or durable history service.
- Hermes transcript IDs rotate hourly by default. `HERMES_API_SESSION_ROTATION_HOURS` supports 1–24 hours; `HERMES_API_SESSION_RESET_HOUR_UTC` aligns buckets.
- Stable `X-Hermes-Session-Key` includes Worker, source, authenticated user, and conversation/thread hash. `X-Hermes-Session-Id` is conversation/thread-based and time-bucketed. Group transcripts are shared despite user-specific memory keys.
- `/new` and `/reset` create a distinct native session generation; native inventory preserves that selection across gateway restarts. Personal Memory and Work History remain on disk.
- Email and Word/Excel/PowerPoint comment notifications use `/api/messages` and reply through the originating workload, not an arbitrary Teams channel. An email gets exactly one reply: Hermes either sends it with Mail tools and returns `NO_RESPONSE`, or returns only the body that the bridge sends.

Teams currently receives a completed response through the Agents SDK, not raw model deltas. Personal/group chats show typing; eligible group requests also receive a temporary processing reaction. The bridge first removes provenance and UI control directives, reconciles learning and schedule bindings, then chooses text, a card, a reaction, or no visible reply. Streaming internal transport and streaming text to Teams are separate decisions.

As checked on 2026-10-30, [Teams streaming documentation](https://learn.microsoft.com/microsoftteams/platform/bots/streaming-ux) describes non-notifying informative updates and one progressively populated message in personal, group and channel conversations with current SDKs. It limits a conversation to one open stream and a stream to ten minutes, and excludes proactive messaging. This documents Teams capability, not verified support for this Worker's Agent 365 identity/SDK path. Keep UI streaming disabled until that path and final-response filtering are explicitly validated.

## Persistent state

The Worker home is `/data/hermes` on the runtime Sandbox root disk. Hermes 0.22.5 runs one gateway per home, so the Worker is the home itself rather than a named profile.

| State | Home-relative location | Refresh | Packet |
| --- | --- | --- | --- |
| Personal Memory | `memories\USER.md`, `memories\MEMORY.md` | Preserve | Exclude |
| Private Playbooks | `skills\private\<name>\` | Preserve | Exclude |
| Work History | `state.db` and native session data | Preserve | Exclude raw content |
| Role Skills | `skills\role\<name>\SKILL.md` | Replace | Shared diff from release baseline |
| Candidate Improvements | `skills\candidates\<name>\SKILL.md` | Archive/retire | Shared artifact |
| Skill change history | Native skill ledger and staged writes | Preserve | Ledger chain of shared changes |
| Owner decisions | `learning\receipts\`, `learning\share-choices.json` | Archive per release | Share choice |
| Worker manifest | `local\worker.json` | Update atomically | Identity/release bindings |
| Cron and delivery state | Native cron files and execution ledger | Preserve | Exclude |

Personal Memory is a bounded frozen snapshot injected at fresh-session start. Its files and Private Playbooks belong to the Worker, not separate per-caller profiles. A user-specific session key does not split these stores. Skills use progressive disclosure; transferable skills are restricted to one `SKILL.md`. Skill basenames are unique across namespaces.

The distribution includes only declared role-owned files: `distribution.yaml`, `SOUL.md`, configuration, MCP definitions, Role Skills, schemas, and optional reviewed cron definitions. Credentials, memories, private/candidate skills, sessions, workspace, caches, and learning state are excluded. The Worker manifest records the source, commit, release, owned paths, and Role Skill baseline hashes.

### Durability

The root disk is local to one Sandbox, so it is not the only copy. The runtime wrapper checks the home once a minute and, when anything a backup would keep has changed, runs native `hermes backup`. Logs, caches, heartbeats, locks and SQLite shared-memory files that idle Hermes rewrites by itself do not count as changes. `hermes backup` snapshots every SQLite database through the SQLite backup API and writes a complete zip into the home's own `backups` directory; only a complete archive is copied to the Worker's platform-managed `AzureBlob` Sandbox volume at `/backup` and published by rename. The newest 24 archives are kept. Staging is local because SQLite snapshots fail on the blobfuse mount.

A runtime starting with an empty home restores the newest archive with native `hermes import` before anything else touches the home, then checks that the restored manifest names the same Worker. A damaged or foreign archive, or a volume that cannot be listed, stops startup; a readable empty volume starts a fresh Worker. An existing home is never overwritten. Before deployment deletes a replaced runtime, it runs one more backup inside it, waiting for any automatic backup in progress. `/health` reports the last published archive and error.

| Event | Data kept |
| --- | --- |
| Idle suspend, resume, platform maintenance | Everything, including running processes |
| Image, setting or Role Release change | Everything up to the final backup before deletion |
| `Failed` or lost Sandbox | Everything up to the newest automatic backup, at most about a minute before the last change |
| Deleted Sandbox Group | Nothing; volumes belong to the group |

## Learning and review

### Native learning on the Worker

Hermes learns natively. Ordinary turns may update memory and skills, and a background review after a turn may add more; memory and skill nudges arrive every ten turns. The skill ledger records every skill change with before/after file hashes and contents. With `HERMES_SKILL_WRITE_APPROVAL`, skill writes are staged until the owner approves them. Automatic Curator runs are disabled.

The Worker owner (`worker_owner_id`, the Agent Identity sponsor) is the only person who reviews what was learned. After Dreaming, and on `/learning` or `/learned` in the owner's personal chat, the gateway sends a digest card: recent skill changes with diffs and **Undo** (native rollback), staged writes with **Approve**/**Reject**, and for Role Skill or Candidate Improvement changes **Share**, **Keep** or **Discard**. **Keep** copies the change to the private `<skill>-playbook` and restores the release baseline; **Discard** restores the baseline. Actions are encrypted, expiring tokens bound to the owner and conversation, and every decision is an idempotent receipt. Nothing is sent to group chats.

Attachment and persistence-disabled turns run in a separate native session marked `private:`. A `pre_tool_call` hook blocks `memory`, `skill_manage` and file writes into `skills/` or `memories/` there, including in the background review fork, which Hermes 0.22.5 cannot skip per request.

### Transferable contract

- A Role Skill or Candidate Improvement leaves the Worker only after the owner chose **Share** for its current content. A later change requires a new choice.
- Learning Packet **3.0** contains each shared skill's files, release-baseline hashes, artifact hash, native ledger chain from the release baseline to the current files, share decision and owner approvals of staged writes. File hashes are recomputed from disk; the ledger explains them.
- Every other governed change is excluded with a reason and stays local. Only one bounded UTF-8 `SKILL.md` whose frontmatter names its directory is transferable; scripts, references, binary payloads, credentials, memories, sessions and private playbooks never are. A privacy scan rejects addresses, identifiers, internal URLs, secrets and absolute paths.
- Human approval binds the exact digest, Worker, release commit, and governed-state hash. The gateway holds the Ed25519 private key; runtime and review receive public keys only.
- Central review rejects modified, stale, unsigned, unknown-Worker, or incompatible-release packets. It privacy-scans every model-visible field and decision.
- The judge receives reviewed role context, identifies support/conflicts/outliers, and proposes only `skills\role\<name>\SKILL.md` changes.
- Promotion is a draft Git pull request by default. Deterministic validation and independent semantic privacy, role-alignment, evidence, and skill-quality gates support human review; automation does not merge or refresh Workers.

The evaluation runner invokes real Hermes in isolated baseline/candidate profiles with identical non-skill state and model settings. An independent suite is mandatory. It checks literal response text with skill content supplied in prompts; tools, hooks, plugins, MCP, and background learning are disabled. Packets do not carry agent-proposed scenarios.

### Refresh

Refresh is forward-only to a newer semantic release and immutable commit. Local governed changes require either signed approval permitting export or a signed `reject_and_refresh` disposition permitting discard, not export. Preflight validates that exact state before replacing compute. An interrupted profile update restores the previous profile; private state survives and old release-scoped candidates leave the active skill tree.

## Scheduling and Dreaming

Hermes cron state is canonical in the Worker home and travels with its backups. Its Azure `CronScheduler` provider schedules only the next occurrence in a per-Worker Service Bus queue. Messages contain type, Worker/job identity, revision, and due time—not prompts, skill text, document content, tokens, or delivery credentials.

The awake gateway uses PeekLock, lock renewal, bounded concurrency, and explicit settlement. It wakes/reuses runtime compute; revision checks and durable execution receipts prevent a stale/redelivered occurrence from rerunning the task. Hosted jobs allow prompts and reviewed Role Skills, not arbitrary scripts or durable human delegation.

Proactive Teams work needs an existing bound conversation reference. A durable activity ID acknowledges delivery. Execution and external delivery are separate: Teams can accept a message before its local receipt is persisted, so crash recovery can duplicate delivery.

Reserved `system.dream` uses fenced checkpoints for invocation, durable response, reconciliation, packet preparation, and completion. Persisted completed responses replay without new model/tool execution. An ambiguous `dream_started` stops for inspection. Operator run-now has a distinct occurrence and does not advance the production schedule. A no-change Dream can complete with `recordCount=0` and `packet=null`.

Dreaming can prepare but cannot approve, export, promote, or merge. Both `hermes` and `hermes2` enable user scheduling and Service Bus Dreaming, each on its own queue.

## Documents and interactive output

### Office and attachments

- Teams ingestion accepts up to 20 DOCX/UTF-8 TXT files, 50 MiB each, 300 MiB per turn, and 1,000,000 extracted characters. Connector credentials remain on the original connector origin; inaccessible or unsupported content fails explicitly.
- Attachment turns use private context and transactionally restore memory, private playbooks, and governed skills. Raw bytes/excerpts never enter learning exports.
- Work IQ supplies discovery, creation, comments, replies, sharing, Mail, Teams, and Calendar through its per-server MCP servers. Graph under Agent User supplies deterministic reads, ETags, same-item publication, attribution, and Excel range writes. Do not read a file that may be edited through Work IQ Word: it can hold an Office lock for about 30 minutes that blocks every later save, including the owner's.
- Reviewed, image-pinned MiniMax Office tooling performs local package edits. The loopback collaboration MCP wraps tools rather than implementing a second Office editor.
- Download privately, inspect, apply the requested edit, verify semantics, validate Open XML against Microsoft 365, then publish with the source ETag. Existing validation defects form a private baseline; new defects block publication. Cleanup follows completion.
- Word edits are anchored to paragraph content, not only indexes. If another user saves first, nothing is overwritten: the result is `source_changed`, and a retry reapplies the retained edit to the latest version or fails closed when the target text changed. A 24-hour patch receipt stops a repeated request from applying the same edit twice.
- Retained locked publishes are private, conversation-scoped operations. Staging TTL defaults to one hour; configuration is bounded to 5 minutes–24 hours. Internal IDs never become user-visible content or learning.
- Persistent locks offer **Keep trying original** or **Send shared copy now**. Authenticated encrypted action tokens bind Worker, user, conversation, operation, and expiry.
- Choosing background retry starts a 24-hour retry deadline and extends staged-file expiry to one hour beyond that deadline for completion/cleanup. Deterministic `document.publish.retry` queue work uses no repeated model turns. Guarded edits may rebase; opaque transformations fail closed on changed sources.
- At the deadline or a terminal rebase conflict, the background choice produces an explicitly shared copy. Copy delivery verifies a user-specific write permission and removes an unshareable copy. A copy has independent comments/sharing/history.
- Mutation receipts retained seven days, delivery leases, and persisted Teams activity IDs handle redelivery. When Teams accepts a result but its receipt cannot be written, the receipt is reconciled later without resending. External sends are still not exactly-once.
- Word comment requests stay in the comment thread. Failed body publication returns the exact proposed text, rationale, not-applied status, and a fresh-mention retry instruction.

### Cards and generated apps

Consequential Teams cards use reviewed typed actions; informational cards use a bounded display DSL. Hermes cannot invent raw action payloads. The gateway validates bindings/expiry, consumes actions idempotently, and passes only the visible choice back to Hermes.

Generated apps use one child Sandbox per app, deny-default egress, at most five active apps per Worker, and artifacts limited to 80 files/2 MiB. Native Entra participant ports are OnDemand; five idle minutes suspend compute and default deletion is 24 hours after suspension. Owner-bound **Keep 1h/6h/24h/72h** and **Delete** actions change native lifecycle directly, without an LLM or Service Bus. Updates preserve the logical app ID; failed updates preserve the working deployment. App traffic never traverses the gateway.

## Observability

Foundry external-agent registration identifies externally hosted compute. Azure Monitor export uses Entra authentication. Default spans contain timing, opaque correlation, model/tool names, and available usage—not prompts, arguments, results, documents, raw identities, or error payloads.

Native `llm_execution` and `tool_execution` callbacks are instrumented. A local metadata-only handoff keyed by explicit native session bridges executor-thread context loss. Session chat and explicit-session chat completions support this handoff. Ambiguous interrupted leases return `409`; recovery restarts **both runtime wrapper and native gateway**, not only the public gateway. Expiry stops attribution but does not prove the old executor stopped.

## Current limitations

Both Workers run Hermes 0.22.5 with Memory suspend and Blob-volume backups. A cold-response long tail remains: a non-streaming upstream model call can stall until Hermes' no-response watchdog retries. VM wake, native API readiness, model generation and Teams delivery are distinct timings; keepalive and UI streaming do not by themselves resolve that model-transport behavior.

| Area | Boundary |
| --- | --- |
| Fresh Worker bootstrap | `scripts.worker_lifecycle bootstrap` needs a tenant administrator, interactive admin consent, available licenses, and a manual package upload in Microsoft 365 admin center. |
| Gateway endpoint | A gateway image change replaces its Sandbox and URL, which also replaces runtime compute because the runtime environment contains that URL. Agent 365 keeps sending to the old URL until a direct blueprint owner updates the endpoint ([DEPLOYMENT.md](DEPLOYMENT.md) step 6). |
| Runtime repair | Ingress wakes only existing compute. Deleted or `Failed` runtime compute is replaced by rerunning service deployment, not by the gateway; the replacement loses changes made after the newest automatic backup. |
| Registry conversion | SDK 0.1.0b4 does not expose the native-v2 disk-image endpoint, so the deployer calls it through the SDK client pipeline ([ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md)). The four workload identities can pull from the demo registry. |
| Hermes version | Hermes 0.20+ ships only as GitHub releases and container images, not on the approved package feed. The runtime uses the official image, imported into ACR by digest. Packages the plugins need inside Hermes' sealed environment are vendored from the approved feed and pinned by hash. |
| MCP discovery | A fresh Hermes start discovers all eleven MCP servers before opening its API. Native lazy discovery needs a cached tool schema, but these servers return `ttlMs: 0`, so it stays eager. Memory resume avoids the cost; a new Sandbox pays it once. |
| Work IQ routes | Only the per-server Work IQ MCP servers are used. The unified Work IQ MCP, A2A, and REST routes reject the Agent User under the current tenant billing and Copilot licensing. |
| Teams privacy | Public group `@` discovery is available; Hermes `/` discovery is absent in this Agent User package. `agenticUserTemplates` has no `bots[]`; targeted private handling and human OBO are not implemented. Do not put private facts in shared group context. |
| Teams routing | Do not assume unmentioned channel messages, thread subscriptions, full history, or every supported SDK event reaches the Agent User. |
| Native tracing | `/v1/responses` creates its own session and is marked `autopilots.trace.correlation=missing`. Sandbox-origin egress preserves W3C TraceId but rewrites the parent through platform hops absent from Application Insights. Proactive Teams sends and Graph document uploads have no spans. |
| Office | Work IQ Word lacks arbitrary existing-body editing; the reviewed local/Graph path supplies it. Direct PDF/image/XLSX/PPTX attachment ingestion is absent. Shared PowerPoint supports bounded text reads, not creation/body editing. |
| UI hosts | MCP Apps is not implemented in the Hermes Agent User conversation. Proactive card attachments and generated-app action replacement rendering have host limitations; use text/suggested actions and request fresh app inventory. |
| BYO MCP | Agent 365 registration/approval requires a supported client's connection/OAuth flow; raw generic MCP initialization is insufficient. |
| Evaluation | `scripts.evaluate_learning` checks response text only; it does not measure native skill discovery, tool workflows, or repeated-trial variance. Judge packets on owner-authored holdout cases, not agent-proposed ones. |

No swarms, direct Worker writes to shared Role Blueprints, automatic Promotion, or parallel central memory store.
