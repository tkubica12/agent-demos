# OpenClaw option path: Teams + Agent 365

## Status and relationship to Autopilots on Azure

This document is the historical and forward option path for the OpenClaw runtime. It records the implementation path that brought the project from an OpenClaw-only Azure demo through Teams collaborative UX and into Agent 365 setup.

The project is now moving toward the `autopilots-on-azure` architecture, where OpenClaw and Hermes are peer runtimes behind a common bridge. Keep completed OpenClaw milestones here as historical baseline rather than rewriting them as if they were originally implemented with the new multi-runtime abstraction.

Current status at the time of rename:

- Milestones through 3.5 are complete for the OpenClaw path.
- Milestone 4 setup has been partially completed; remaining browser, tenant, license, and instance-validation steps are tracked below.
- New runtime-neutral architecture decisions live in `docs\adr\0005-autopilots-rename-and-runtime-neutrality.md`, `docs\adr\0006-common-bridge-with-runtime-adapters.md`, `docs\adr\0007-side-by-side-autopilot-deployments.md`, and `docs\adr\0008-hermes-api-server-in-sandbox.md`.
- Hermes parity and the multi-runtime migration plan live in `HERMES_OPTION_PLAN.md`.

## Goal

Evolve the OpenClaw-on-Azure demo from a directly accessed ACA Sandbox gateway into a Teams / Agent 365 agent experience:

```text
Teams / Agent 365
  -> tiny always-available bridge endpoint
  -> wake or create OpenClaw ACA Sandbox
  -> forward user turn to OpenClaw Gateway
  -> OpenClaw uses private incidents MCP and, later, Work IQ MCP
```

End-state direction: OpenClaw acts like an Agent 365 "AI teammate" / Autopilot-style agent with its own identity, Teams presence, mailbox/calendar capabilities, group chat participation, mentions, reactions, notifications, and governed Work IQ tool access.

## Strategic shift from the first bridge experiment

The initial Milestone 1 implementation used `azd` + Bicep + post-provision scripts to get ACA Express, images, secrets, and the bridge deployed quickly. That was useful for learning, but it exposed deployment-shape issues:

- `azd deploy` against ACA Express hit preview control-plane issues while syncing/listing secrets.
- ACA Express supports ARM/Bicep through preview API versions, but the exact shape is sensitive.
- Deploying a bridge app before its image, gateway token, app registration secret, and device identity are known creates placeholder states and repeated Bicep runs with different meanings.
- OpenClaw remote Gateway write calls require a human-approved device/operator identity. This cannot and should not be silently bypassed.
- Image builds do not belong inside Terraform/Bicep desired state.

New strategy:

1. **Use Terraform as the canonical IaC path**, because the user generally prefers Terraform and `azapi` can provision preview ARM resources when first-party providers lag.
2. **Use two Terraform layers only**:
   - `platform`: stable Azure substrate, no deployed containers.
   - `apps`: all deployed app/container resources and all app configuration.
3. **Use scripts for build, generated inputs, and interactive flows**:
   - ACR remote builds.
   - Resolve image digests.
   - Generate OpenClaw bridge device identity.
   - Generate OpenClaw bridge device identity.
   - Guide the user through OpenClaw device approval.
4. **Terraform owns durable resources; scripts prepare inputs**. Scripts should not create or mutate ACA apps behind Terraform.
5. **Avoid dummy images/placeholders where possible**. If an app cannot be validly deployed yet, do not deploy it until its required inputs exist.

## Target deployment model

### Layer 1: `platform`

Owns only durable shared infrastructure:

- Resource group.
- ACR.
- VNet/subnets/private DNS.
- ACA environment for private MCP.
- Standard ACA environment for bridge.
- ACA SandboxGroup and VNet connection.
- Foundry/Azure AI account, project, model deployment.
- Platform-level managed identities/RBAC.

Must not own:

- Container Apps.
- Standard ACA bridge app.
- App revisions/images/env vars/secrets.
- Teams/Agent 365 app-specific config.

### Layer 2: `apps`

Owns all deployed app resources and app runtime configuration:

- Private incidents MCP Container App.
- Standard ACA bridge Container App.
- Image digest references.
- App env vars and secrets.
- Bridge gateway token and OpenClaw device private key.
- Any later Teams/Agent 365 settings that require changing app resources.

Must not own:

- Platform resources from layer 1.

