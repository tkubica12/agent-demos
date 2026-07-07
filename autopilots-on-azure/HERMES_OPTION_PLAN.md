# Hermes option plan: Autopilots on Azure

This plan describes the migration from the original OpenClaw-on-Azure project to `autopilots-on-azure`: a shared Azure, Teams, and Agent 365 hosting pattern that can run both OpenClaw and Nous Research Hermes as selectable autopilot runtimes.

The initial target is parity through the OpenClaw option path's milestone 3.5 and milestone 4 surfaces in `OPENCLAW_OPTION_PATH.md`: shared Teams routing/reactions/context and Agent 365 registration. Later identity, Work IQ, and deeper multi-user features should wait until the OpenClaw path is proven and stable.

## Executive decision

Use a common bridge as the Microsoft 365 boundary and make the sandbox runtime pluggable behind bridge-side adapters.

| Decision | Selected direction |
| --- | --- |
| Project name | `autopilots-on-azure` |
| Teams / Agent 365 boundary | Common bridge owns Teams, Bot Framework, Agent 365, Teams context, reactions, and routing |
| Runtime abstraction | Bridge runtime adapters: OpenClaw websocket adapter plus Hermes HTTP adapter |
| Deployment model | Two parallel deployments can coexist, each with its own bridge/bot/runtime settings |
| Rename strategy | Full rename now from OpenClaw-specific project names to neutral autopilot names |

The bridge remains the stable Azure/Microsoft 365 front door. OpenClaw and Hermes become runtime implementations behind a common internal contract. This avoids throwing away the custom Teams behavior already built for milestone 3.5, and it keeps Agent 365 registration independent of the selected agent runtime.

## Why not use Hermes native Teams first?

Hermes has first-party Teams support, but it should not replace the project bridge for the first Hermes milestone.

Hermes native Teams is valuable, but it is a different product boundary. It owns `/api/messages` itself and has its own routing model, allowlist, Adaptive Card approval flow, and Teams lifecycle. The current bridge already implements important project-specific behavior:

- Signal classification for explicit mentions, targeted private messages, thread replies, reactions, and undirected channel messages.
- Bounded Teams context passed to the agent.
- `must_answer` versus `observe_then_maybe_answer` semantics.
- Temporary status reactions, semantic `TEAMS_REACTION:` control lines, gratitude reactions, and quoted replies.
- Personal chat streaming while preserving non-streaming channel behavior.
- Agent 365 bridge setup against the same `/api/messages` surface.

Replacing this with Hermes native Teams would force a second Teams/Agent 365 integration path before the core Hermes runtime proof is complete. Instead, Hermes native Teams should be documented as a later optional mode for pure-Hermes deployments.

## Current OpenClaw architecture to preserve

The current project has three useful layers:

1. **Platform layer** - durable Azure substrate: resource group, ACR, VNet/subnets, private DNS, ACA environments, Foundry/Azure AI, SandboxGroup, RBAC, and private MCP environment.
2. **Apps layer** - deployable app resources: bridge app, bridge identity, Bot Service/Teams channel, private incidents MCP app, image references, secrets, and environment variables.
3. **Runtime image and sandbox scripts** - agent-specific runtime image, sandbox lifecycle, runtime boot command, persistent disk/image naming, and bridge-to-runtime protocol.

Most of layers 1 and 2 are runtime-neutral once names and variables are renamed. The runtime image, bootstrapping, and bridge protocol are agent-specific.

## Hermes facts that matter for this design

Hermes Agent is a Python agent runtime and gateway, not just a model. It is positioned as a successor to OpenClaw and includes a migration command (`hermes claw migrate`) for OpenClaw state.

Relevant runtime surfaces:

- Runs as a gateway process with optional OpenAI-compatible API server.
- API server is enabled with `API_SERVER_ENABLED=true`.
- Default API port is `8642`.
- Container deployments must set `API_SERVER_HOST=0.0.0.0`.
- API server requires `API_SERVER_KEY`.
- Useful endpoints include `/health`, `/v1/chat/completions`, `/v1/responses`, `/v1/runs`, and `/api/sessions/{id}/chat`.
- Long-term user memory can be scoped with `X-Hermes-Session-Key`.
- `HERMES_HOME` controls persistent state, normally `~/.hermes`; in Azure it should point at the sandbox data mount.
- Hermes uses SQLite for session state, memory files, skills, MCP tokens, and other durable state. Treat a single Hermes home as single-writer.
- Hermes supports MCP servers via `mcp_servers` configuration, including stdio and HTTP servers.
- Hermes has native Teams support on `/api/messages`, but that should remain a future optional integration mode for this project.

