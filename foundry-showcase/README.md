# Foundry Showcase

This project demonstrates Microsoft Foundry agent capabilities through one inspectable support-operations scenario. [PLAN.md](PLAN.md) defines the complete five-phase target.

## Deployed status

| Phase | Status | Live evidence |
|---|---|---|
| 1. Hosted baseline | Complete | Hosted Agent, Responses, Invocations, Foundry Memory, telemetry |
| 2. Governed tools and skills | Complete | Toolbox v3, three published skills, Entra-protected case MCP, Foundry IQ, private Table Storage, approval round trip |
| 3. Workflow and A2A | Complete | MAF checkpointed workflow, LangGraph Hosted Agent v2, authenticated A2A Toolbox delegation, correlated traces |
| 4. Routines and surfaces | External approval pending | Routines, secretless Entra/OBO AG-UI, Activity bridge, Teams channel, and Agent 365 publication are live; tenant approval blocks registry and Teams validation |
| 5. Quality and safety | Complete with preview limitations | Immediate and scheduled evaluations, daily schedule, continuous rule, score alert, optimizer review, red teaming, dedicated observability, and bounded Qwen SFT are implemented |
| Portal experiences | Complete with explicit preview boundaries | Stored Completions, persistent Memory, PII and Task Adherence guardrails, rich Foundry IQ content, workflows, monitoring, and fine-tuning are live; trace curation, continuous session retrieval, and secretless Work IQ are blocked upstream |

Current immutable assets:

- Hosted Agent `foundry-showcase-main`, active and only retained version 28;
- Hosted Agent `foundry-showcase-policy-helper`, active version 2;
- Toolbox `foundry-showcase-support`, default version 3;
- Toolbox `foundry-showcase-policy-tools`, default version 1;
- skills `support-style`, `escalation-policy`, and `profile-update-policy`, version 1;
- case MCP `ca-foundry-showcase-case-mcp` in North Europe;
- private case data in Azure Table Storage in Sweden Central;
- AG-UI BFF `ca-foundry-showcase-agui`, revision `ca-foundry-showcase-agui--0000007`;
- weekday `daily-support-quality-review` Routine and disabled completed one-time `case-follow-up-reminder`;
- Bot Service and Teams channel `foundry-showcase-main-bot-si4ons`;
- Agent 365 publication `1.0.2`, submitted as title `T_16ff5b9f-aeaa-bcce-29b1-e7d5f1dd67d9`;
- 12 Stored Completions and runtime-aligned persistent memories for Tomas;
- Guardrail `foundry-showcase-sensitive-data`;
- Task Adherence resource `cs-task-foundry-showcase-vz5kj8`;
- Azure AI Search `srch-foundry-iq-vz5kj8` and knowledge base `foundry-showcase-knowledge`;
- Application Insights `appi-foundry-showcase-vz5kj8`, workspace `log-foundry-showcase-vz5kj8`, and Task Adherence score alert;
- daily evaluation schedule `foundry-showcase-daily-evaluation` and continuous rule `foundry-showcase-continuous-live`;
- Qwen SFT job `ftjob-d6e97df9e4cd4766ba81e754c848b635` and retained model `qwen3-32b.ft-d6e97df9e4cd4766ba81e754c848b635-foundry-showcase`;
- two Content Understanding-processed PDF knowledge assets;
- portal red-team run `7627b190-4823-44f6-b265-2cb33da7836f`, containing six real attacks and two ungrounded-attribute findings.

## Architecture today

```text
Responses / structured Invocations     AG-UI browser
                |                           |
                |                    Entra token / thin BFF
                |                           |
                +-------------+-------------+
                              |
                    MAF Hosted Agent v28
                 /             |                 \
        Foundry Memory  Support Toolbox v3   Policy Toolbox v1
                       /             \              |
               Entra case MCP      Foundry IQ    RemoteA2A connection
                     |                 |              |
              private Table       Search + web   LangGraph helper v2

Foundry Routines ---------> structured Invocations
Agent 365 / Teams --------> Activity bridge (tenant approval pending)
```

Toolbox reads and proposal creation do not require approval. `case-write___apply_case_update` always requires the Responses approval exchange. Live validation proved that the write does not run before approval, resumes from `previous_response_id`, updates Table Storage once, and can be restored through a second approved write.

The `resolve_support_case` MAF workflow retrieves the case, delegates an exact sanitized policy payload through authenticated A2A, rejects contradictions before proposal creation, branches on deterministic risk, checkpoints confirmation state, and resumes the governed write in the same Hosted Agent session. The `process_invoice` workflow demonstrates deterministic sequential prepare, validate, and route stages with `auto_post`, `finance_review`, and `rejected` outcomes. The primary and helper spans share one W3C operation ID.

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
  --auto-approve
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

Configure the Activity bridge, Bot Service Teams channel, Agent 365 permissions, and publication request:

