# Step 02: Foundry Invocations baseline

Smallest useful Microsoft Agent Framework agent hosted through the Foundry Responses and Invocations protocols.

```text
Client -> /responses -> ResponsesHostServer -> Agent Framework Agent -> Foundry model
Client -> /invocations -> custom handler -> Agent Framework Agent -> Foundry model
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

The Responses host listens on `http://localhost:8088`.

## Smoke test

In another terminal, test Responses:

```powershell
uv run python smoke_test.py
```

Test Invocations:

```powershell
uv run python smoke_invocations.py
```

Equivalent direct requests:

```powershell
(Invoke-WebRequest -Uri http://localhost:8088/responses -Method POST -ContentType "application/json" -Body '{"input":"Hello from the baseline"}').Content
(Invoke-WebRequest -Uri http://localhost:8088/invocations -Method POST -ContentType "application/json" -Body '{"message":"Hello from invocations","stream":false}').Content
```

## Deploy

```powershell
azd deploy -C progressive-agents\02-foundry-invocations-baseline
```

The repository keeps `pyproject.toml` and `uv.lock` for local `uv` workflows. Hosted Agent code deploy uses the Microsoft sample-style `requirements.txt` path; `.agentignore` excludes the local `uv` files from the deployment ZIP.

## Deployed smoke test

```powershell
azd ai agent show step-02-foundry-invocations-baseline -C progressive-agents\02-foundry-invocations-baseline
azd ai agent invoke step-02-foundry-invocations-baseline --protocol responses "Say exactly: deployed responses ok" -C progressive-agents\02-foundry-invocations-baseline
azd ai agent invoke step-02-foundry-invocations-baseline --protocol invocations '{"message":"Say exactly: deployed invocations ok","stream":false}' -C progressive-agents\02-foundry-invocations-baseline
```

## Known gaps

- No A2A, AG-UI, memory, custom tools, or telemetry beyond platform defaults.
- Requires the existing Foundry project to support Hosted Agents in its region and the configured model deployment to be accessible.