## Target architecture

```text
Teams / Agent 365 / HTTP client
        |
        v
Common bridge ACA app
  - Bot Framework auth and activity handling
  - Teams routing, context, reactions, and response shaping
  - Agent 365-compatible /api/messages endpoint
  - /invoke test endpoint
  - Runtime adapter selection per deployment
        |
        +-- OpenClawRuntimeAdapter
        |     - wakes ACA Sandbox
        |     - talks to OpenClaw Gateway websocket on port 18789
        |     - uses OpenClaw token and device identity
        |
        +-- HermesRuntimeAdapter
              - wakes ACA Sandbox
              - talks HTTP to Hermes API server on port 8642
              - uses API_SERVER_KEY
              - maps Teams/user/thread identity to Hermes session headers
```

Each deployment chooses one runtime. Because the selected deployment model is two parallel deployments, an OpenClaw autopilot and a Hermes autopilot can run side by side with separate bridge app names, bot registrations, secrets, and runtime settings while sharing the common platform layer.

## Proposed repository restructure

Milestone A0 renames the folder from `openclaw-on-azure` to `autopilots-on-azure`. Within it, common code is split from runtime-specific code.

```text
autopilots-on-azure\
  README.md
  OPENCLAW_OPTION_PATH.md
  HERMES_OPTION_PLAN.md
  azure.yaml
  bridge\
    app.py
    runtime\
      __init__.py
      base.py
      openclaw.py
      hermes.py
    teams\
      ...
  runtimes\
    openclaw\
      Dockerfile
      openclaw_gateway\
        bootstrap.py
        start_gateway.py
    hermes\
      Dockerfile
      config\
        config.yaml.template
        env.template
      start_hermes.py
  scripts\
    build_images.py
    setup_app_tfvars.py
    setup_teams_tfvars.py
    setup_agent365.py
    sandbox_runtime.py
    package_teams_app.py
  terraform\
    platform\
    apps\
  teams\
    manifest.template.json
  tests\
```

Rename intent:

- `openclaw_gateway` becomes runtime-specific under `runtimes\openclaw`.
- Common bridge code should not import an OpenClaw client directly from top-level modules.
- Common scripts should use `autopilot`, `runtime`, or `agent_runtime` names.
- Runtime-specific scripts, image labels, ports, and secrets live behind runtime config objects.
- Documentation should say "OpenClaw autopilot" and "Hermes autopilot", not "the OpenClaw project".

## Runtime adapter contract

The first abstraction should be intentionally small. It only needs to cover the bridge's current needs.

```python
class AgentRuntimeAdapter:
    async def invoke(self, request: AgentRequest) -> AgentResponse:
        ...
```

Suggested request fields:

| Field | Purpose |
| --- | --- |
| `prompt` | User-visible instruction or message text |
| `conversation_id` | Stable logical conversation key |
| `user_id` | Stable user key. Use the sender AAD object ID when available; it is the primary key for Hermes memory scoping and future per-user OBO state. Do not use display name, email, or UPN as the primary user key because those can change. |
| `source` | `invoke`, `teams_personal`, `teams_channel`, `teams_reaction`, or `agent365` |
| `must_answer` | Whether the runtime should produce an answer |
| `context` | Bounded Teams/channel/thread context text |
| `metadata` | Runtime-neutral details such as tenant, team, channel, thread, and activity IDs |

Suggested response fields:

| Field | Purpose |
| --- | --- |
| `text` | Assistant response text |
| `reaction` | Optional normalized reaction instruction |
| `raw` | Optional runtime-specific response for diagnostics |

The bridge should own Teams-specific response parsing. Runtime adapters should not call Teams APIs directly.

## OpenClaw adapter

The OpenClaw adapter should wrap the current `OpenClawGatewayClient` behavior:

- Wake or create the ACA Sandbox.
- Connect to the OpenClaw Gateway websocket.
- Use the OpenClaw token and Ed25519 device identity.
- Preserve the current Control UI device approval flow.
- Preserve current `/invoke` and Teams prompt wording until tests are updated.

OpenClaw-specific settings:

| Setting | Example |
| --- | --- |
| Runtime kind | `openclaw` |
| Internal port | `18789` |
| Boot command | OpenClaw Gateway startup |
| Persistent home | existing OpenClaw gateway/config paths |
| Required secrets | gateway token/device identity material |
| Approval model | OpenClaw Control UI device approval |