```powershell
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\configure_agent365.py `
  --project-endpoint $projectEndpoint `
  --agent-version 28 `
  --foundry-account-name "<foundry-account-name>" `
  --foundry-project-name "<foundry-project-name>" `
  --publish-version 1.0.2 `
  --auto-approve
```

Provision the portal experience resources, including dedicated observability, and populate the inspectable examples. The commands resolve every required Terraform input from the existing Foundry project:

```powershell
$foundryResourceGroup = "ai-services"
$foundryAccountName = "tomaskubica-foundry-resource"
$foundryProjectName = "tomaskubica-foundry-project"
$portalUserUpn = "tomas@tomasonline.net"
$account = az account show | ConvertFrom-Json
$foundryAccount = az resource show `
  --resource-group $foundryResourceGroup `
  --name $foundryAccountName `
  --resource-type Microsoft.CognitiveServices/accounts | ConvertFrom-Json
$portalUserObjectId = az ad user show --id $portalUserUpn --query id --output tsv
$projectPrincipalId = az rest --method get `
  --url "https://management.azure.com$($foundryAccount.id)/projects/$foundryProjectName?api-version=2025-04-01-preview" `
  --query identity.principalId --output tsv

$env:TF_VAR_subscription_id = $account.id
$env:TF_VAR_tenant_id = $account.tenantId
$env:TF_VAR_foundry_account_id = $foundryAccount.id
$env:TF_VAR_foundry_project_principal_id = $projectPrincipalId
$env:TF_VAR_portal_user_object_id = $portalUserObjectId

Push-Location foundry-showcase\terraform\experiences
terraform init
terraform apply
Pop-Location

$env:FOUNDRY_PROJECT_ENDPOINT = $projectEndpoint
$subscriptionId = az account show --query id --output tsv
$searchEndpoint = terraform -chdir=foundry-showcase\terraform\experiences output -raw search_endpoint
$taskAdherenceEndpoint = terraform -chdir=foundry-showcase\terraform\experiences output -raw task_adherence_endpoint
uv run --project foundry-showcase\experiences python foundry-showcase\experiences\seed_stored_completions.py
foreach ($scope in @(
  "playground-user",
  $portalUserObjectId,
  "$($account.tenantId)_$portalUserObjectId"
)) {
  uv run --project foundry-showcase\experiences python foundry-showcase\experiences\seed_foundry_memory.py `
    --scope $scope `
    --replace
}
uv run --project foundry-showcase\experiences python foundry-showcase\experiences\validate_guardrail.py `
  --task-adherence-endpoint $taskAdherenceEndpoint
uv run --project foundry-showcase\experiences python foundry-showcase\experiences\generate_knowledge_assets.py
uv run --project foundry-showcase\experiences python foundry-showcase\experiences\configure_foundry_iq.py `
  --search-endpoint $searchEndpoint `
  --subscription-id $subscriptionId

$caseMcpEndpoint = terraform -chdir=foundry-showcase\terraform\apps output -raw case_mcp_endpoint
uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\publish_foundry_assets.py `
  --project-endpoint $projectEndpoint `
  --mcp-endpoint $caseMcpEndpoint `
  --new-toolbox-version `
  --promote

uv run --project foundry-showcase\experiences python foundry-showcase\experiences\configure_monitoring.py `
  --project-endpoint $projectEndpoint `
  --agent-version 28 `
  --skip-continuous-wait

uv run --project foundry-showcase\experiences python foundry-showcase\experiences\run_qwen_finetuning.py `
  --project-endpoint $projectEndpoint
```

Build, deploy, and validate the secretless AG-UI BFF after the project AppInsights connection exists. The deployer resolves that connection by default:

```powershell
uv run --project foundry-showcase\bff python foundry-showcase\scripts\deploy_agui.py `
  --agent-invocations-url "<main-agent-invocations-url>" `
  --foundry-account-name "<foundry-account-name>" `
  --foundry-project-name "<foundry-project-name>" `
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
  --version 28 `
  --protocol responses `
  --new-session `
  -C foundry-showcase

azd ai agent invoke foundry-showcase-main `
  --version 28 `
  --protocol invocations `
  --input-file foundry-showcase\main-agent\smoke-invocation.json `
  --new-session `
  -C foundry-showcase

azd ai agent eval run `
  --config eval.yaml `
  --name foundry-showcase-v28-final `
  --no-prompt `
  -C foundry-showcase\main-agent
uv run --project foundry-showcase\experiences python foundry-showcase\experiences\run_local_red_team.py `
  --project-endpoint $projectEndpoint `
  --agent-version 28 `
  --out .artifacts\foundry-showcase-red-team
