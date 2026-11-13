# Hermes Worker contracts

Hermes 0.22.6, pinned by digest in a dependency image and paired with a verified application release, implements a Junior Project Manager Worker: delivery coordination, document collaboration, scheduled work, and governed learning. [Deployment](DEPLOYMENT.md) contains operator procedures; [Demo](DEMO.md) contains prompts.

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

For an offline source snapshot, set `HERMES_ROLE_BLUEPRINT_SOURCE=local`, retain the role name, semantic release and full commit settings, and set `HERMES_ROLE_BLUEPRINT_PATH` relative to the installed `AUTOPILOTS_APPLICATION_ROOT` (for example `blueprints/junior-project-manager`). The runtime source-release package must include that complete role tree and `role-release.json` at its root. `HERMES_ROLE_BUNDLE_SHA256` pins the SHA-256 of the manifest's exact bytes independently through deployment configuration.

The manifest has `schemaVersion: 1`, `roleBlueprint`, `roleBlueprintPath` (POSIX relative path), `roleRelease`, `roleReleaseCommit`, and `files`, a mapping of every role-relative regular-file path to its lowercase SHA-256. The packager must derive the commit and files from the same verified snapshot, not an uncommitted checkout. Runtime checks the manifest hash, configured role/release/commit/path, exact file inventory, every content hash and `distribution.yaml`; it rejects links and path escape. Verification also runs before reusing an already installed release. The Worker manifest records `roleBundleSha256` alongside the existing release commit. Local mode never invokes Git or fetches a repository; Git metadata and GitHub access are unnecessary on the customer host. Existing remote sources retain their immutable Git checkout and Worker Refresh behavior. This is snapshot integrity tied to deployment configuration, not an independent publisher signature.

## Hosting and ownership

Shared Terraform infrastructure supplies Foundry, ACR, networking, private DNS, an Express managed environment for private ingress, Service Bus infrastructure, and keyless monitoring. Foundry and Sandboxes use Sweden Central; ACR and the application network use North Europe, connected through VNet peering.

Each Worker has a separate Terraform workspace, Agent 365 platform blueprint, Agent Identity, Agent User, and five Sandbox Groups with five user-assigned managed identities:

| Group | Responsibility |
| --- | --- |
| `gateway` | Authenticated Agent 365 admission, direct Teams turn handling, proactive delivery, and generated-app publication. Native callbacks receive scheduled work only. |
| `runtime` | One Hermes writer, OnDemand compute suspended to memory, Hermes home on the Sandbox disk with native backups on a Blob volume, native tools, memory, learning, and cron state. |
| `private-mcp` | Incidents MCP, private ingress through the linked Express environment, application-level Entra authorization. |
| `public-mcp` | Shipments MCP, public HTTPS, application-level Entra authorization. |
| `generated-apps` | Separate child Sandbox per published app; participant-authenticated ports and native lifecycle. |

The gateway does not proxy model inference, MCP traffic, or generated-app traffic. Agent 365 activities enter the bridge directly; the bridge handles the turn and calls native Hermes Runs over OnDemand HTTPS. Teams messages are not copied into Service Bus or a durable activity-recovery inbox. The bridge's journal is used for scheduling receipts and bounded conversation state, not for admitting interactive model requests.

Each Worker has one Service Bus queue, `worker-<name>`, for native Hermes cron, Dreaming, and deterministic document retries when enabled. There is no activity-recovery queue and no interactive runtime request/result queue. A per-Worker managed connector has its own system-assigned identity and one caller-owned connection created by the gateway identity that settles its scheduling leases, not by the deployment operator.

