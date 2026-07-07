# Autopilots on Azure: current plan and specification

This is the active specification for the project from the current implementation point forward. OpenClaw and Hermes are peer runtimes behind one Microsoft 365 bridge pattern. Agent 365 is the primary Microsoft 365 packaging and installation path.

## Current baseline

The repository has one shared Azure hosting pattern:

```text
Agent 365 / Teams / /invoke
  -> common bridge Container App
  -> ACA Sandbox runtime selected by AGENT_RUNTIME
       -> OpenClaw Gateway on port 18789
       -> Hermes API server on port 8642
  -> private incidents MCP Container App
```

Implemented and verified:

- `autopilots-on-azure` project layout.
- `bridge\runtime\AgentRuntimeAdapter` abstraction.
- `OpenClawRuntimeAdapter`.
- `HermesRuntimeAdapter`.
- Generic `AgentSandboxConfig` lifecycle for ACA Sandbox.
- OpenClaw runtime image under `runtimes\openclaw`.
- Hermes runtime image under `runtimes\hermes`.
- Private incidents MCP works from both OpenClaw and Hermes.
- Agent 365 setup script supports both runtimes:
  - `uv run python -m scripts.setup_agent365 --runtime openclaw`
  - `uv run python -m scripts.setup_agent365 --runtime hermes`

Current limitation:

- Before side-by-side deployment support, one bridge deployment is switched between runtimes with `AGENT_RUNTIME=openclaw` or `AGENT_RUNTIME=hermes`.
- Separate OpenClaw and Hermes Agent 365 packages can be generated, but they currently point to whichever single bridge endpoint is deployed.

## Product direction

Agent 365 is the primary install and user-facing Microsoft 365 path.

Removed from the active path:

- Teams sideload package generation.
- Separate Teams manifest preview packages.
- Runtime-specific Teams sideload quickstarts.

The bridge still owns Teams-compatible `/api/messages` behavior, but operators should package and publish through Agent 365 rather than sideloading Teams apps.

## Runtime switching before side-by-side deployments

Until A5, there is one bridge app. Switch it by changing bridge app environment variables and image/runtime settings.

OpenClaw mode requires:

- `AGENT_RUNTIME=openclaw`
- OpenClaw runtime image and disk image name.
- OpenClaw data volume, for example `openclaw-kind-data`.
- `OPENCLAW_GATEWAY_TOKEN`
- `OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM`
- OpenClaw bridge device approval in the OpenClaw Gateway UI.

Hermes mode requires:

- `AGENT_RUNTIME=hermes`
- Hermes runtime image and disk image name.
- Hermes data volume, for example `hermes-a35-mcp-data`.
- `API_SERVER_KEY`
- Foundry model configuration:
  - `FOUNDRY_OPENAI_BASE_URL`
  - `OPENCLAW_MODEL_ID` for the shared model deployment name
  - `HERMES_MODEL_PROVIDER=azure-foundry`
  - `HERMES_MODEL`
  - `HERMES_INFERENCE_MODEL`

Both modes use:

- Private incidents MCP URL and static key.
- `app=autopilots-on-azure` and `kind=<runtime>` sandbox labels.
- The common bridge `/api/messages` endpoint.

## Agent 365 packaging specification

Agent 365 artifacts are runtime-scoped:

```text
.local\openclaw\agent365\
.local\hermes\agent365\
```

Each runtime workspace contains:

```text
a365.config.json
a365.generated.config.json
<runtime>-agent365-identifiers.json
manifest\
```

Runtime defaults:

| Runtime | Agent name | Package branding |
| --- | --- | --- |
| OpenClaw | OpenClaw Autopilot | OpenClaw Autopilot on Azure |
| Hermes | Hermes Autopilot | Hermes Autopilot on Azure |

The generated Agent 365 config must include:

- `autopilotName`
- `agentRuntime`
- `agentName`
- `tenantId`
- `messagingEndpoint`
- AI teammate mode when available.