```

The current regression is 20/20 main-agent tests, 4/4 helper tests, 3/3 AG-UI tests, and 10/10 MCP tests. Final evaluation run `evalrun_ee8329679c7340d2a95047229a347878` evaluated 30 cases against version 26: the domain rubric passed 23, failed 5, and errored 2; task adherence passed 17, failed 10, and errored 3; intent resolution passed 12, failed 15, and errored 3. The aggregate result was 10 passed, 18 failed, and 2 errored. The bounded optimizer run `opt_8e32d2e5f7344b2ab65c2689acd5e9ea` scored the baseline at `0.5259027` and its candidate at `0.512111`; the baseline correctly remained the promoted behavior now deployed with the expanded version-28 capabilities.

The dedicated Application Insights resource recorded 95 agent, model, and tool trace events across three operations after version 28 deployment. The score alert queries portal evaluation telemetry for Task Adherence below `0.5`. Cost remains unset because the Azure Retail Prices API does not publish the contracted regional `gpt-5.4-mini` rate.

The bounded Qwen3-32B Global Standard SFT job `ftjob-d6e97df9e4cd4766ba81e754c848b635` completed one epoch over 20 training and five validation examples. It produced model `qwen3-32b.ft-d6e97df9e4cd4766ba81e754c848b635-foundry-showcase` with training loss `3.2383`, evaluation loss `3.0637`, 3,105 trained tokens, and 2,784 billed tokens. No hosting deployment was created.

## Known constraints

- The subscription disables public Storage endpoints, so the Container Apps environment uses VNet integration, private DNS, and a Table private endpoint.
- Container Apps capacity was unavailable in Sweden Central during deployment; compute is in North Europe while persistent resources remain in Sweden Central.
- Foundry Toolbox uses the Hosted Agent instance identity, not the Agent Identity Blueprint.
- The A2A connection uses Entra token passthrough because the current regional backend rejects the documentation's `AgenticIdentity` discriminator. The caller's instance and blueprint principals have only `Foundry Agent Consumer` on the target project.
- The current upstream client drops approval responses during service-managed continuation. The main agent contains a tested narrow override until the package fixes that behavior.
- The AG-UI BFF uses its managed identity as a federated client assertion for a secretless OBO exchange, so Foundry and the internal `UserEntraToken` A2A connection receive the signed-in user context.
- The version-28 Invocations gateway returns `502 Failed to forward request` for `stream=true` before the request reaches the agent. The thin BFF therefore uses a real non-streaming Invocations call and emits the returned text through the AG-UI event stream.
- Non-fatal hosted logs can report duplicate telemetry instrumentation and unavailable optional `agents` instrumentation.
- Agent Optimizer supports Responses targets but rejects agents that also expose Invocations. Version 25 temporarily isolated Responses for the optimizer run; it was deleted after the unchanged baseline was promoted as multi-protocol version 26.
- Evaluation cases 20 and 28 fail before producing a response because the Hosted Agent service raises `'ContentFiltered' is not a valid ContentFilterCodes`. The final dataset and run retain these as explicit operational failures rather than replacing them with easier prompts or synthetic responses.
- Cloud red-team run `evalrun_d78033e5c5a746c1a238ad23d7ad79dc` completed against version 26 with zero attack items. Local SDK run `7627b190-4823-44f6-b265-2cb33da7836f` is portal-visible with six genuine version-27 conversations: protected material and code vulnerability passed, while both ungrounded-attribute attacks succeeded for an overall ASR of 33.33%.
- Harm-category inputs still expose the upstream Hosted Agent error `'ContentFiltered' is not a valid ContentFilterCodes`; the local runner rejects SDK-generated error placeholders instead of counting them as successful safety responses.
- The project now uses showcase-owned Application Insights and Log Analytics resources, and version 28 emits content-rich user, assistant, and tool events. The trace curator still returns `DataGenerationJobNoTracesFound` for agent name, version, Hosted Agent GUID, and telemetry ID. All eight failed jobs still reject deletion with `unexpectedEntityState`.
- Immediate quality evaluation and the static scheduled validation pass, and the daily 07:00 UTC schedule is provisioned. The continuous rule triggers correctly, but the evaluation worker cannot retrieve its triggering response and fails with `403 session_not_accessible`; the service job log proves the failure occurs before any evaluator runs.
- `Purview` is attached to the model policy. Deterministic synthetic email, phone, and SSN examples use the custom blocklist because the advertised granular PII filters are rejected by the regional RAI policy API. Task Adherence is validated against the dedicated East US Content Safety preview because Sweden Central returns feature-unavailable and `TaskAdherence` is rejected inside a model RAI policy.
- Work IQ works for Tomas through delegated Microsoft 365 credentials, but the supported Foundry MCP connection requires tenant-admin consent plus a stored OAuth client secret. Application-only authentication is unsupported, so Work IQ is intentionally not added while the repository requires secretless managed identity and OBO.
- Agent 365 publication is submitted and the Activity/Teams infrastructure is deployed. Tenant-admin approval at `https://admin.cloud.microsoft/?#/agents/all/requested` is the only blocker to registry, Agent User, and Teams interaction validation.

## Design constraints

- one primary agent and one bounded helper;
- native Foundry capabilities before custom substitutes;
- Terraform with `azapi`;
- Python managed with `uv`;
- scripts rather than portal-only operations;
- no stored credentials;
- no fake platform features or simulated deployment paths.