Important rule: **a resource is owned by exactly one Terraform layer**. If Teams/Agent 365 work requires changing bridge app env vars or secrets, that still belongs in `apps`; it is not a new Terraform layer.

### Generated tfvars

Use stable, explicit generated variable files:

```text
terraform.tfvars                       human choices
generated.images.auto.tfvars.json      image digests from build script
generated.bridge.auto.tfvars.json      bridge identity/token values from setup/pairing script
```

These generated files should be ignored by git if they contain secrets or environment-specific values.

### Image build flow

Terraform should not build images.

Recommended flow:

1. Script runs ACR remote builds for:
   - `image\Dockerfile` -> OpenClaw runtime image.
   - `bridge\Dockerfile` -> bridge image.
   - `private-incidents-mcp\Dockerfile` -> private MCP image.
2. Script resolves immutable digests.
3. Script writes `generated.images.auto.tfvars.json`.
4. `terraform apply` in the `apps` layer deploys those exact digests.

Later, a GitHub Actions release path can build public/private GHCR or ACR images and write/publish digest values. Do not force GHCR for the local inner loop yet because it slows development.

## Current implementation state

The repository now uses the Terraform two-layer strategy:

- `bridge\` FastAPI app with:
  - `GET /health`
  - `POST /invoke`
- `terraform\platform\` for base Azure resources, no containers.
- `terraform\apps\` for private MCP and bridge apps.
- `scripts\build_images.py` builds ACR images and writes digest tfvars.
- `scripts\setup_bridge_tfvars.py` creates bridge identity inputs and writes bridge tfvars.
- `scripts\prepare_control_ui.py` patches the current sandbox Control UI bootstrap settings and prints Gateway URL/token/device ID.
- `scripts\sandbox_gateway.py` reusable sandbox lifecycle code.
- `scripts\sandbox_run_gateway.py` wrapper to run/wake OpenClaw in ACA Sandbox.

Validated learnings from the transitional implementation:

- Standard ACA bridge environment can be created with `Microsoft.App/managedEnvironments@2025-07-01`.
- Standard ACA bridge app can be created with `Microsoft.App/containerApps@2025-07-01`.
- Standard ACA supports user-assigned managed identity for bridge Azure API calls and ACR pull.
- The previous ACA Express bridge path was abandoned because outbound Teams replies failed TLS validation behind an undocumented ADC egress proxy root CA.
- `/invoke` requires:
  - Stable gateway token.
  - Standard ACA managed identity with Sandbox Data Owner role.
  - Consistent DataDisk volume/token pairing. Reusing a sandbox started with a different gateway token causes `gateway token mismatch`.
  - Approved OpenClaw bridge device identity for write-scoped `agent` calls. In the current flow, no device token copy is needed after UI approval; the bridge uses its stable private key.

Important: the old `azd` + Bicep path was a prototype and has been removed from the active deployment path.

## Current architecture decisions that remain valid

### Bridge is separate from the suspendable OpenClaw sandbox

Do not put Teams/Agent webhook receiving inside the same suspended ACA Sandbox. The bridge receives incoming HTTP requests and wakes the sandbox. If the bridge itself lives only inside a suspended sandbox, there is a circular dependency.

```text
Bridge
  tiny, public HTTPS, webhook receiver, scale-to-zero capable

OpenClaw ACA Sandbox
  heavier runtime, DataDisk, private MCP VNet access, suspend/resume