## Hermes adapter

The Hermes adapter should start with Hermes API server integration, not Hermes native Teams.

Bridge call shape:

- Wake or create the ACA Sandbox.
- Call `GET /health` until Hermes is ready.
- Send turns to `/api/sessions/{id}/chat`, `/v1/responses`, or `/v1/chat/completions`.
- Prefer a sessionful endpoint so Hermes can keep transcript state.
- Set `Authorization` using `API_SERVER_KEY`.
- Set stable session headers:
  - `X-Hermes-Session-Id`: derived from Teams thread/conversation or `/invoke`.
  - `X-Hermes-Session-Key`: derived from deployment, source, and user identity to scope memory.

Recommended first endpoint choice:

1. Use `/api/sessions/{id}/chat` if it provides the cleanest synchronous "one user turn in, one assistant turn out" behavior.
2. Fall back to `/v1/responses` if the session API shape is not stable enough.
3. Use `/v1/chat/completions` only for a minimal stateless smoke test.

Hermes-specific settings:

| Setting | Example |
| --- | --- |
| Runtime kind | `hermes` |
| Internal port | `8642` |
| Boot command | `hermes gateway run` with API server enabled |
| Persistent home | `HERMES_HOME` on sandbox data disk |
| Required secrets | `API_SERVER_KEY`, model/provider credentials |
| Optional secrets | MCP OAuth tokens, Teams native plugin credentials if enabled later |
| Approval model | Hermes approvals config; keep bridge-level Teams behavior separate |

Hermes container requirements:

- Python 3.11-3.13.
- Node.js 22.
- `uv`, `ripgrep`, `git`, and any Hermes runtime dependencies.
- `API_SERVER_ENABLED=true`.
- `API_SERVER_HOST=0.0.0.0`.
- `API_SERVER_PORT=8642`.
- `HERMES_HOME` set to the mounted runtime data path.
- Single replica per Hermes home because of SQLite state.

## Terraform and deployment model

Use one shared platform deployment and multiple app deployments.

Platform remains common:

- Resource group.
- ACR.
- VNet/subnets and private DNS.
- ACA environments.
- Foundry/Azure AI resources.
- SandboxGroup and RBAC.
- Private incidents MCP service and private network path, unless later made optional.

Apps layer becomes deployable per autopilot instance:

| Variable | Purpose |
| --- | --- |
| `autopilot_name` | Logical instance name, e.g. `openclaw` or `hermes` |
| `agent_runtime` | `openclaw` or `hermes` |
| `bridge_image` | Common bridge image tag |
| `runtime_image` | Runtime-specific image tag |
| `runtime_port` | `18789` for OpenClaw, `8642` for Hermes |
| `bot_display_name` | Teams/Agent 365-visible name |
| `teams_app_name` | Manifest display name |
| `agent365_name` | Agent 365 package/app name |
| `sandbox_disk_name` | Runtime-specific persistent disk/image name |
| `runtime_secret_refs` | Secret names mounted/injected into bridge or sandbox |

Because side-by-side deployments are required, use one of these implementation shapes:

1. **Preferred first implementation:** run `terraform\apps` once per autopilot using separate tfvars files and unique suffixes, e.g. `.local\openclaw\apps.tfvars` and `.local\hermes\apps.tfvars`.
2. **Later consolidation:** change `terraform\apps` to accept a map of autopilot deployments and create bridge/bot/runtime resources with `for_each`.

The first shape is easier to migrate and safer for existing OpenClaw state. The second shape is cleaner once both runtimes are stable.

## Naming and full rename plan

Full rename should be done intentionally in one milestone so the repo does not carry mixed mental models.

Rename examples:

| Current | Target |
| --- | --- |
| `openclaw-on-azure` | `autopilots-on-azure` |
| `openclaw-bridge` | `autopilot-bridge` |
| `openclaw-runtime` | `openclaw-runtime` under `runtimes\openclaw` |
| `openclaw_gateway` | `runtimes\openclaw\openclaw_gateway` |
| `setup_bridge_tfvars.py` | `setup_app_tfvars.py` |
| `sandbox_gateway.py` | `sandbox_runtime.py` |
| `OPENCLAW_*` env vars | runtime-specific names or generic `AGENT_RUNTIME_*` names |
| `rg-openclaw-*` | `rg-autopilots-*` for new deployments |