If AI teammate / Frontier is unavailable, blueprint-only mode is acceptable, but it should be described as blueprint-backed Agent 365, not as a user-like digital worker identity.

## Validation baseline

OpenClaw bridge smoke:

```powershell
$bridge = terraform -chdir=terraform\apps output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"openclaw-smoke","message":"List services from private incidents MCP"}'
```

Expected services:

```text
core_banking
card_payments
digital_onboarding
fraud_detection
wealth_portfolio
```

Hermes bridge smoke:

```powershell
$bridge = terraform -chdir=terraform\apps output -raw bridge_url
Invoke-RestMethod `
  -Method Post `
  -Uri "$bridge/invoke" `
  -ContentType application/json `
  -Body '{"conversationId":"hermes-smoke","message":"Reply with exactly: Hermes bridge OK"}'
```

Hermes private MCP smoke should return the same service list as OpenClaw.

Local validation:

```powershell
uv run python -m unittest tests.test_agent365_setup tests.test_hermes_runtime tests.test_runtime_adapters tests.test_teams_bridge
uv run python -m compileall bridge scripts tests runtimes\openclaw\openclaw_gateway runtimes\hermes -q
Set-Location .\private-incidents-mcp
uv run --with pytest --with pytest-asyncio --with-editable . pytest -q
Set-Location ..
terraform -chdir=terraform\apps validate
```

## Next milestones

### A4.5 - Test Agent 365 packages for both runtimes

Goal: prove the Agent 365 path works for OpenClaw and Hermes with the runtime-aware setup script.

Tasks:

- Run `scripts.setup_agent365 --runtime openclaw --run-setup`.
- Publish/capture OpenClaw Agent 365 artifacts.
- Switch the bridge to OpenClaw and validate Agent 365 messages.
- Run `scripts.setup_agent365 --runtime hermes --run-setup`.
- Publish/capture Hermes Agent 365 artifacts.
- Switch the bridge to Hermes and validate Agent 365 messages.
- Record any tenant/Frontier/AI teammate limitations.

Exit criteria:

- OpenClaw and Hermes each have runtime-specific Agent 365 config and metadata artifacts.
- At least one Agent 365 package reaches the bridge `/api/messages` endpoint successfully.
- Any blocker is explicit and reproducible.

### A5 - Side-by-side app deployments

Goal: run OpenClaw and Hermes at the same time instead of switching one bridge.

Tasks:

- Generate separate runtime app tfvars:
  - `.local\openclaw\apps`
  - `.local\hermes\apps`
- Create separate bridge app names.
- Create separate bridge identities/secrets.
- Use separate runtime images, disk image names, and data volumes.
- Use separate Agent 365 package/config directories.
- Ensure logs, diagnostics, and outputs include runtime kind.
- Decide whether Terraform `apps` remains one-run-per-runtime or moves to `for_each`.

Exit criteria:

- OpenClaw and Hermes bridge endpoints are both live.
- OpenClaw and Hermes can be packaged/published through Agent 365 independently.
- Runtime state and sandbox volumes do not collide.

### A6 - Operator polish

Goal: make the repository easy to run as a demo.

Tasks:

- Update README around Agent 365-first operation.
- Add explicit runtime switch commands.
- Add side-by-side deployment guide after A5.
- Add troubleshooting for:
  - OpenClaw device approval.
  - Hermes API server health.
  - Private MCP host validation.
  - Agent 365 setup/publish/capture.
  - Azure `azapi` token refresh failures.

Exit criteria:

- A new operator can deploy, switch, package, and validate either runtime without reading historical planning docs.

## Deferred

Do not implement until the Agent 365 and side-by-side paths are stable:

- Work IQ integration.
- Deeper user identity/profile isolation.
- Hermes native Teams mode.
- Hermes dashboard exposure.
- Full multi-user memory/profile policy.