```

### Bridge uses standard Azure Container Apps

ACA Express was evaluated for the bridge, but Teams replies exposed an outbound TLS trust problem. ACA Express egress presented certificates issued by `CN=ADC Egress Proxy Root CA`; no public/internal documentation was found for a supported customer contract to retrieve/trust that root CA or disable Express egress inspection for container apps.

Decision: use standard Azure Container Apps for the bridge. See `docs\adr\0001-standard-aca-bridge.md`.

Standard ACA gives the bridge public HTTPS ingress, managed identity, ACR pull through managed identity, and documented outbound networking options.

### Bridge does not need VNet integration

The bridge path is public/control-plane oriented:

- Teams and Agent 365 call bridge over public HTTPS.
- Bridge calls Azure APIs over public HTTPS.
- Bridge calls exposed OpenClaw Gateway URL over public HTTPS.
- Later, bridge/agent calls Agent 365 and Work IQ public endpoints.

The OpenClaw sandbox still needs VNet because it reaches private incidents MCP.

### Bridge uses managed identity

Standard ACA supports managed identity. Bridge Azure API calls should use the bridge user-assigned managed identity, not an Entra app registration/client secret.

### All SDK work should be Python

Use Python for Teams, Microsoft 365 Agents SDK, and Agent 365 SDK integration.

Relevant docs:

- Microsoft Teams SDK for Python: https://learn.microsoft.com/en-us/python/api/msteams-sdk-python/overview?view=msteams-sdk-python-latest
- Teams SDK welcome: https://learn.microsoft.com/en-us/microsoftteams/platform/teams-ai-library/welcome
- Microsoft 365 Agents SDK Python reference: https://learn.microsoft.com/en-us/python/api/agent-sdk-python/agents-overview?view=agent-sdk-python-latest
- Microsoft Agent 365 SDK and CLI: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/

## OpenClaw Gateway auth and pairing model

The bridge has two separate auth concerns:

1. **Azure API auth**: bridge needs permission to create/wake/list ACA Sandboxes.
2. **OpenClaw Gateway auth**: bridge needs permission to call Gateway methods such as `agent`.

Gateway behavior learned so far:

- Shared gateway token is required to connect to the sandbox Gateway.
- The same token must be used for the sandbox process and bridge client.
- Reusing an old sandbox that was started with a different token causes `gateway token mismatch`.
- Token-only remote clients do not get `operator.write`.
- A stable bridge device identity must be approved in OpenClaw Control UI.
- After approval, the bridge uses:
  - stable Ed25519 private key
  - approved Gateway-side device entry
  - requested operator scopes

Pairing is intentionally manual because it is the security boundary.

Expected flow:

1. App setup generates gateway token and bridge device key.
2. Apps Terraform deploys bridge with token and private key.
3. First `/invoke` starts/wakes sandbox and triggers pairing requirement.
4. User opens OpenClaw Control UI and approves the bridge `deviceId`.
5. `/invoke` should now work non-interactively. No approved device token copy is needed in the current flow.

## Product naming

Use these names consistently:

| Name | Purpose | Doc |
| --- | --- | --- |
| Teams SDK | Primary SDK for Teams agents/apps. Former Teams AI Library. Handles Teams-specific app/bot/event plumbing. | https://learn.microsoft.com/en-us/microsoftteams/platform/teams-ai-library/welcome |
| Microsoft 365 Agents SDK | Multichannel conversational agent framework. Receives channel activities, routes to handlers, sends responses. | https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/agents-sdk-overview |
| Microsoft Agent 365 SDK | Enterprise layer: Agent identity, notifications, observability, governed Work IQ MCP, blueprint/governance. Complements, does not replace Teams SDK / Microsoft 365 Agents SDK. | https://learn.microsoft.com/en-us/microsoft-agent-365/developer/ |
| Agent 365 CLI | CLI for blueprint, identity, config, MCP integration, publishing, and Azure deployment. | https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-cli |
| Work IQ MCP | Governed MCP servers for Mail, Calendar, Teams, SharePoint, OneDrive, User, Word, etc. | https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview |

## Revised milestone plan

### Milestone 0: Refactor deployment model

Purpose: replace the transitional `azd` + Bicep deployment strategy with the two-layer Terraform strategy before adding more app/channel complexity.

Deliverables:

- `terraform/platform` or equivalent root:
  - platform resources only
  - no deployed containers
- `terraform/apps` or equivalent root:
  - private MCP app
  - bridge app
  - app images/config/secrets
- Python guide/build/setup scripts:
  - build images in ACR and write digest tfvars
  - generate bridge identity/token tfvars
  - guide OpenClaw approval
  - run verification checks
- Updated README with phase-by-phase commands.
- Remove or clearly mark old `azd` + Bicep flow as transitional/deprecated.

Definition of done:

- Platform layer can apply repeatedly from clean state.
- Apps layer can apply after images exist.
- No Azure resource is owned by more than one layer.
- Scripts do not create/mutate ACA app resources behind Terraform.

### Milestone 1: Bridge can wake and talk to OpenClaw

Purpose: prove the core platform split before Teams complexity.

Deliverables:

- Bridge service in:

```text
openclaw-on-azure\bridge\
```

- Endpoints:

```text
GET  /health
POST /invoke
```

- `/invoke` accepts:

```json
{
  "conversationId": "test",
  "message": "List services from private incidents MCP"
}
```

- Bridge behavior:
  1. Use bridge managed identity for Azure API calls.
  2. Find existing OpenClaw sandbox for configured DataDisk or create one.
  3. Ensure sandbox is running.
  4. Get sandbox public Gateway URL.
  5. Connect to OpenClaw Gateway with stable token and approved device identity.
  6. Call Gateway `agent` method.
  7. Return plain text result.

Notes:

- Keep bridge image tiny.
- Do not include OpenClaw runtime/model dependencies in bridge.
- Do not call private incidents MCP directly from bridge.
- Bridge should be stateless except small request tracking.
- Store durable OpenClaw state only in sandbox DataDisk.

Definition of done:

- `/health` works on deployed bridge.
- `/invoke` wakes or reuses sandbox.
- `/invoke` forwards a text prompt to OpenClaw and returns a response.
- Pairing flow is documented and repeatable.
- No Teams/Agent 365 complexity yet.

Status: complete. The README records deployed `/health` and `/invoke` success after bridge device approval.

### Milestone 2: Teams 1:1 chat to OpenClaw

Purpose: prove Teams can talk to OpenClaw through the bridge.

Deliverables:

- Add Teams SDK for Python to bridge.
- Add `/api/messages` endpoint.
- Add Teams app/bot manifest and registration docs/scripts.
- Support 1:1 chat only.
- Map Teams conversation ID to OpenClaw conversation/session ID.
- Text in, plain text out.

Implementation status:

- Bridge now hosts a Teams SDK `/api/messages` endpoint.
- Milestone 2 validated the `personal` conversation path.
- The Milestone 2 Teams session key was based on the Teams conversation ID.
- The apps Terraform layer owns the Azure Bot resource, Teams channel, and bridge app Teams credential settings.
- Scripts prepare generated Teams tfvars and package the Teams app for sideloading.
- Status: complete. Deployed `/health`, direct `/invoke`, and Teams package generation have been revalidated after bridge device approval.

Architecture:

```text
Teams 1:1 chat
  -> bridge /api/messages
  -> ensure OpenClaw sandbox running
  -> OpenClaw Gateway
  -> Teams reply
