# Foundry Showcase

This project demonstrates Microsoft Foundry agent capabilities through one inspectable support-operations scenario. [PLAN.md](PLAN.md) defines the complete five-phase target.

## Deployed status

| Phase | Status | Live evidence |
|---|---|---|
| 1. Hosted baseline | Complete | Hosted Agent, Responses, Invocations, Foundry Memory, telemetry |
| 2. Governed tools and skills | Complete | Toolbox v2, three published skills, Entra-protected case MCP, private Table Storage, approval round trip |
| 3. Workflow and A2A | Complete | MAF checkpointed workflow, LangGraph Hosted Agent v2, authenticated A2A Toolbox delegation, correlated traces |
| 4. Routines and surfaces | External approval pending | Routines, secretless Entra/OBO AG-UI, Activity bridge, Teams channel, and Agent 365 publication are live; tenant approval blocks registry and Teams validation |
| 5. Quality and safety | Complete with preview limitations | 30-case evaluation, optimizer review, baseline promotion, trace metrics, and cloud red-team submission are complete; two Hosted Agent requests hit a content-filter enum defect and the red-team service returned zero attack items |

Current immutable assets:

- Hosted Agent `foundry-showcase-main`, active and only retained version 26;
- Hosted Agent `foundry-showcase-policy-helper`, active version 2;
- Toolbox `foundry-showcase-support`, default version 2;
- Toolbox `foundry-showcase-policy-tools`, default version 1;
- skills `support-style`, `escalation-policy`, and `profile-update-policy`, version 1;
- case MCP `ca-foundry-showcase-case-mcp` in North Europe;
- private case data in Azure Table Storage in Sweden Central;
- AG-UI BFF `ca-foundry-showcase-agui`, revision `ca-foundry-showcase-agui--0000005`;
- weekday `daily-support-quality-review` Routine and disabled completed one-time `case-follow-up-reminder`;
- Bot Service and Teams channel `foundry-showcase-main-bot-si4ons`;
- Agent 365 publication `1.0.1`, submitted as title `T_8d26edfe-1e4d-76f9-a67c-884535f3e1de`.

## Architecture today

```text
Responses / structured Invocations     AG-UI browser
                |                           |
                |                    Entra token / thin BFF
                |                           |
                +-------------+-------------+
                              |
                     MAF Hosted Agent v26
                 /             |                 \
        Foundry Memory  Support Toolbox v2   Policy Toolbox v1
                              |                 |
                       Entra case MCP       RemoteA2A connection
                              |                 |
                      private Table       LangGraph helper v2

Foundry Routines ---------> structured Invocations
Agent 365 / Teams --------> Activity bridge (tenant approval pending)
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

Converge and validate both Routines:

```powershell
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\configure_routines.py `
  --project-endpoint $projectEndpoint `
  --agent-name foundry-showcase-main `
  --wait-for-timer
```

Build, deploy, and validate the secretless AG-UI BFF:

```powershell
uv run --project foundry-showcase\bff python foundry-showcase\scripts\deploy_agui.py `
  --agent-invocations-url "<main-agent-invocations-url>" `
  --foundry-account-name "<foundry-account-name>" `
  --foundry-project-name "<foundry-project-name>" `
  --auto-approve
```

Configure the Activity bridge, Bot Service Teams channel, Agent 365 permissions, and publication request:

```powershell
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\configure_agent365.py `
  --project-endpoint $projectEndpoint `
  --agent-version 26 `
  --foundry-account-name "<foundry-account-name>" `
  --foundry-project-name "<foundry-project-name>" `
  --publish-version 1.0.1 `
  --auto-approve
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

Push-Location foundry-showcase\bff
uv run pytest -q
Pop-Location

azd ai agent invoke foundry-showcase-main `
  "Use the case tools to get CASE-1001." `
  --version 26 `
  --protocol responses `
  --new-session `
  -C foundry-showcase

azd ai agent invoke foundry-showcase-main `
  --version 26 `
  --protocol invocations `
  --input-file foundry-showcase\main-agent\smoke-invocation.json `
  --new-session `
  -C foundry-showcase

azd ai agent eval run `
  --config eval.yaml `
  --name foundry-showcase-v26-phase5-final `
  --no-prompt `
  -C foundry-showcase\main-agent
```

The current regression is 17/17 main-agent tests, 4/4 helper tests, 3/3 AG-UI tests, and 10/10 MCP tests. Final evaluation run `evalrun_ee8329679c7340d2a95047229a347878` evaluated 30 cases against version 26: the domain rubric passed 23, failed 5, and errored 2; task adherence passed 17, failed 10, and errored 3; intent resolution passed 12, failed 15, and errored 3. The aggregate result was 10 passed, 18 failed, and 2 errored. The bounded optimizer run `opt_8e32d2e5f7344b2ab65c2689acd5e9ea` scored the baseline at `0.5259027` and its candidate at `0.512111`; the baseline correctly remained the promoted version.

Application Insights metrics for the final validation window recorded 57 model calls, 1.75% model-call failures, p50/p95 model latency of 2.13/3.64 seconds, 47 agent requests, and 29 helper calls across 7 operations. Cost remains unset because the Azure Retail Prices API does not publish the contracted regional `gpt-5.4-mini` rate.

## Known constraints

- The subscription disables public Storage endpoints, so the Container Apps environment uses VNet integration, private DNS, and a Table private endpoint.
- Container Apps capacity was unavailable in Sweden Central during deployment; compute is in North Europe while persistent resources remain in Sweden Central.
- Foundry Toolbox uses the Hosted Agent instance identity, not the Agent Identity Blueprint.
- The A2A connection uses Entra token passthrough because the current regional backend rejects the documentation's `AgenticIdentity` discriminator. The caller's instance and blueprint principals have only `Foundry Agent Consumer` on the target project.
- The current upstream client drops approval responses during service-managed continuation. The main agent contains a tested narrow override until the package fixes that behavior.
- The AG-UI BFF uses its managed identity as a federated client assertion for a secretless OBO exchange, so Foundry and the internal `UserEntraToken` A2A connection receive the signed-in user context.
- Non-fatal hosted logs can report duplicate telemetry instrumentation and unavailable optional `agents` instrumentation.
- Agent Optimizer supports Responses targets but rejects agents that also expose Invocations. Version 25 temporarily isolated Responses for the optimizer run; it was deleted after the unchanged baseline was promoted as multi-protocol version 26.
- Evaluation cases 20 and 28 fail before producing a response because the Hosted Agent service raises `'ContentFiltered' is not a valid ContentFilterCodes`. The final dataset and run retain these as explicit operational failures rather than replacing them with easier prompts or synthetic responses.
- Cloud red-team run `evalrun_d78033e5c5a746c1a238ad23d7ad79dc` completed against version 26 with seven reviewed prohibited-action categories and real tool descriptions, but the preview service emitted zero attack items and no error. Deterministic tool, confirmation, identity, and policy tests remain the effective safety evidence until the service produces attack output.
- Agent 365 publication is submitted and the Activity/Teams infrastructure is deployed. Tenant-admin approval at `https://admin.cloud.microsoft/?#/agents/all/requested` is the only blocker to registry, Agent User, and Teams interaction validation.

## Design constraints

- one primary agent and one bounded helper;
- native Foundry capabilities before custom substitutes;
- Terraform with `azapi`;
- Python managed with `uv`;
- scripts rather than portal-only operations;
- no stored credentials;
- no fake platform features or simulated deployment paths.
