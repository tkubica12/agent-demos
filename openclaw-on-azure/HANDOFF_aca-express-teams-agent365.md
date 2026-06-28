# Handoff: OpenClaw on Azure toward Teams + Agent 365

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
   - Create/rotate Entra app credentials where ACA Express lacks managed identity.
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
- ACA Express environment for bridge.
- ACA SandboxGroup and VNet connection.
- Foundry/Azure AI account, project, model deployment.
- Platform-level managed identities/RBAC.

Must not own:

- Container Apps.
- ACA Express bridge app.
- App revisions/images/env vars/secrets.
- Teams/Agent 365 app-specific config.

### Layer 2: `apps`

Owns all deployed app resources and app runtime configuration:

- Private incidents MCP Container App.
- ACA Express bridge Container App.
- Image digest references.
- App env vars and secrets.
- Bridge gateway token, app registration client ID/secret, and OpenClaw device private key.
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

- ACA Express environment can be created with `Microsoft.App/managedEnvironments@2025-10-02-preview`.
- Express create body uses `properties.environmentMode: "express"` and `workloadProfiles: null`.
- ACA Express bridge app can be created with `Microsoft.App/containerApps@2025-10-02-preview`.
- Express app shape should use:
  - `activeRevisionsMode: "single"`
  - `transport: "auto"`
  - `workloadProfileName: null`
  - explicit scale `{ minReplicas: 0, maxReplicas: 1, rules: [] }`
  - private ACR credentials as Container App registry secrets.
- `az containerapp secret set` and `azd deploy` can fail against ACA Express preview with internal control-plane errors. Prefer declarative app deployment through ARM/Terraform/azapi instead of CLI secret mutation.
- The bridge `/health` path can run successfully on ACA Express.
- `/invoke` requires:
  - Stable gateway token.
  - ACA Express Azure API credential because Express lacks managed identity today.
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

### ACA Express remains the preferred bridge target for now

Use ACA Express for the bridge while evaluating preview limitations:

- Public HTTPS endpoint.
- Automatic scale from zero.
- Good fit for a small Teams/Agent webhook endpoint.

Fallback if ACA Express blocks progress: regular Azure Container Apps with `minReplicas: 1` and managed identity. That costs more but removes several preview limitations.

### Bridge does not need VNet integration

The bridge path is public/control-plane oriented:

- Teams and Agent 365 call bridge over public HTTPS.
- Bridge calls Azure APIs over public HTTPS.
- Bridge calls exposed OpenClaw Gateway URL over public HTTPS.
- Later, bridge/agent calls Agent 365 and Work IQ public endpoints.

The OpenClaw sandbox still needs VNet because it reaches private incidents MCP.

### ACA Express currently needs app registration secrets

ACA Express preview currently lacks managed identity. Bridge Azure API calls need an Entra app registration/client secret for now. Plan to switch to managed identity when ACA Express supports it.

Do not store long-lived secrets in source.

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
  1. Use Azure credential from app registration secret while ACA Express lacks managed identity.
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

### Milestone 2: Teams 1:1 chat to OpenClaw

Purpose: prove Teams can talk to OpenClaw through the bridge.

Deliverables:

- Add Teams SDK for Python to bridge.
- Add `/api/messages` endpoint.
- Add Teams app/bot manifest and registration docs/scripts.
- Support 1:1 chat only.
- Map Teams conversation ID to OpenClaw conversation/session ID.
- Text in, plain text out.

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

- Add `groupchat` and later `team` scopes.
- Handle @mentions.
- Strip bot mention from prompt text before sending to OpenClaw.
- Preserve conversation/thread IDs.
- Add typing indicators and basic error messages.
- Optional: add emoji reactions once a message is accepted/processed.

Important auth behavior:

- In group chat, one activity has one sender.
- OBO token, if used, is for the user who invoked/consented, not for every participant.
- Other users must invoke/consent separately for their private data.

Docs:

- Teams SSO overview: https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/authentication/bot-sso-overview
- Teams group/channel bot conversations: https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-and-group-conversations
- Build 2026 collaborative Teams agents blog: https://devblogs.microsoft.com/microsoft365dev/build-collaborative-agents-where-work-happens/

### Milestone 4: Agent 365 registration around the same bridge