```

Docs:

- Build your first Teams agent: https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/build-first-agent
- Teams SDK Python API: https://learn.microsoft.com/en-us/python/api/msteams-sdk-python/overview?view=msteams-sdk-python-latest
- Teams SDK welcome: https://learn.microsoft.com/en-us/microsoftteams/platform/teams-ai-library/welcome

### Milestone 3: Teams group chat/channel behavior

Purpose: make OpenClaw participate where work happens.

Deliverables:

- Add `groupChat` and later `team` scopes.
- Handle @mentions.
- Strip bot mention from prompt text before sending to OpenClaw.
- Preserve conversation/thread IDs.
- Add typing indicators and basic error messages.
- Optional: add emoji reactions once a message is accepted/processed.

Implementation status:

- Manifest now includes `personal`, `groupChat`, and `team` bot scopes.
- Bridge accepts `personal`, `groupchat`, `channel`, and `team` conversation types.
- Personal chats still send plain text directly.
- Group chats and channel/team conversations receive mentions by default and can observe unmentioned messages when Teams grants the RSC permissions in the manifest.
- Bot mentions are stripped before forwarding prompts to OpenClaw.
- OpenClaw session keys preserve Teams conversation IDs, and channel/team requests include team, channel, and thread identifiers where present.
- The bridge sends a Teams typing activity before starting OpenClaw work and keeps the existing progress/error stream messages.
- The bridge now sends a Teams event metadata envelope to OpenClaw with signal type, response contract, conversation/thread IDs, activity IDs, sender, targeting, mention status, and reaction types. Signal types include `explicit_bot_mention`, `targeted_private_message`, `reply_in_thread_without_bot_mention`, `reaction_to_message`, and `undirected_message`.
- The bridge keeps bounded in-memory Teams history per session/thread and sends OpenClaw a compact context window, not only the latest message and not the whole conversation. The context includes recent local events, reply/reaction anchors when already observed, latest OpenClaw answer when available, and hard event/character limits.
- Default context window sizes: 18 events for `must_answer`, 12 for replies, 8 for weak undirected messages, and 6 for reactions. Environment knobs include `OPENCLAW_TEAMS_MEMORY_MAX_EVENTS`, `OPENCLAW_TEAMS_MEMORY_EVENT_CHARS`, `OPENCLAW_TEAMS_CONTEXT_MAX_CHARS`, `OPENCLAW_TEAMS_CONTEXT_MUST_ANSWER_EVENTS`, `OPENCLAW_TEAMS_CONTEXT_REPLY_EVENTS`, `OPENCLAW_TEAMS_CONTEXT_WEAK_SIGNAL_EVENTS`, and `OPENCLAW_TEAMS_CONTEXT_REACTION_EVENTS`.
- Current memory is bridge-local and demo-grade; production should replace or augment it with durable conversation state plus Teams Graph/RSC retrieval.
- Channel troubleshooting showed Teams delivered the channel mention to `/api/messages` and OpenClaw completed, but Teams streaming responses were not visible in channel UI. The bridge now limits streaming to 1:1 personal chats and uses non-streaming replies for group chat/channel replies.
- Follow-up channel testing showed reactions, unmentioned channel messages, and thread messages were received through Teams/RSC. Reactions and weak signals reached OpenClaw, but OpenClaw returned `NO_RESPONSE`, so the bridge correctly suppressed public replies.
- Signal tuning now treats plain-text `OpenClaw` references as `textual_bot_name_mention` with `must_answer`, even without an explicit Teams mention. It also treats channel-thread replies as thread replies based on the Teams `conversation.id` `;messageid=...` root and promotes replies in threads where OpenClaw already answered to `must_answer`.
- Processing reactions now use the documented Teams SDK reaction client (`ctx.api.reactions.add`) instead of sending a `messageReaction` activity. `OPENCLAW_TEAMS_ADD_REACTIONS=true` by default; failed reaction writes are recorded in `/diag/teams` and do not block the answer.
- For forwarded non-personal messages, the bridge can add temporary eyes (`1f440_eyes`) as status UX and removes it after OpenClaw finishes deciding, whether OpenClaw answers, returns `NO_RESPONSE`, or asks for a final semantic reaction.
- The bridge remembers OpenClaw-sent Teams message IDs in bridge-local memory. Reactions to those messages are forwarded as feedback context; reactions to unrelated human messages are ignored by default and recorded as `ignoredReactionToNonOpenClawMessage`.
- RSC manifest permissions allow unmentioned channel/group messages to be received after chat/team install consent. Those messages use an `observe_then_maybe_answer` response contract; OpenClaw can return exactly `NO_RESPONSE` to avoid jumping in.
- The bridge runtime handles targeted private messages if Teams sends `recipient.is_targeted`. The default sideload package stays validator-safe; `scripts.package_teams_app --preview-targeted-messages` builds an alternate manifest 1.29 package with `supportsTargetedMessages: true` and root `supportsChannelFeatures: "tier1"` for tenants/clients with Teams Public Preview enabled.
- Status: implemented and deployed in the current environment. The Teams package has been refreshed at `.local\ehvw\teams\openclaw-teams.zip`; upload it to Teams before testing group chat and channel scopes.

Important auth behavior:

- In group chat, one activity has one sender.
- OBO token, if used, is for the user who invoked/consented, not for every participant.
- Other users must invoke/consent separately for their private data.

Docs:

- Teams SSO overview: https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/authentication/bot-sso-overview
- Teams group/channel bot conversations: https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-and-group-conversations
- Build 2026 collaborative Teams agents blog: https://devblogs.microsoft.com/microsoft365dev/build-collaborative-agents-where-work-happens/
- ADR 0002: `docs\adr\0002-teams-event-routing-and-reactions.md`
- ADR 0003: `docs\adr\0003-targeted-messages-preview-package.md`

### Milestone 3.5: Teams collaborative UX polish

Purpose: make OpenClaw feel less like a command bot and more like a polite teammate in chats, channels, and threads before moving to full Agent 365 identity.

Build/DEM334/DEM332 takeaways:

- **Manners**: agents should use quoted replies, thread locality, and lightweight reactions instead of flooding public chat.
- **Privacy**: targeted private messages let a user interact with the agent privately inside a shared channel/group context, but this is public developer preview and requires `supportsTargetedMessages`.
- **Polish**: good Teams agents use Markdown, Adaptive Cards where useful, feedback/citations/sensitivity labels where applicable, and clear progress cues. Streaming remains reliable for 1:1; channel/group experiences should use normal sends/replies plus reactions.

Deliverables:

- Use `ctx.reply()` for non-personal responses by default so Teams renders a quoted reply and keeps channel answers in the current thread.
- Keep 1:1 chat on Teams streaming.
- Use the documented `ctx.api.reactions.add(conversation_id, activity_id, reaction_type)` path for agent reactions.
- Add temporary eyes (`1f440_eyes`) when a forwarded non-personal message starts processing and remove it when OpenClaw finishes deciding.
- Add like (`like`) for simple gratitude in an active OpenClaw thread instead of a noisy text reply.
- Keep reaction failures non-blocking and observable through `/diag/teams`.
- Add an opt-in preview Teams package flag for targeted private messages without breaking the default validator-safe package.
- Update README with a demo script for quoted/threaded replies, received reactions, sent reactions, gratitude acknowledgement, weak-signal intervention, and targeted-message preview.

Implementation status:

- `OPENCLAW_TEAMS_QUOTED_REPLIES=true` by default. Set it to `false` to use unquoted `ctx.send()` in collaborative scopes.
- `OPENCLAW_TEAMS_ADD_REACTIONS=true` by default. If a Teams scope rejects reaction writes, the bridge records `reactionSendFailed` and still completes the text response.
- OpenClaw can request a reaction through a control line that the bridge strips from visible Teams text: `TEAMS_REACTION: eyes|like|heart|smile|surprised|check`. Reaction-only output is supported; reaction plus visible text is also supported.
- Current decision: bridge owns fast transport/status reactions such as temporary eyes; OpenClaw owns semantic/social reactions such as heart, smile, surprised, check, or like. Do not expand bridge static rules into semantic judgment. If classification is needed later, use it only as a forwarding throttle in high-volume channels.
- Demo-grade implementation: OpenClaw-sent Teams message IDs are remembered in bridge-local memory so reactions to OpenClaw messages are forwarded as feedback, while reactions to unrelated human messages are ignored by default.
- Demo-grade implementation: temporary eyes are removed when OpenClaw finishes deciding, whether it answers or returns `NO_RESPONSE`; any OpenClaw-requested final semantic reaction is then added.
- `scripts.package_teams_app --preview-targeted-messages` emits a separate manifest 1.29 package with `supportsTargetedMessages: true` and `supportsChannelFeatures: "tier1"`.
- Targeted messages remain preview-limited: they are private inside the shared context, expire after 24 hours, and do not support reactions, replies, or forwarding.
- Adaptive Cards, structured feedback buttons, citations, and AI/sensitivity labels are intentionally deferred until OpenClaw has a stable answer schema and Agent 365 identity.

Definition of done:

- Unit tests cover quoted/reaction defaults and gratitude reaction policy.
- Stable Teams package still omits `supportsTargetedMessages` and uploads in normal tenants.
- Preview Teams package can be generated explicitly for tenants/clients that accept targeted-message preview schema.
- README demo guide shows how to test quoted replies, threaded replies, sent/received emoji reactions, weak signals, and targeted preview behavior.

### Milestone 4: Agent 365 identity registration

Purpose: turn the working Teams bridge experience into a governed Agent 365 identity. Do not revisit Teams channel UX here; mentions, RSC observation, quoted/threaded replies, reactions, targeted-message preview packaging, and the OpenClaw reaction control channel are already implemented and documented.

Deliverables:

- Create an Agent 365 blueprint for OpenClaw that points at the existing bridge `/api/messages` endpoint.
- Publish/create an Agent 365 instance for the current tenant.
- Verify whether Agent 365 creates or links an **agent user** and capture its object ID, display name, UPN, and lifecycle owner.
- Keep the existing Teams bot package as the fallback/demo path until the Agent 365 instance is confirmed in Teams.
- Document exactly which parts of the current Teams manifest remain owned by Teams app packaging versus Agent 365 blueprint/configuration.
- Add generated Agent 365 identifiers to ignored/generated tfvars or `.local\<suffix>\...` files; do not hard-code tenant-specific IDs in source.

Definition of done:

- OpenClaw appears as a governed agent/instance in the relevant Microsoft 365 or Agent 365 admin surfaces.
- The same deployed bridge endpoint receives messages from the Agent 365 path.
- Existing Teams package demos still work after Agent 365 registration.
- Handoff records the authoritative create/update/delete commands for the blueprint and instance.

Docs:

- Agent 365 SDK and CLI: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/
- Agent 365 CLI: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-cli
- Create agent instances: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/create-instance
- ADR 0004: `docs\adr\0004-agent-identity-before-workiq.md`

Implementation status:

- Milestone 4 setup has been run against the current tenant.
- The Agent 365 CLI was updated and the tenant has an `Agent 365 CLI` client app with public-client redirects, `wids`, required Microsoft Graph delegated permissions, and admin consent.
- The Agent 365 blueprint is created/reused, the bridge `/api/messages` endpoint is registered, and `a365 query-entra inheritance` reports effective inheritance for all five resources.
- The Agent 365 package is generated and OpenClaw-branded at `.local\ehvw\agent365\manifest\manifest.zip`.
- Current blocker: waiting for the Agent 365 license/tenant enablement before the browser-only publish/instance steps can be completed.
- The script uses the existing `terraform\apps` `bridge_url` output and prepares `.local\<suffix>\agent365\a365.config.json` with:
  - `messagingEndpoint = https://<bridge-fqdn>/api/messages`
  - `needDeployment = false`
  - `deploymentProjectPath = "."`