Compatibility note: full rename does not need to preserve old Azure resource names for new deployments. If existing OpenClaw demos must survive, document that they are legacy deployments and create fresh `autopilots-on-azure` environments rather than trying to mutate names in place.

## Teams and Agent 365 branding

Teams and Agent 365 should be instance-branded, not runtime-hardcoded.

Each autopilot deployment needs:

- Display name.
- Short name.
- Description.
- Icon package.
- Bot App ID/client ID.
- Teams manifest package.
- Agent 365 package/config.
- Optional command/help text.

Suggested initial side-by-side names:

| Runtime | Teams short name | Bridge app | Agent 365 name |
| --- | --- | --- | --- |
| OpenClaw | OpenClaw Autopilot | `autopilot-bridge-openclaw-*` | `openclaw-autopilot-*` |
| Hermes | Hermes Autopilot | `autopilot-bridge-hermes-*` | `hermes-autopilot-*` |

The bridge code should not contain these names except as environment variables or generated config.

## MCP and private incidents service

The private incidents MCP service should remain common and runtime-neutral.

OpenClaw currently reaches it through the existing OpenClaw config/bootstrap. Hermes should get equivalent access via `mcp_servers` config:

```yaml
mcp_servers:
  private-incidents:
    url: "${PRIVATE_INCIDENTS_MCP_URL}"
    headers:
      Authorization: "Bearer ${PRIVATE_INCIDENTS_MCP_TOKEN}"
```

Exact auth should follow the existing private MCP pattern. The important design point is that the MCP service is not OpenClaw-specific. It is a private tool surface for any autopilot runtime.

## Milestones

### Milestone A0 - Rename and neutralize the project

Status: implemented as the neutral `autopilots-on-azure` project structure with OpenClaw retained as the first runtime.

Goal: make the repository and documentation describe a neutral autopilot host before adding Hermes code.

Tasks:

- Rename folder/project references to `autopilots-on-azure`.
- Rename README language from OpenClaw-only to runtime-neutral.
- Keep `OPENCLAW_OPTION_PATH.md` as the historical and forward option path for the OpenClaw runtime, with a note that completed milestones are the OpenClaw baseline for the multi-runtime architecture.
- Rename bridge, script, Terraform variable, and generated tfvars concepts from `openclaw` to `autopilot` or `agent_runtime` where they are not truly OpenClaw-specific.
- Move OpenClaw runtime image/bootstrap code under `runtimes\openclaw`.
- Keep OpenClaw-specific protocol and approval code explicitly OpenClaw-named.

Exit criteria:

- Existing OpenClaw deployment path still works under the neutral naming.
- Documentation can explain that OpenClaw is one runtime, not the whole project.

### Milestone A1 - Introduce bridge runtime adapters

Status: implemented for OpenClaw. Hermes remains intentionally unsupported by the bridge runtime factory until the Hermes image/API milestones add a working runtime endpoint.

Goal: make bridge logic independent of OpenClaw protocol details.

Tasks:

- Add `bridge\runtime\base.py` with request/response models.
- Move current OpenClaw websocket logic into `bridge\runtime\openclaw.py`.
- Add runtime selection from environment, e.g. `AGENT_RUNTIME=openclaw`.
- Keep `/invoke` and Teams handlers calling the generic adapter.
- Keep Teams event classification, context, reactions, and response formatting in common bridge code.
- Update unit tests to prove current OpenClaw behavior did not regress.

Exit criteria:

- No Teams handler imports OpenClaw protocol code directly.
- OpenClaw remains functionally equivalent.

### Milestone A2 - Generalize sandbox lifecycle

Status: implemented for OpenClaw with a Hermes dry-run config path. Hermes still does not start until the Hermes runtime image exists in A3.

Goal: make ACA Sandbox startup runtime-configurable.

Tasks:

- Continue generalizing `sandbox_runtime.py` beyond the OpenClaw Gateway startup path.
- Replace OpenClaw hard-coding with `AgentSandboxConfig`.
- Parameterize runtime image, command, port, health path, disk image name, data mount, and labels.
- Keep digest-specific disk image naming so rebuilt runtime images do not reuse stale sandboxes.
- Add runtime-specific config builders for OpenClaw and Hermes.

Exit criteria:

- OpenClaw sandbox still starts with the same behavior.
- A dry-run or unit test can produce Hermes sandbox config without starting Hermes yet.

### Milestone A3 - Build Hermes runtime image

