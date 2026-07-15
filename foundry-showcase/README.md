# Foundry Showcase

One cohesive Microsoft Foundry reference demo built from the proven Progressive Agents components.

The first implementation milestone is the main Microsoft Agent Framework Hosted Agent with Responses and Invocations protocols, Foundry Memory, reusable skills, structured observability, and evaluation-ready configuration.

See [PLAN.md](PLAN.md) for the complete target architecture.

## Main agent

```powershell
cd foundry-showcase\main-agent
uv sync

$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
$env:MEMORY_STORE_NAME = "foundry-showcase-main"

uv run python scripts\setup_foundry_memory.py --name foundry-showcase-main
uv run python main.py
```

Local validation:

```powershell
uv run python smoke_skills.py
uv run python smoke_memory.py
uv run python smoke_test.py
uv run python smoke_invocations.py
```

Deploy from the showcase root:

```powershell
azd deploy -C foundry-showcase
azd ai agent invoke foundry-showcase-main "Reply exactly: deployed showcase ok" --protocol responses -C foundry-showcase
```

## Current deployment

- Hosted Agent: `foundry-showcase-main`
- Active version: `3`
- Protocols: Responses `2.0.0`, Invocations `1.0.0`
- Memory store: `foundry-showcase-main`
- Cloud evaluation baseline: 13 of 15 generated cases passed