- The default script path is Agent 365 AI teammate setup because that is the path expected to create an agent user identity. Use `--blueprint-agent` only when testing blueprint registration without an Entra user.
- The script captures non-secret generated values from `.local\<suffix>\agent365\a365.generated.config.json` into `.local\<suffix>\agent365\openclaw-agent365-identifiers.json`; do not commit any `.local` output.
- Existing Teams app packaging remains the fallback/demo path. Agent 365 owns blueprint, instance, agent user lifecycle, admin-center publishing, and the Developer Portal API-based endpoint configuration.

Authoritative commands for the current approach:

```powershell
cd D:\agent-demos\openclaw-on-azure

# Prepare local workspace and print exact commands for the deployed bridge.
uv run python -m scripts.setup_agent365

# Create or update the Agent 365 blueprint/AI teammate registration.
uv run python -m scripts.setup_agent365 --run-setup

# If Frontier/AI teammate is unavailable, create a blueprint-backed M365 agent only.
uv run python -m scripts.setup_agent365 --blueprint-agent --run-setup

# Capture non-secret IDs after setup.
uv run python -m scripts.setup_agent365 --capture

# Update endpoint registration after a bridge URL change.
cd D:\agent-demos\openclaw-on-azure\.local\<suffix>\agent365
a365 setup blueprint --update-endpoint https://<new-bridge-fqdn>/api/messages

# Publish package for Microsoft 365 admin center upload.
cd D:\agent-demos\openclaw-on-azure
uv run python -m scripts.setup_agent365 --publish

# Preview destructive cleanup.
cd D:\agent-demos\openclaw-on-azure\.local\<suffix>\agent365
a365 cleanup --dry-run
a365 cleanup instance --dry-run
a365 cleanup blueprint --endpoint-only
```

