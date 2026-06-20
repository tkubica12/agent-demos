# Hermes Foundry Gateway POC

This project is the first executable slice of `HERMES_PLAN.md`.

It packages a Foundry Hosted Agent-compatible HTTP service that exposes:

- `GET /health`
- `GET /readiness`
- `POST /invocations`
- `POST /responses`

Both protocols delegate each turn to the real Hermes runtime:

```text
python -m hermes_cli.main chat -q "<prompt>"
```

The adapter does not emulate Hermes responses. If Hermes is not installed or not
configured with a model/provider, runtime calls fail explicitly.

## Local test

```powershell
cd C:\git\agent-demos\hermes-foundry
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

## Local run

```powershell
cd C:\git\agent-demos\hermes-foundry
.\.venv\Scripts\python main.py
```

Then call:

```powershell
curl -sS -H "Content-Type: application/json" `
  -X POST http://localhost:8088/invocations `
  -d "{\"task\":\"Say hello from Hermes\"}"
```

## Foundry deployment path

The required CLI is:

```powershell
azd ext install azure.ai.agents
azd ext install microsoft.foundry
```

Initialize/deploy this folder into an existing Foundry project:

```powershell
azd init --minimal --no-prompt `
  -e hermes-foundry `
  --subscription 673af34d-6b28-41dc-bc7b-f507418045e6 `
  --location swedencentral

azd ai project set `
  https://tomaskubica-foundry-resource.services.ai.azure.com/api/projects/tomaskubica-foundry-project `
  --no-prompt

azd ai agent init `
  --src . `
  --agent-name hermes-foundry-gateway `
  --protocol responses `
  --protocol invocations `
  --project-id "/subscriptions/673af34d-6b28-41dc-bc7b-f507418045e6/resourceGroups/ai-services/providers/Microsoft.CognitiveServices/accounts/tomaskubica-foundry-resource/projects/tomaskubica-foundry-project" `
  --model-deployment gpt-4.1 `
  --deploy-mode code `
  --runtime python_3_13 `
  --entry-point main.py `
  --no-prompt `
  --force

azd deploy
```

The Hosted Agent managed identity must have data-plane access to the Foundry
account when local auth is disabled:

```powershell
az role assignment create `
  --assignee-object-id <agent-instance-principal-id> `
  --assignee-principal-type ServicePrincipal `
  --role "Cognitive Services OpenAI User" `
  --scope "/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<foundry-account>"
```

## Required environment variables

| Variable | Default | Purpose |
|---|---|---|
| `HERMES_HOME` | `/files/hermes` in Foundry, `.hermes` locally | Durable Hermes state |
| `HERMES_EXECUTABLE` | `hermes` | Hermes CLI executable |
| `HERMES_TURN_TIMEOUT_SECONDS` | `900` | Turn timeout |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | unset | Passed through for Foundry-hosted deployments |
| `AZURE_FOUNDRY_BASE_URL` | unset | OpenAI-compatible Foundry endpoint, for example `https://<account>.cognitiveservices.azure.com/openai/v1` |
| `HERMES_AZURE_FOUNDRY_TOKEN_AUTH` | `false` | Set to `true` when local key auth is disabled and Entra tokens should be injected into Hermes as the bearer credential |

## Current validation status

- Adapter/unit tests pass locally.
- Docker image builds as `hermes-foundry-gateway:local`.
- Foundry Hosted Agent `hermes-foundry-gateway` is deployed and active in the `tomaskubica-foundry-project` project.
- Hosted `responses` protocol returned `hosted responses ok`.
- Hosted `invocations` protocol returned `hosted invocation ok`.
- Agent 365 AI teammate publication is the next gate after Hosted Agent deployment.
