# Step 03: A2A enabled Responses agent

Microsoft Agent Framework agent hosted through Foundry Responses and Invocations protocols, with Foundry-managed incoming A2A enabled after deployment.

```text
Client -> /responses -> ResponsesHostServer -> Agent Framework Agent -> Foundry model
Client -> /invocations -> custom handler -> Agent Framework Agent -> Foundry model
A2A caller -> /endpoint/protocols/a2a -> Foundry bridge -> Responses agent
```

## Prerequisites

- `uv`
- Azure CLI authenticated with access to the target Microsoft Foundry project
- Azure Developer CLI with the AI agent extension:

```powershell
azd ext install azure.ai.agents
```

## Configuration

Set the existing Foundry project endpoint and, optionally, the model deployment.

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
```

`AZURE_AI_MODEL_DEPLOYMENT_NAME` defaults to `gpt-5.4-mini` when omitted.

## Local run

```powershell
uv sync
uv run python main.py
```

The local host listens on `http://localhost:8088`.

## Local smoke test

In another terminal, test Responses:

```powershell
uv run python smoke_test.py
```

Test Invocations:

```powershell
uv run python smoke_invocations.py
```

## Deploy

```powershell
azd deploy -C progressive-agents\03-a2a-enabled-responses-agent
```

The repository keeps `pyproject.toml` and `uv.lock` for local `uv` workflows. Hosted Agent code deploy uses the Microsoft sample-style `requirements.txt` path; `.agentignore` excludes the local `uv` files from the deployment ZIP.

## Enable A2A

After deployment, patch the live agent endpoint to publish an agent card and enable incoming A2A. Foundry does not read A2A endpoint configuration from `agent.yaml` and the portal cannot configure it yet, so this REST patch is required:

```powershell
.\scripts\Setup-A2A.ps1
```

The script sends one merge-patch with both required properties: `agent_card` and `agent_endpoint.protocols = ["responses", "a2a"]`. It then verifies that the live agent reports `a2a` and that the v1.0 agent card is reachable. If the portal still shows **Set up** for A2A after this succeeds, treat that as a portal preview display issue and verify with the agent object or smoke test instead.

Fetch the v1.0 agent card:

```powershell
.\scripts\Get-AgentCard.ps1
```

## Deployed smoke test

```powershell
azd ai agent show step-03-a2a-enabled-responses-agent -C progressive-agents\03-a2a-enabled-responses-agent
azd ai agent invoke step-03-a2a-enabled-responses-agent --protocol responses "Say exactly: deployed responses ok" -C progressive-agents\03-a2a-enabled-responses-agent
azd ai agent invoke step-03-a2a-enabled-responses-agent --protocol invocations '{"message":"Say exactly: deployed invocations ok","stream":false}' -C progressive-agents\03-a2a-enabled-responses-agent
uv run python smoke_a2a.py --a2a-url "https://<account>.services.ai.azure.com/api/projects/<project>/agents/step-03-a2a-enabled-responses-agent/endpoint/protocols/a2a"
```

## Known gaps

- A2A is text-only and non-streaming for now.
- No AG-UI, memory, custom tools, or telemetry beyond platform defaults.
- Requires the existing Foundry project to support Hosted Agents, incoming A2A preview, and the configured model deployment.