Manual Agent 365 browser steps:

Status: blocked until Agent 365 licensing is available.

1. Configure the blueprint in Teams Developer Portal. Use `developerPortalConfigurationUrl` from `.local\<suffix>\agent365\openclaw-agent365-identifiers.json`.
2. Set **Agent Type** to **API Based** and **Notification URL** to the bridge `/api/messages` URL.
3. Upload `.local\<suffix>\agent365\manifest\manifest.zip` in Microsoft 365 admin center under **Agents > All agents > Upload custom agent**.
4. Create/request the instance in Teams Apps and approve pending requests at `https://admin.cloud.microsoft/#/agents/all/requested`.
5. Verify the agent user object ID, display name, UPN, and lifecycle owner; record those non-secret values in `.local\<suffix>\agent365\openclaw-agent365-identifiers.json` and summarize them in this option path without hard-coding tenant-specific IDs in source.

### Milestone 5: Agent-owned identity and auth boundary

Purpose: give OpenClaw a clear enterprise identity model: when it acts as itself, when it acts for a human, and how that identity is represented in prompts, logs, and tool calls.

Questions to answer and implement:

- What agent user or service principal is created by Agent 365 for this OpenClaw instance?
- Which identity should OpenClaw use for autonomous actions such as posting status, sending reminders, or maintaining its own mailbox/calendar?
- Which flows require OBO for the invoking user, especially private mail/calendar/files/chats?
- How should a Teams activity map to identity context in the OpenClaw prompt: sender, tenant, conversation, targeted/private flag, and agent identity?
- How should consent be requested and cached per user without assuming a group chat grants access to every participant?