Status: implemented. Hermes runtime image builds, starts in ACA Sandbox, exposes Hermes Gateway `/health` on port 8642, and writes `HERMES_HOME`, `.env`, and `config.yaml` under the data mount. Full bridge `/invoke` integration is handled in A3.5.

Goal: create a Hermes container that can run inside ACA Sandbox and expose the API server.

Tasks:

- Add `runtimes\hermes\Dockerfile`.
- Install Hermes with Python and Node requirements.
- Add startup wrapper that writes/validates `HERMES_HOME`, `.env`, and `config.yaml`.
- Enable API server on `0.0.0.0:8642`.
- Inject model/provider settings through environment variables or generated files.
- Configure private incidents MCP under Hermes `mcp_servers`.
- Add a simple health smoke test against `/health`.

Exit criteria:

- Hermes sandbox starts.
- Bridge or a test command can reach `/health`.
- Hermes persistent state lands under the runtime data mount.

### Milestone A3.5 - Hermes through common Teams bridge

Status: implemented. `AGENT_RUNTIME=hermes` selects `HermesRuntimeAdapter`; `/invoke` works through Hermes, and the same bridge endpoint can be used by the existing Teams app while the deployment is switched to Hermes mode. Side-by-side separate Teams apps remain A5.

Goal: reach the current Teams milestone 3.5 behavior with Hermes behind the common bridge.

Tasks:

- Implement `bridge\runtime\hermes.py`.
- Map bridge conversation/user identity to Hermes session headers with a deterministic rule:
  - `X-Hermes-Session-Id`: derive from the Teams thread/conversation ID or `/invoke` conversation ID.
  - `X-Hermes-Session-Key`: derive from `deployment_name`, `tenant_id`, and sender AAD object ID. If AAD OID is unavailable, fall back to an anonymous key derived from deployment and conversation ID, and log that degraded state.
  - Do not derive the session key from Teams display name, email, UPN, or conversation ID alone.
- When a turn arrives through an Agent 365 registration, set `source = agent365` in the `AgentRequest` and include the Agent 365 instance ID / agent user ID in `metadata` when known.
- Send channel and personal chat prompts to Hermes using a sessionful endpoint.
- Preserve current Teams signal classification and `must_answer` behavior.
- Preserve reactions and response parsing in the bridge.
- Add runtime-specific prompt preamble only where needed to teach Hermes the bridge contract, especially reaction control lines.
- Add tests equivalent to existing Teams bridge tests for Hermes adapter request formation and response parsing.

Exit criteria:

- `/invoke` works against Hermes.
- Teams personal chat works against Hermes.
- Mentioned channel/thread flow works against Hermes.
- Unmentioned observe-only flow does not spam channels.
- Existing reaction/status behavior remains bridge-owned.
- Two different simulated users in the same Teams conversation produce different `X-Hermes-Session-Key` values; the same AAD OID produces the same key across turns.
- Anonymous/fallback session-key mode is visible in diagnostics so it cannot silently mix user memory.

### Milestone A4 - Agent 365 parity for Hermes

Goal: make Hermes deployments register through the same Agent 365 setup path as OpenClaw deployments.

Terminology note: Agent 365 docs and internal material may use **AI teammate**, **Autopilot**, **digital worker**, or **virtual employee** for the user-like agent identity pattern. In this plan, those terms mean the same target only when Agent 365 creates an Entra agent user for the Hermes instance. Blueprint-only registration is not a digital worker/Autopilot identity.

Tasks:

- Make `setup_agent365.py` accept autopilot instance metadata and runtime kind.
- Generate separate Agent 365 config under `.local\hermes\agent365`.
- Ensure Hermes Agent 365 package points to the Hermes bridge `/api/messages` endpoint.
- Keep the same tenant/browser/license prerequisites documented as for OpenClaw.
- Parameterize package names, display names, icons, and descriptions.
- Document side-by-side Agent 365 packages for OpenClaw and Hermes.
- Keep Hermes Agent 365 config independent from Hermes runtime config. The Agent 365 blueprint endpoint belongs under `.local\hermes\agent365`; Hermes API key, `HERMES_HOME`, and runtime secrets belong to the Hermes runtime config.
- Reuse the OpenClaw M4 distinction between AI teammate/Autopilot mode and blueprint-only mode. If Frontier/AI teammate is unavailable, document that Hermes is blueprint-only and does not have a user-like digital-worker identity.

Architecture note:

This milestone uses the **autopilots bridge path**: the common Python ACA bridge owns Bot Framework auth, Teams routing, and Agent 365 registration, then forwards turns to the Hermes sandbox. A separate Foundry-direct Hermes path exists in the broader research/planning material, where Hermes runs as a Foundry Hosted Agent and a generated M365 bridge handles the Bot Framework path. Do not mix the Foundry-direct components with this bridge-path milestone.

Exit criteria:

- OpenClaw and Hermes can have separate Agent 365 package artifacts.
- Agent 365 setup does not care which runtime is behind the bridge, but each runtime has its own `.local\<runtime>\agent365` config and bridge endpoint.
- Hermes Agent 365 blueprint points to the Hermes bridge endpoint, not the OpenClaw endpoint.
- Running Agent 365 setup/capture for Hermes does not overwrite OpenClaw's local Agent 365 config or identifiers.

### Milestone A5 - Side-by-side app deployments

Goal: support OpenClaw and Hermes in the same Azure platform environment.

Tasks:

- Generate separate `.local\openclaw` and `.local\hermes` config/tfvars directories.
- Ensure app resource names include autopilot instance suffixes.
- Ensure each bridge has separate secrets and bot registration.
- Ensure each runtime uses separate sandbox disk/image names.
- Ensure logs and validation commands clearly include instance/runtime names.

Exit criteria:

- OpenClaw and Hermes can be deployed side by side without name, secret, bot, or disk collisions.
- Shared platform resources are not duplicated unnecessarily.

### Milestone A6 - Documentation and operator polish

Goal: make the new project understandable for demo operators.

Tasks:

- Rewrite README around `autopilots-on-azure`.
- Add quickstarts for OpenClaw and Hermes.
- Add a side-by-side deployment guide.
- Add troubleshooting for Hermes API server, session state, SQLite/persistence, and model credentials.
- Explain why Hermes native Teams is not the initial path.
- Explain future optional Hermes-native mode.

Exit criteria:

- A new operator can deploy either OpenClaw or Hermes by following README commands.
- The old OpenClaw-only mental model is gone.

## Deferred after OpenClaw proof

Do not implement these until the current OpenClaw path is proven through the validation in `OPENCLAW_OPTION_PATH.md`:

- Agent identity refinements beyond the current bridge/Agent 365 setup. Defer these until OpenClaw Milestone 5 proves the prompt-envelope identity block, per-user OBO state keyed by AAD object ID, blueprint-only degraded mode, and consent/error behavior.
- Work IQ integration. Defer this until OpenClaw Milestone 6 proves explicit identity selection for Work IQ MCP calls, including no app-only auth, per-user OBO, admin consent for MCP scopes, and no token passthrough to custom MCP servers.
- Multi-user profile isolation beyond separate deployment instances.
- Hermes native Teams adapter mode.
- Hermes dashboard exposure.
- Hermes cron/job management through Teams.
- Full Terraform `for_each` multi-autopilot deployment model.
- Deeper memory/profile integration across OpenClaw and Hermes.
- Foundry-direct Hermes as a Hosted Agent / AI teammate / Autopilot path. Treat it as a separate architecture track and revisit after the bridge-path Agent 365 parity is stable.

## Key risks

| Risk | Mitigation |
| --- | --- |
| Hermes API endpoint shape differs from docs | Start with a narrow smoke test and keep endpoint choice isolated in `HermesRuntimeAdapter` |
| Hermes SQLite state is not horizontally scalable | Keep one replica per Hermes home and one runtime per deployment instance |
| Full rename breaks existing OpenClaw deployment | Treat old deployments as legacy; validate OpenClaw immediately after rename |
| Teams behavior regresses | Keep Teams logic common and test it through the runtime adapter boundary |
| Runtime-specific secrets leak into common code | Use runtime config objects and secret references; do not put Hermes/OpenClaw secrets in shared constants |
| Agent 365 package generation becomes duplicated | Parameterize metadata; keep one setup script |

## Recommended implementation order

1. Rename and neutralize project/docs.
2. Introduce runtime adapter boundary while keeping OpenClaw working.
3. Generalize sandbox lifecycle.
4. Add Hermes image and health smoke.
5. Add Hermes bridge adapter and Teams milestone 3.5 parity.
6. Add Hermes Agent 365 package generation.
7. Enable side-by-side deployments and operator docs.

This order preserves the working OpenClaw implementation while creating the smallest useful seam for Hermes. It also avoids committing to Hermes native Teams, dashboard, or advanced memory operations before the shared Azure bridge model is proven.