Purpose: move from Teams app/bot demo toward Agent 365 governed agent lifecycle.

Deliverables:

- Use Agent 365 CLI to create/publish blueprint.
- Register same bridge `/api/messages` as API-based agent endpoint.
- Configure Developer Portal:

```text
Agent Type: API Based
Notification URL: https://<bridge>/api/messages
```

- Create an agent instance in Teams.
- Confirm agent appears in Microsoft 365 admin center and Teams.

Docs:

- Agent 365 SDK and CLI: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/
- Agent 365 CLI: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-cli
- Create agent instances: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/create-instance

### Milestone 5: Agent 365 identity / AI teammate path

Purpose: give OpenClaw its own enterprise identity instead of only acting as a bot/app.

Agent 365 identity components:

- Agent blueprint.
- Agent instance.
- Agent user.

Agent user capabilities from docs:

- Marked agentic in directory.
- Own UPN.
- Can be @mentioned.
- Can have mailbox and OneDrive after license assignment.
- Appears in org chart and people cards.
- Can use agent identity authentication for autonomous work.

Auth modes:

| Mode | Identity used | Use |
| --- | --- | --- |
| Agent identity auth | OpenClaw's agent user | Autonomous tasks, posting as itself, own mailbox/calendar |
| OBO | Invoking human user | User-private mail/calendar/files/chats |

Docs:

- Agent 365 identity: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/identity
- Agent 365 get started: https://learn.microsoft.com/en-us/microsoft-agent-365/developer/get-started

### Milestone 6: Work IQ MCP

Purpose: give OpenClaw governed access to Microsoft 365 data and actions.

Work IQ MCP tools to test:

- Work IQ Mail: create/update/delete/reply/search messages.
- Work IQ Calendar: create/list/update/delete events, accept/decline.
- Work IQ Teams: create/update/delete chats, add members, post messages, channel operations.
- Work IQ SharePoint/OneDrive/User/Word later.

Identity rule in group chats:

```text
Group chat has many humans.
One incoming activity has one sender.
OBO = sender / consenting user.
Agent identity = agent user itself.
```

Examples:

| Scenario | Identity |
| --- | --- |
| "What is on my calendar?" | OBO of invoking user |
| "Summarize my unread mail" | OBO of invoking user |
| Agent posts/replies as teammate | Agent identity |
| Agent schedules from its own calendar | Agent identity |
| Agent schedules on behalf of user | OBO of invoking user |
| Agent needs another participant's private data | That participant must invoke/consent, or use another governed/admin-approved pattern |

Docs:

- Work IQ MCP overview: https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview
- Work IQ Teams reference: https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-teams-work-iq

## Risks and limitations

### ACA Express limitations

ACA Express is preview. Current documented/observed gaps:

- No managed identity.
- No VNet integration.
- No custom domains.
- No health probes.
- No advanced autoscaling.
- No SLA during preview.
- Limited regions.
- Control-plane bugs with some CLI/azd secret operations.

Impact:

- Bridge must use app registration secret until managed identity is supported.
- Bridge cannot directly reach private VNet resources.
- Bridge app deployment should be IaC/ARM/azapi-driven rather than CLI secret mutation.
- Regular ACA fallback remains valid if Express preview blocks Teams reliability.

### Teams and Agent 365 webhook expectations

Teams/Agent endpoint needs a stable public HTTPS URL and responsive `/api/messages`.

If OpenClaw sandbox wake-up is slow:

- Bridge should respond quickly with "starting OpenClaw".
- Then use proactive/follow-up message when supported.
- For Milestone 1, synchronous response is acceptable only if wake+OpenClaw response is reliably fast.

### OBO in group chat

Do not assume group chat gives access to every participant's private data. OBO is tied to the invoking/consenting user. Use agent identity for agent-owned actions and OBO for the current human user's private context.

## Immediate next step for future work

Do **not** start Teams integration yet.

Recommended next step:

1. Refactor deployment into the two Terraform layers.
2. Add image build script that writes digest tfvars.
3. Move bridge app ownership into the apps layer.
4. Make bridge credential/pairing scripts generate tfvars rather than mutate Container Apps.
5. Revalidate Milestone 1 `/invoke`.
6. Start Teams 1:1 only after `/invoke` is stable.