Required bridge changes:

- Add an identity context block to the Teams/Agent prompt envelope:
  - agent instance ID / agent user ID when known
  - incoming human sender ID and display name
  - auth mode available for this turn: `agent_identity`, `obo_user`, `none`, or `unknown`
  - privacy boundary: public channel/group, targeted private message, or 1:1
- Add diagnostics that show chosen auth mode without logging tokens or private data.
- Keep OpenClaw Gateway approval/device identity separate from Microsoft 365/Agent 365 identity; they are different trust boundaries.

Identity rules:

| Scenario | Identity |
| --- | --- |
| Agent posts/replies as teammate | Agent identity |
| Agent sends its own reminder/status | Agent identity |
| "What is on my calendar?" | OBO of invoking user |
| "Summarize my unread mail" | OBO of invoking user |
| Agent schedules on behalf of user | OBO of invoking user |
| Agent needs another participant's private data | That participant must invoke/consent, or use another governed/admin-approved pattern |

Definition of done:

- Prompt envelope and diagnostics make the identity boundary explicit for every Teams/Agent turn.
- At least one agent-identity action and one OBO-required scenario are documented with expected behavior.
- No feature assumes group/channel membership implies access to all participants' private data.

Docs:

- Agent 365 identity: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/identity
- Agent 365 get started: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/get-started
- Teams SSO/OBO overview: https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/authentication/bot-sso-overview