Gateway, runtime, and both MCP services suspend to **Memory** after five idle minutes. Unchanged stopped Sandboxes resume with the same ID and running processes; identity clients must refresh expired tokens rather than reuse stale credentials. The Hermes home (`/data/hermes`) lives on the Sandbox root disk. Native backups on a platform-managed Blob volume make it survive replacement or loss of the Sandbox ([Persistent state](#persistent-state)). A Data Disk volume would force Disk mode. A home remains single-writer because it contains SQLite and transactional skill state.

Application source, dependencies, and identity have different lifecycles. Gateway and MCP services use a pinned public Python image, hashed source releases, and a lock-addressed dependency environment prepared from approved-feed wheels. The runtime uses one ACR dependency image for Hermes, Office binaries, and installed packages, plus a separate source release. A source-only update switches the supervised process in the existing Sandbox; it does not rebuild an image, recreate an identity, or change the native URL. A changed base image or compute-defining setting can require replacement and endpoint reconciliation.

The `autopilots` Hermes plugin's keepalive wraps actual model and tool execution, including background review and Curator forks, in `try/finally` activity scopes. The runtime wrapper uses the same supervisor for mutating HTTP requests, including deterministic cron/document work. While active, each process sends periodic GETs at roughly 60-second intervals to its own published `/health`, using platform-provided `ADC_SANDBOX_ID` and `ADC_PROXY_DNS_SUFFIX`. Loopback requests, outbound model calls and local CPU work do not reset platform idle time. There is no keepalive queue, external controller, durable busy ledger or additional Azure permission. An operation expires after 30 minutes or when its owning thread exits; expiry and ingress failures are logged. This limit applies to one uninterrupted operation, not the total multi-step agent task. Detached subprocesses outside a tracked operation and future schedules do not keep compute awake.

Deployment owns compute. After the gateway publishes its native URL, deployment creates or reuses the runtime, installs the exact source release, and verifies its loaded release identity before exposing it to the gateway. A changed compute fingerprint or Role Release replaces runtime compute: a Role Release change first requires Worker Refresh readiness, then the old runtime publishes a final backup and its replacement restores it. A `Failed` runtime restores the newest automatic backup. An update refuses to delete or replace a Worker identity or Sandbox Group merely because the wrong Terraform workspace or inputs were selected.

The gateway has no runtime Sandbox Group role or runtime provisioning path. It uses authenticated native `/v1/runs` and deterministic control APIs over OnDemand HTTPS. Scheduled native Runs persist their admission scope, run ID, and frozen transcript/cron comparison state in the scheduling delivery receipt, so an exact broker redelivery can resume the same native run rather than create another. Teams turns are not admitted through a separate model API or polling protocol. Missing or `Failed` runtime compute returns an explicit error until deployment is rerun. Keepalive does not guarantee crash recovery or exactly-once external delivery.

Gateway, runtime and MCP ports all use `OnDemand`. A request to a stopped `Manual` port returns `403` instead of waking compute; this previously disabled both MCP servers whenever they had been idle for 30 minutes. Measured on 2026-10-31 in Sweden Central (2 vCPU, 2 GiB): the platform resumed a Sandbox in under a second in either mode, and a Memory resume reached the native Hermes API in 1.5–2.7 s, including three cycles on the deployed `hermes2` Worker. Disk mode needed 17–25 s, because every wake repeated the launcher, the official bootstrap, imports and plugins, and MCP discovery of eleven servers, which Hermes completes before opening its API. A new runtime Sandbox pays that full start once: the `hermes2` replacement restored a 24 MB backup and passed `/health` 12 s after creation.

The native connector runner owns PeekLock for the scheduling queue and POSTs the complete broker envelope directly to a fixed authenticated OnDemand HTTPS callback on gateway port 8001. The native proxy pins the infrastructure tenant and namespace MI object ID and validates the ADC audience; public Teams port 8000 cannot admit callbacks, including requests carrying the former callback key. One gateway process serves both listeners. The gateway stores a durable scheduling receipt, renews the lease through the same creator-owned connection, performs bounded work, and completes only after the scheduled handler succeeds. Connection-policy delegation remains blocked by live `502 ConnectorNamespaceConnectionOwnerAccessDenied`; it is not replaced with a custom relay. A receive loop inside a sleeping process cannot provide wake-up and is not used.

The connector service still polls the source outside the actors, which can delay scheduled work. The rejected three-hop design measured 68-116 seconds before activity receipt and 161-175 seconds per warm turn, versus about 4-13 seconds of model execution. Teams uses the direct gateway-to-native-Runs path; Service Bus is not on its response path. Queue timing is not chat timing, and an unverified ten-second metadata hint is not a latency guarantee.

Scheduling envelopes are at most 64 KiB and contain job metadata, not Teams messages, prompts, document bodies, model output, or credentials. Interactive model input travels directly in the authenticated native HTTPS request body. Attachment extraction and model-context limits apply independently.

Source updates use owned maintenance barriers: stop new gateway admissions before touching runtime compute, require active gateway turns and scheduled callbacks plus actual native tasks/executor threads to be quiescent, switch source, and verify loaded health. Native endpoint futures remain active until their worker threads finish, even after HTTP cancellation. Inactive scheduling receipts remain unchanged and do not block source repair; startup never executes journal rows. Unsettled receipts do not authorize repeating completed effects. Failure restores the preceding application configuration. Superseded gateway/MCP compute is recorded for explicit retirement after replacement validation.

Unknown scheduled effects are not completed or silently retried. They are retained as unknown in the journal and moved to the broker DLQ through the owning connection. This terminal quarantine stops poison-message wake loops without claiming successful execution. A failure before scheduled effects start remains retryable. Journal snapshots are created and checked locally, then copied and renamed to Blob under a local cross-thread/process publication lock.

Deployment explicitly separates Terraform resources, identity reconciliation, image conversion, service creation, and endpoint capture. It does not use Terraform `local-exec`. Linking a Group to the private-ingress environment is irreversible.

## Identity and network boundaries

| Operation | Principal and enforcement |
| --- | --- |
| Foundry inference | Runtime managed identity and model-resource RBAC; native Hermes `azure-foundry` Entra token callback. |
| Autonomous custom MCP | Managed identity → blueprint federation → Agent Identity token; server validates issuer, tenant, audience, Worker binding, and app role. |
| Worker-owned Microsoft 365 | Agent Identity exchange → `user_fic` for the fixed Agent User → delegated Work IQ/Graph token. |
| Agent 365 ingress | Microsoft 365 Agents SDK validates Activity Protocol authentication. |
| Native cron Service Bus send | Worker Agent Identity with Data Sender, federated from runtime managed identity. |
| Document retry publication | Gateway managed identity with queue-scoped Data Sender; no Agent Identity exchange. |
| Queue receive and callback execution | Native connector system identity with queue-scoped Data Receiver and SandboxGroup Data Owner on the gateway callback group only. |
| Lease renewal and completion | Gateway managed identity through its own native connection and narrowly scoped named-action role. |
| Runtime compute lifecycle | Deployer only. The gateway managed identity has no role on the runtime Sandbox Group. |
| Generated-app lifecycle | Gateway managed identity with Data Owner on that Worker's generated-app Group. |
| Generated-app user access | Native Entra-authenticated port with explicit participant allowlist. |

`autopilots_identity.mcp_proxy` is the required loopback-only Agent Identity/Agent User federation adapter. It acquires and refreshes fixed upstream MCP tokens, reusing one blueprint exchange and prefetching all scopes at startup; it is not a model proxy or human OBO implementation.

Private network access and authorization are independent. Runtime VNet attachment supplies routing/DNS; the Express environment's Private Endpoint restricts incidents ingress. Gateway and MCP native ports allow anonymous HTTPS transport, but their applications authenticate requests. Shipments accepts `Shipments.Read.All` application access or the separate `Shipments.Read` delegated BYO flow.

Sandbox egress inspection remains enabled. Python clients trust the system certificate store through `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`; disabling TLS checks is not an authentication remedy.

Registry conversion is deployment-only. Only the runtime dependency image needs ACR and runtime-group `AcrPull`; gateway and MCP groups convert the public Python digest directly. No registry token or admin credential exists. Runtime deployment uses `AGENT_RUNTIME_DISK_IMAGE_ID`. [ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md) records the conversion boundary.

SandboxGroup Data Owner is broader than execute-only, and the native connection named-action wildcard is broader than one settlement action. Neither is described as absolute least privilege. Temporary connection/trigger creation grants are bounded to the owned namespace and removed in `finally`. A connection's immutable creator must match the identity that settles its leases.

## Messaging, events, and sessions

There is no general model-invocation HTTP API, request polling endpoint, or bridge HTTP trigger for manual Dreaming. Real interactive validation uses the Agent 365/Teams surface or the configured Agent Chat Simulator channel. Automatic Dreaming remains a native scheduled Service Bus job; `scripts.servicebus_dream_smoke` uses the Sandbox management plane to enqueue a separate ad-hoc occurrence without changing production cron. Learning Packet preparation, approval, and export remain separate explicit operations and are never triggered automatically by Dreaming.

- Agent 365 Agent Users remain the real Teams packaging path; no companion bot is installed. Simulator channels use the same bridge with separate configuration.
- Personal messages and explicit mentions are invocation surfaces. Delivered replies, reaction events, and message updates are handled; SDK handlers do not imply that Teams delivers every event.
- When observation is enabled, added reactions to remembered agent-authored messages may invoke Hermes. Removed reactions do not invoke it. Message updates record diagnostics only; there is no delete-event handler.
- The bridge adds/removes temporary `eyes` on eligible public group requests and sends typing indicators in personal/group chats. Hermes requests semantic reactions through `TEAMS_REACTION: eyes|like|heart|smile|surprised|check`; the bridge strips control lines and executes them. Undirected thanks after an agent response can receive a direct `like` acknowledgement. `NO_RESPONSE` suppresses visible text.
- Bridge context is a bounded journal-backed record of delivered events, not a channel subscription or a complete channel-history service.
- Hermes transcript IDs rotate hourly by default. `HERMES_API_SESSION_ROTATION_HOURS` supports 1–24 hours; `HERMES_API_SESSION_RESET_HOUR_UTC` aligns buckets.
- Stable `X-Hermes-Session-Key` includes Worker, source, authenticated user, and conversation/thread hash. `X-Hermes-Session-Id` is conversation/thread-based and time-bucketed. Group transcripts are shared despite user-specific memory keys.
- `/new` and `/reset` create a distinct native session generation; native inventory preserves that selection across gateway restarts. Personal Memory and Work History remain on disk.
- Email and Word/Excel/PowerPoint comment notifications use `/api/messages` and reply through the originating workload, not an arbitrary Teams channel. An email gets exactly one reply: Hermes either sends it with Mail tools and returns `NO_RESPONSE`, or returns only the body that the bridge sends.

Teams currently receives a completed response through the Agents SDK, not raw model deltas. Personal/group chats show typing; eligible group requests also receive a temporary processing reaction. The bridge first removes provenance and UI control directives, reconciles learning and schedule bindings, then chooses text, a card, a reaction, or no visible reply. Streaming internal transport and streaming text to Teams are separate decisions.

As checked on 2026-10-30, [Teams streaming documentation](https://learn.microsoft.com/microsoftteams/platform/bots/streaming-ux) describes non-notifying informative updates and one progressively populated message in personal, group and channel conversations with current SDKs. It limits a conversation to one open stream and a stream to ten minutes, and excludes proactive messaging. This documents Teams capability, not verified support for this Worker's Agent 365 identity/SDK path. Keep UI streaming disabled until that path and final-response filtering are explicitly validated.

### Simulator channel configuration

`USE_AGENTIC_AUTH=true` retains the Agent 365 adapter and identity path. Without it, a service-connection `CLIENTID` selects standard Agents SDK authentication; no `AGENT365_*` objects or settings are required. Set `USE_AGENTIC_AUTH=false` on both simulator gateway and runtime services to disable Work IQ/M365 sidecars and Agent Identity/User token acquisition, including cached tokens. Foundry and optional Service Bus managed-identity authentication are independent of this switch.

| Standard simulator gateway setting | Value |
| --- | --- |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE` | `FederatedCredentials` |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID` | Agent application UUID |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID` | Tenant UUID |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID` | Gateway user-assigned managed identity client UUID |
| `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__SCOPES__0` | `<simulator channel application UUID>/.default` |
| `CONNECTIONSMAP__0__SERVICEURL` | Anchored, regex-escaped simulator HTTPS origin, for example `^https://simulator\.example/?$` |
| `CONNECTIONSMAP__0__CONNECTION` | `SERVICE_CONNECTION` |
| `ATTACHMENT_DOWNLOAD_ORIGINS` | Comma-separated exact simulator HTTPS origins, with no path, query or credentials |

`ClientSecret` is supported for local tests only. Standard inbound authentication requires a signed, unexpired Entra v2 application token for the agent application, from the configured tenant issuer and channel `azp`; delegated tokens and unmapped service URLs are rejected. The channel application is inferred from the configured scope. Bot Framework user-token/OBO acquisition is disabled for this channel. Proactive sends use the stored service URL and channel-scoped application token, not an Agent 365 blueprint.

Hosted DEMO mode instead requires `DEMO_CHANNEL_KEY` (at least 32 URL-safe ASCII characters) and exact `DEMO_SERVICE_URL=https://<simulator>/demo/<same-key>`. It refuses startup with Agent 365 mode or a service-connection client ID. `/api/messages` requires `X-Demo-Channel-Key` using a constant-time comparison and rejects any different `serviceUrl`, including a trailing-slash variation. SDK replies and proactive continuations carry no bearer token; the simulator authenticates the key-bearing base path. Startup logs an explicit test-data-only warning. This is not Entra authentication and is unsuitable for confidential data.

File downloads retain public-address validation and pinned DNS. Explicitly allowlisted simulator capability URLs (`/api/agent-files/<id>/content?capability=...`) are downloaded without forwarding a service token; Connector original views use the connector token only on the service origin. HTTP loopback requires both an explicit origin and `ALLOW_LOOPBACK_ATTACHMENTS=1`; neither private-network targets nor cross-origin token forwarding are enabled by this test switch. `send_file_attachment` uploads DOCX/TXT bytes through the Connector and sends the original-view attachment. Automatic discovery/delivery of arbitrary Hermes-generated local files is not implemented: the native Runs API has no verified artifact retrieval contract. Existing M365 document publication still requires Agent 365 tools.

The repeatable local protocol smoke uses the real simulator backend, actual SDK adapters/Connector and temporary SQLite state, with an explicit stub Hermes. From `autopilots-on-azure`, after both projects' locked environments are available, run `uv run --frozen python tests\simulator_channel_smoke.py --simulator-root .\chat-simulator --mode standard`, then repeat with `--mode demo`. An extracted source tree can use `--simulator-site-packages <its-installed-environment-site-packages>`. Standard mode uses the simulator's local RSA Test IdP and synthetic token acquisition, not real Entra/FIC. DEMO mode uses a generated, explicitly trusted temporary TLS certificate (verification stays enabled), checks that every outbound Connector request is anonymous and keeps `/demo/<key>/v3/`, and rejects wrong ingress keys/service URLs. Both cover personal/group mentions, typing, reactions, Adaptive Cards, DOCX/TXT capability input, Connector file output/download, existing document-choice cards, proactive delivery with/without saved claims and update/delete. The document-choice provider is test data, not a live Office lock. This does not substitute for deployed model/Entra validation.

Interactive chat does not require native scheduling transport. With no `TRANSPORT_CONNECTION_ID`, `TRANSPORT_JOURNAL_PATH` enables bounded conversation persistence; `TRANSPORT_JOURNAL_BACKUP`, when configured, is checkpointed after each state write and restored if local state is absent. `/internal/transport/status` still requires `X-Autopilot-Key` and reports actual gateway work with `enabled=false` and zero scheduling work. This does not enable cron wake-up or automatic Dreaming. Unless scheduling is enabled and namespace/queue are present, cron tools return an explicit scheduling-unavailable error without creating a job or falling back to container-local scheduling. Configured non-agentic cron uses `DefaultAzureCredential` and requires its own Service Bus sender permission and scheduling infrastructure.

### Worker owner and shared conversations

`WORKER_OWNER_ID` is the invoking human's Entra object UUID, not a UPN, app ID, managed identity, simulator database user ID or Agent User ID. The bridge reads `from.aadObjectId` first. DEMO mode supplies the simulator's deterministic UUIDv5 display-name identity; use that emitted identity for a demo owner.

The owner setting is **not a chat or scheduling allowlist**. Other authenticated simulator members can chat with an installed agent. Personal messages invoke it; group mentions are strong invocation signals. With `AUTOPILOT_TEAMS_OBSERVE_UNMENTIONED=true` (default), delivered unmentioned group messages can also reach Hermes, which may return `NO_RESPONSE`. Setting it false restricts non-personal admission to explicit mentions or targeted messages. Group context/transcripts are shared; a mention does not grant private or delegated resource access.

Only the owner's personal chat can receive `/learning`/`/learned` digests, save the owner delivery reference, or act on learning decisions; group chats cannot do so even when the owner mentions the agent. Learning tokens also bind the user, conversation, Worker and expiry. Collective Learning administration requires the dedicated Entra operator token, owner `oid`, Azure CLI caller and review scope; its tenant accepts the existing `AGENT365_TENANT_ID`, then `TENANT_ID`, then service-connection `TENANTID`. Other internal administration routes retain their `API_SERVER_KEY` boundary. Native cron requires a trusted conversation/session origin, not `WORKER_OWNER_ID`; schedules must not be described as owner-only or isolated by group participant. Native upstream per-user cron authorization is not established by bridge transport tests.

## Persistent state

The Worker home is `/data/hermes` on the runtime Sandbox root disk. Hermes 0.22.6 runs one gateway per home, so the Worker is the home itself rather than a named profile.

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
| Native API session/run state | Native Hermes home and native backup | Preserve | Exclude |

Personal Memory is a bounded frozen snapshot injected at fresh-session start. Its files and Private Playbooks belong to the Worker, not separate per-caller profiles. A user-specific session key does not split these stores. Skills use progressive disclosure; transferable skills are restricted to one `SKILL.md`. Skill basenames are unique across namespaces.

The distribution includes only declared role-owned files: `distribution.yaml`, `SOUL.md`, configuration, MCP definitions, Role Skills, schemas, and optional reviewed cron definitions. Credentials, memories, private/candidate skills, sessions, workspace, caches, and learning state are excluded. The Worker manifest records the source, commit, release, owned paths, and Role Skill baseline hashes.

### Durability

The root disk is local to one Sandbox, so it is not the only copy. The runtime wrapper checks the home once a minute and, when anything a backup would keep has changed, runs native `hermes backup`. Logs, caches, heartbeats, locks and SQLite shared-memory files that idle Hermes rewrites by itself do not count as changes. `hermes backup` snapshots every SQLite database through the SQLite backup API and writes a complete zip into the home's own `backups` directory; only a complete archive is copied to the Worker's platform-managed `AzureBlob` Sandbox volume at `/backup` and published by rename. The newest 24 archives are kept. Staging is local because SQLite snapshots fail on the blobfuse mount.

A runtime starting with an empty home restores the newest archive with native `hermes import` before anything else touches the home, then checks that the restored manifest names the same Worker. A damaged or foreign archive, or a volume that cannot be listed, stops startup; a readable empty volume starts a fresh Worker. An existing home is never overwritten. Before deployment deletes a replaced runtime, it runs one more backup inside it, waiting for any automatic backup in progress. `/health` reports the last published archive and error.

The gateway journal has its own `gateway-state` managed Blob volume. Local SQLite snapshots are checked and copied/renamed to that volume before acknowledging scheduling callbacks. A gateway replacement restores the verified journal before accepting callbacks. Scheduling receipts, native run IDs, frozen transcript identities, occurrence-scoped outbound acknowledgements, and bounded Teams conversation state are retained; interactive message payloads and operator request bindings are not stored there. The runtime keeps native API state in its Hermes home, not a second custom transport journal. A possibly external scheduled effect interrupted mid-delivery is explicitly `unknown`, not silently replayed.

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

Attachment and persistence-disabled turns run in a separate native session marked `private:`. A `pre_tool_call` hook blocks `memory`, `skill_manage` and file writes into `skills/` or `memories/` there, including in background review. Operational transcripts and transport journals are private execution state, never transferable learning material.

### Transferable contract

- A Role Skill or Candidate Improvement leaves the Worker only after the owner chose **Share** for its current content. A later change requires a new choice.
- Learning Packet **3.0** contains each shared skill's files, release-baseline hashes, artifact hash, native ledger chain from the release baseline to the current files, share decision and owner approvals of staged writes. File hashes are recomputed from disk; the ledger explains them.
- Every other governed change is excluded with a reason and stays local. Only one bounded UTF-8 `SKILL.md` whose frontmatter names its directory is transferable; scripts, references, binary payloads, credentials, memories, sessions and private playbooks never are. A privacy scan rejects addresses, identifiers, internal URLs, secrets and absolute paths.
- Human approval binds the exact digest, Worker, release commit, and governed-state hash. The gateway holds the Ed25519 private key; runtime and review receive public keys only. Collective Learning operator routes require a validated Entra v2 delegated access token for the dedicated operator API, the `CollectiveLearning.Review` scope, the Azure CLI client and the configured Worker's owner in its tenant. Runtime API credentials cannot authorize those routes. The gateway derives receipt identity as `entra:<tenant-id>:<owner-object-id>` and refuses caller-supplied approver/rejector fields. Teams staged-skill approval stays separate. Exact pending digest, current governed-state and native-ledger checks remain mandatory.
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

Before native admission the gateway registers the conversation reference against the exact native transcript. A runtime tool middleware supplies that trusted origin to native cron creation; it is persisted with the job before provider registration. The provider's native `register_job(job)` hook derives the delivery binding from that origin and verifies that the exact persisted revision and due time have a broker sequence number before creation can report scheduling success. Registration failures propagate through native `CronSchedulerRegistrationError`: the saved job remains, but callers must not claim it is armed or create a duplicate. The provider refuses rebinding to another conversation. There is no before/after cron inventory diff or post-turn binding request. The model-turn lock remains for learning/document mutation safety; this change does not enable unchecked parallel mutation.

Updates, pause/resume and removal use native `on_jobs_changed()` to replace or cancel the existing broker occurrence; there is no separate native unregister hook. Only an already-missing broker message is treated as a cancellation race. Other cancellation failures retain the observed registry for a later reconciliation. Native mutation notifications are best-effort, so inspect diagnostics after a broker failure rather than assuming cancellation succeeded; native revision/state checks still reject obsolete callbacks. The provider forwards native `force`/`manual` fire arguments without replacing native claim, execution or interrupted-attempt recovery.

After execution, result recovery authenticates the exact native execution ID, job and scheduled instant, plus the captured output path/hash. A completed occurrence retires only its matching consumed broker registry entry; it does not try to cancel an already-active delivery. Future-schedule reconciliation errors are recorded separately and do not hide an authenticated completed result or force another inference. Missing/mismatched execution evidence or changed output is explicitly unknown; recovery never selects a file by timestamp or directory order.

Reserved `system.dream` uses fenced checkpoints for invocation, durable response, reconciliation, and completion. Persisted completed responses replay without new model/tool execution. A `dream_started` with a known journal `native_run_id` reattaches that native run; without one it stops for inspection. Ad-hoc run-now has a distinct occurrence and does not advance the production schedule. Dreaming never prepares or exports a Learning Packet; a no-change Dream can complete with `recordCount=0`.

The transport distinguishes completed/duplicate/stale, retryable pre-claim failure, known continuing native work, failed work, and unknown effects. Only successful or explicitly skipped outcomes complete broker leases; unknown/failed work is durably quarantined and dead-lettered. Final-attempt exceptions terminalize immediately, and startup/status/callback sweeps terminalize stranded final attempts without treating lease expiry as replay permission. Admission counts live scheduling tasks, not historical recovery rows. Safe pre-claim repair validates the unchanged native occurrence and uses a stable repair delivery ID without changing native occurrence identity. Scheduling tombstones have no automatic TTL, including old DLQ replay; older native receipts are compacted, not deleted. Operator skip/resolve actions, schedule health and DLQ inspection are described in [DEPLOYMENT.md](DEPLOYMENT.md).

Dreaming can update local memory and skills, but cannot prepare or approve a Learning Packet, export, promote, or merge. Both `hermes` and `hermes2` enable user scheduling and Service Bus Dreaming, each on its own queue.

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

Native `llm_execution` and `tool_execution` callbacks are instrumented. A metadata-only handoff bridges executor-thread context loss. Scheduled Native Runs bind it to their durable delivery identity: an exact redelivery preserves the original context, while another delivery cannot acquire a potentially active lease. Expiry stops attribution without proving the old executor stopped. Scheduling envelopes carry only W3C context and opaque correlation, never prompt contents.

The authenticated runtime wrapper publishes the local handoff for `POST /v1/runs` before native admission. It retains it after HTTP 202 and releases it only after observing completed/failed status. Ambiguous admissions retain their lease; another admission cannot take over that session. The gateway does not attempt filesystem handoff across Sandboxes. Gateway active-work accounting covers the complete Teams HTTP response and reserves background continuations before their parent exits; bounded self-ingress keepalive does not turn a hung operation into permission to update.

Azure Monitor records a per-Worker alert when the schedule queue has dead-lettered messages and, for enabled daily Dreaming, when no completed Dream transport span arrived in 36 hours. The freshness rule applies to five-field schedules whose day/month/weekday are all `*`; less frequent custom schedules need a different monitoring window. These checks query broker metrics and exported telemetry, not Worker ingress, so they do not prevent idle suspend. Rules create Azure Monitor alerts; no external paging destination is configured.

## Current limitations

The dependency base pins Hermes 0.22.6 with Memory suspend and Blob-volume backups. VM wake, application readiness, native admission, model generation, and channel delivery remain distinct timings. Keepalive does not repair an upstream model stall or turn an unknown external effect into a safe retry.

| Area | Boundary |
| --- | --- |
| Fresh Worker bootstrap | `scripts.worker_lifecycle bootstrap` needs a tenant administrator, interactive admin consent, available licenses, and a manual package upload in Microsoft 365 admin center. |
| Gateway endpoint | Source-only updates preserve the Sandbox and URL. A base-image or compute replacement can change the URL; a direct blueprint owner must reconcile Agent 365 before retiring the preceding gateway. |
| Runtime repair | Ingress wakes only existing compute. Deleted or `Failed` runtime compute is replaced by rerunning service deployment, not by the gateway; the replacement loses changes made after the newest automatic backup. |
| Registry conversion | SDK 0.1.0b4 does not expose native-v2 private-image conversion, so deployment uses its SDK pipeline ([ADR 0021](docs/adr/0021-deployment-time-sandbox-image-authentication.md)). Only runtime conversion needs `AcrPull`; thin services use a public digest. |
| Native connectors | Caller-owned connections and broader-than-execute callback roles are platform constraints. Trigger GET omits write-only authentication; configuration readback alone is not proof of callback execution or lease settlement. |
| Receiver latency | Managed source polling can delay scheduled work. It is not on the interactive Teams path. The scheduling smoke separately checks real queue delivery and Memory retention. |
| Transport limits | Five-minute scheduling PeekLock with renewal and a bounded active-operation window. Unknown scheduled effects stop rather than silently replay. |
| Hermes version | Hermes 0.20+ ships only as GitHub releases and container images, not on the approved package feed. The runtime uses the official image, imported into ACR by digest. Packages the plugins need inside Hermes' sealed environment are vendored from the approved feed and pinned by hash. |
| MCP discovery | A fresh Hermes start discovers all eleven MCP servers before opening its API. Native lazy discovery needs a cached tool schema, but these servers return `ttlMs: 0`, so it stays eager. Memory resume avoids the cost; a new Sandbox pays it once. |
| Work IQ routes | Only the per-server Work IQ MCP servers are used. The unified Work IQ MCP, A2A, and REST routes reject the Agent User under the current tenant billing and Copilot licensing. |
| Teams privacy | Public group `@` discovery is available; Hermes `/` discovery is absent in this Agent User package. `agenticUserTemplates` has no `bots[]`; targeted private handling and human OBO are not implemented. Do not put private facts in shared group context. |
| Teams routing | Do not assume unmentioned channel messages, thread subscriptions, full history, or every supported SDK event reaches the Agent User. |
| Native tracing | Runs executor handoff requires an explicit session and idempotency key. Sandbox-origin egress can rewrite the parent through platform hops absent from Application Insights. Proactive Teams sends and Graph document uploads have no spans. |
| Office | Work IQ Word lacks arbitrary existing-body editing; the reviewed local/Graph path supplies it. `CreateDocument` can commit a file before returning sharing `input-required`; verify the original item before any retry and never fabricate a sharing recipient. Direct PDF/image/XLSX/PPTX attachment ingestion is absent. Shared PowerPoint supports bounded text reads, not creation/body editing. |
| UI hosts | MCP Apps is not implemented in the Hermes Agent User conversation. Proactive card attachments and generated-app action replacement rendering have host limitations; use text/suggested actions and request fresh app inventory. |
| BYO MCP | Agent 365 registration/approval requires a supported client's connection/OAuth flow; raw generic MCP initialization is insufficient. |
| Evaluation | `scripts.evaluate_learning` checks response text only; it does not measure native skill discovery, tool workflows, or repeated-trial variance. Judge packets on owner-authored holdout cases, not agent-proposed ones. |

No swarms, direct Worker writes to shared Role Blueprints, automatic Promotion, or parallel central memory store.
