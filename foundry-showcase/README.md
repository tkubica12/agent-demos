# Foundry Showcase

This project demonstrates Microsoft Foundry agent capabilities through one inspectable support-operations scenario. [PLAN.md](PLAN.md) defines the complete five-phase target.

## Deployed status

| Phase | Status | Live evidence |
|---|---|---|
| 1. Hosted baseline | Complete | Hosted Agent v9, Responses, Invocations, Foundry Memory, telemetry |
| 2. Governed tools and skills | Complete | Toolbox v2, three published skills, Entra-protected case MCP, private Table Storage, approval round trip |
| 3. Workflow and A2A | Not started | MAF workflow and LangGraph helper remain pending |
| 4. Routines and surfaces | Not started | Routines, AG-UI, Agent 365, and Teams remain pending |
| 5. Quality and safety | Partial | Baseline evaluation is live; optimizer review, red teaming, canary, and final promotion remain pending |

Current immutable assets:

- Hosted Agent `foundry-showcase-main`, active version 9;
- Toolbox `foundry-showcase-support`, default version 2;
- skills `support-style`, `escalation-policy`, and `profile-update-policy`, version 1;
- case MCP `ca-foundry-showcase-case-mcp` in North Europe;
- private case data in Azure Table Storage in Sweden Central.

## Architecture today

```text
Responses / structured Invocations
                |
       MAF Hosted Agent v9
          |            |
  Foundry Memory   Toolbox v2 + skills
                       |
             agentic-identity connection
                       |
             Entra-protected case MCP
                       |
          private endpoint + private DNS
                       |
              Azure Table Storage
```

Toolbox reads and proposal creation do not require approval. `case-write___apply_case_update` always requires the Responses approval exchange. Live validation proved that the write does not run before approval, resumes from `previous_response_id`, updates Table Storage once, and can be restored through a second approved write.

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

## Validate

```powershell
Push-Location foundry-showcase\case-mcp
uv run pytest -q
Pop-Location

Push-Location foundry-showcase\main-agent
uv run python -m unittest discover -s tests -v
$env:FOUNDRY_PROJECT_ENDPOINT = "<foundry-project-endpoint>"
$env:TOOLBOX_VERSION = "2"
uv run python smoke_toolbox.py
Pop-Location

azd ai agent invoke foundry-showcase-main `
  "Use the case tools to get CASE-1001." `
  --version 9 `
  --protocol responses `
  --new-session `
  -C foundry-showcase

azd ai agent invoke foundry-showcase-main `
  --version 9 `
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

The current Phase 2 regression is 12/12 local tests and 15/15 rows in Foundry evaluation run `evalrun_00182e08a0f84a2caf02aeabd3375edb`.

## Known constraints

- The subscription disables public Storage endpoints, so the Container Apps environment uses VNet integration, private DNS, and a Table private endpoint.
- Container Apps capacity was unavailable in Sweden Central during deployment; compute is in North Europe while persistent resources remain in Sweden Central.
- Foundry Toolbox uses the Hosted Agent instance identity, not the Agent Identity Blueprint.
- The current upstream client drops approval responses during service-managed continuation. The main agent contains a tested narrow override until the package fixes that behavior.
- Non-fatal hosted logs can report duplicate telemetry instrumentation and unavailable optional `agents` instrumentation.
- Phases 3 through 5 are not implemented; the showcase does not yet meet the completion criteria in [PLAN.md](PLAN.md).

## Design constraints

- one primary agent and one bounded helper;
- native Foundry capabilities before custom substitutes;
- Terraform with `azapi`;
- Python managed with `uv`;
- scripts rather than portal-only operations;
- no stored credentials;
- no fake platform features or simulated deployment paths.