### Milestone 6: Work IQ MCP with explicit identity selection

Purpose: give OpenClaw governed Microsoft 365 data/actions after identity is explicit, not before.

Scope:

- Start with read-only Work IQ queries that demonstrate identity selection:
  - OBO user: "What is on my calendar?"
  - OBO user: "Summarize my unread mail."
  - Agent identity: "What reminders/status items does OpenClaw own?"
- Then add write actions only with clear confirmation UX:
  - Agent posts status as itself.
  - User-approved calendar/mail action via OBO.
  - Optional public share after targeted private draft/approval.
- Defer broad SharePoint/OneDrive/Word actions until Mail/Calendar/Teams identity behavior is proven.

Bridge/OpenClaw requirements:

- Tool calls must carry the selected identity mode explicitly.
- If a requested tool requires OBO and no user token/consent exists, OpenClaw must ask that user to authenticate rather than silently failing or using agent identity.
- If a requested tool would expose private user data in a public thread, prefer targeted private response or ask before sharing publicly.

Docs:

- Work IQ MCP overview: https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview
- Work IQ Teams reference: https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-teams-work-iq

## Risks and limitations

### ACA Express limitation and bridge target

ACA Express is no longer the bridge target. Teams validation found outbound TLS interception by an undocumented `ADC Egress Proxy Root CA`, and no supported customer mechanism was found to retrieve/trust that CA or disable Express egress inspection for container apps.

Impact:

- Bridge runs on standard Azure Container Apps.
- Bridge uses managed identity instead of an app registration secret for Azure API calls.
- Future egress control should use documented standard ACA networking features.

### Teams and Agent 365 webhook expectations

Teams/Agent endpoint needs a stable public HTTPS URL and responsive `/api/messages`.

If OpenClaw sandbox wake-up is slow:

- Bridge should respond quickly with "starting OpenClaw".
- Then use proactive/follow-up message when supported.
- For Milestone 1, synchronous response is acceptable only if wake+OpenClaw response is reliably fast.

### OBO in group chat

Do not assume group chat gives access to every participant's private data. OBO is tied to the invoking/consenting user. Use agent identity for agent-owned actions and OBO for the current human user's private context.

## Immediate next step for future work

Resume Milestone 4 after the Agent 365 license/tenant enablement is available. Start with the browser-only steps in the Milestone 4 section: Developer Portal API-based endpoint configuration, Microsoft 365 admin-center package upload, Teams instance request/approval, then record agent instance/user identifiers. Treat Teams collaborative UX as complete baseline; the next code milestone after registration is Milestone 5 identity context in the prompt envelope and diagnostics.
