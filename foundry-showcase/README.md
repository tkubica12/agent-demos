# Foundry Showcase

This project demonstrates Microsoft Foundry agent capabilities through one inspectable support-operations scenario. [PLAN.md](PLAN.md) defines the complete five-phase target.

## Deployed status

| Phase | Status | Live evidence |
|---|---|---|
| 1. Hosted baseline | Complete | Hosted Agent, Responses, Invocations, Foundry Memory, telemetry |
| 2. Governed tools and skills | Complete | Toolbox v2, three published skills, Entra-protected case MCP, private Table Storage, approval round trip |
| 3. Workflow and A2A | Complete | MAF checkpointed workflow, LangGraph Hosted Agent v2, authenticated A2A Toolbox delegation, correlated traces |
| 4. Routines and surfaces | Not started | Routines, AG-UI, Agent 365, and Teams remain pending |
| 5. Quality and safety | Partial | Baseline evaluation is live; optimizer review, red teaming, canary, and final promotion remain pending |

Current immutable assets:

- Hosted Agent `foundry-showcase-main`, active version 16;
- Hosted Agent `foundry-showcase-policy-helper`, active version 2;
- Toolbox `foundry-showcase-support`, default version 2;
- Toolbox `foundry-showcase-policy-tools`, default version 1;
- skills `support-style`, `escalation-policy`, and `profile-update-policy`, version 1;
- case MCP `ca-foundry-showcase-case-mcp` in North Europe;
- private case data in Azure Table Storage in Sweden Central.

## Architecture today

```text
Responses / structured Invocations
                |
             MAF Hosted Agent v16
        /            |                 \
Foundry Memory  Support Toolbox v2   Policy Toolbox v1
                      |                 |
              Entra case MCP       RemoteA2A connection
                      |                 |
             private Table       LangGraph helper v2
```

Toolbox reads and proposal creation do not require approval. `case-write___apply_case_update` always requires the Responses approval exchange. Live validation proved that the write does not run before approval, resumes from `previous_response_id`, updates Table Storage once, and can be restored through a second approved write.

The `resolve_support_case` MAF workflow retrieves the case, delegates an exact sanitized policy payload through authenticated A2A, rejects contradictions before proposal creation, branches on deterministic risk, checkpoints confirmation state, and resumes the governed write in the same Hosted Agent session. The primary and helper spans share one W3C operation ID.

## Prerequisites

- Python 3.11 or newer;
- `uv`;
- Azure CLI;
- `azd`;
- Terraform;
- Microsoft Foundry CLI extension;
- authenticated Azure CLI and `azd` sessions.

## Deploy

Deploy or update the Hosted Agent:

```powershell
azd env select foundry-showcase -C foundry-showcase
azd deploy foundry-showcase-main -C foundry-showcase --no-prompt
azd deploy foundry-showcase-policy-helper -C foundry-showcase --no-prompt
```

Provision the private MCP platform, build the image, deploy the app, create the agentic-identity connection, and publish immutable Foundry assets:

```powershell
$projectEndpoint = "<foundry-project-endpoint>"
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\deploy_phase2.py `
  --project-endpoint $projectEndpoint `
  --auto-approve `
  --new-toolbox-version
```

The publication script does not promote a new Toolbox version automatically. Validate the version first, then promote it explicitly:

```powershell
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\publish_foundry_assets.py `
  --project-endpoint $projectEndpoint `
  --toolbox-version 2 `
  --promote
```

Enable the helper A2A endpoint, converge its project connection and Toolbox, and grant the primary agent identities least-privilege invocation access:

```powershell
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\configure_a2a.py `
  --project-endpoint $projectEndpoint `
  --subscription-id "<subscription-id>" `
  --resource-group "<foundry-resource-group>" `
  --account-name "<foundry-account-name>" `
  --project-name "<foundry-project-name>"
```

## Validate

```powershell
Push-Location foundry-showcase\case-mcp
uv run pytest -q
Pop-Location

Push-Location foundry-showcase\main-agent
uv run --with pytest --with pytest-asyncio pytest -q
$env:FOUNDRY_PROJECT_ENDPOINT = "<foundry-project-endpoint>"
$env:TOOLBOX_VERSION = "2"
uv run python smoke_toolbox.py
uv run python smoke_a2a_delegation.py --url "<main-agent-invocations-url>"
uv run python smoke_workflow.py --url "<main-agent-invocations-url>"
Pop-Location

Push-Location foundry-showcase\policy-helper
$env:PYTHONPATH = "."
uv run --with pytest pytest -q
uv run python smoke_a2a.py --a2a-url "<policy-helper-a2a-url>"
Pop-Location

azd ai agent invoke foundry-showcase-main `
  "Use the case tools to get CASE-1001." `
  --version 16 `
  --protocol responses `
  --new-session `
  -C foundry-showcase

azd ai agent invoke foundry-showcase-main `
  --version 16 `
  --protocol invocations `
  --input-file foundry-showcase\main-agent\smoke-invocation.json `
  --new-session `
  -C foundry-showcase

azd ai agent eval run `
  --config eval.yaml `
  --name foundry-showcase-v9-phase2 `
  --no-prompt `
  -C foundry-showcase\main-agent
```

The current Phase 3 regression is 9/9 main-agent tests, 4/4 helper tests, the existing 12/12 MCP regression, and 15/15 rows in Foundry evaluation run `evalrun_00182e08a0f84a2caf02aeabd3375edb`.

## Known constraints

- The subscription disables public Storage endpoints, so the Container Apps environment uses VNet integration, private DNS, and a Table private endpoint.
- Container Apps capacity was unavailable in Sweden Central during deployment; compute is in North Europe while persistent resources remain in Sweden Central.
- Foundry Toolbox uses the Hosted Agent instance identity, not the Agent Identity Blueprint.
- The A2A connection uses Entra token passthrough because the current regional backend rejects the documentation's `AgenticIdentity` discriminator. The caller's instance and blueprint principals have only `Foundry Agent Consumer` on the target project.
- The current upstream client drops approval responses during service-managed continuation. The main agent contains a tested narrow override until the package fixes that behavior.
- Non-fatal hosted logs can report duplicate telemetry instrumentation and unavailable optional `agents` instrumentation.
- Phases 4 and 5 remain; the showcase does not yet meet the completion criteria in [PLAN.md](PLAN.md).

## Design constraints

- one primary agent and one bounded helper;
- native Foundry capabilities before custom substitutes;
- Terraform with `azapi`;
- Python managed with `uv`;
- scripts rather than portal-only operations;
- no stored credentials;
- no fake platform features or simulated deployment paths.
