# Foundry Showcase

This project demonstrates Microsoft Foundry agent capabilities through one inspectable support-operations scenario. [PLAN.md](PLAN.md) defines the complete five-phase target.

[demo/demo-guide.html](demo/demo-guide.html) is the presenter's guide: how to walk the portal in front of a room, in six chapters and twenty-eight stops, with twenty-two captured screens. The same file is the slide deck — open it with `?view=slides`. Screens are recaptured with `demo/capture`; see [demo/capture/shots.toml](demo/capture/shots.toml) for the shot list.

## Deployed status

| Phase | Status | Live evidence |
|---|---|---|
| 1. Hosted baseline | Complete | Hosted Agent, Responses, Invocations, Foundry Memory, telemetry |
| 2. Governed tools and skills | Complete | Toolbox v3, three published skills, Entra-protected case MCP, Foundry IQ, private Table Storage, approval round trip |
| 3. Workflow and A2A | Complete | MAF checkpointed workflow, LangGraph Hosted Agent v2, authenticated A2A Toolbox delegation, correlated traces |
| 4. Routines and surfaces | External approval pending | Routines, secretless Entra/OBO AG-UI, Activity bridge, Teams channel, and Agent 365 publication are live; tenant approval blocks registry and Teams validation |
| 5. Quality and safety | Complete with preview limitations | Immediate and scheduled evaluations, daily schedule, continuous rule, score alert, optimizer review, red teaming, dedicated observability, and bounded Qwen SFT are implemented |
| Portal experiences | Complete with explicit preview boundaries | Stored Completions, persistent Memory, PII and Task Adherence guardrails, rich Foundry IQ content, workflows, monitoring, and fine-tuning are live; trace curation, continuous session retrieval, and secretless Work IQ are blocked upstream |
| Session scaling | Complete | Sandbox identity probe, three-mode load lab, measured shared, pooled, and isolated results, and verified session stop and resume |
| Human feedback loop | Complete | In-app thumbs up/down, business reviewer app outside the Foundry portal, flagged answers rebuilt into a pinned evaluation set |

Current immutable assets:

- Hosted Agent `foundry-showcase-main`, active and only retained version 33;
- Hosted Agent `foundry-showcase-policy-helper`, active version 2;
- Hosted Agent `foundry-showcase-optimize`, active version 2, the optimizer target;
- Prompt Agent `foundry-showcase-knowledge-expert`, active version 2;
- External Agent `foundry-showcase-external-triage`, version 1;
- Toolbox `foundry-showcase-support`, default version 3;
- Toolbox `foundry-showcase-policy-tools`, default version 1;
- Toolbox `foundry-showcase-knowledge-tools`, default version 1;
- skills `support-style`, `escalation-policy`, and `profile-update-policy`, version 1;
- case MCP `ca-foundry-showcase-case-mcp` in North Europe;
- private case data in Azure Table Storage in Sweden Central;
- AG-UI BFF `ca-foundry-showcase-agui`, revision `ca-foundry-showcase-agui--0000012`;
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
                    MAF Hosted Agent v31
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

## Human feedback loop

Foundry trace annotations are not a proprietary Foundry resource and are not locked to the Foundry portal. An annotation is an OpenTelemetry custom event named `gen_ai.evaluation.result`, written into the Application Insights resource the project is connected to, correlated to the run through `operation_Id` and `operation_ParentId`. The portal's Annotate button writes exactly this event, so any application can write it and everything appears in one place.

The showcase uses that in three ways:

- End users press thumbs up or down in the AG-UI client. `bff/feedback.py` emits the event onto the run's own trace with `source=end_user`.
- Business reviewers rate past answers in `annotation-review/`, a small Entra-authenticated app that reads traces with the reviewer's own delegated Log Analytics token and writes annotations with their own Azure Monitor token, so Azure RBAC decides who may read and who may annotate. No secret and no client credential.
- `annotation-review/scripts/build_flagged_eval_set.py` reads the failing annotations back, rebuilds the question and the rejected answer from the same traces, and writes a real evaluation dataset plus a pinned `eval.yaml` for `azd ai agent eval run`. Every case is a conversation a human rejected.

See [annotation-review/README.md](annotation-review/README.md) for the demo script and the verified write paths.

## Session scaling

Hosted agents scale per session, not per replica. There is no replica count, no warm pool, and no HTTP scaler: the platform creates a VM-isolated sandbox for each `agent_session_id` on demand and deprovisions it after 15 minutes of idle time. The caller therefore owns the scaling decision, and `foundry-showcase/scripts/session_scaling_lab.py` measures the three real strategies against the deployed agent.

`docs/hosted-agent-identity-and-scaling.html` explains the identifier model behind this section: which value routes a request to a sandbox, which value identifies the user, where per-user state is and is not partitioned for you, and how to choose between the three modes.

`main-agent/runtime_probe.py` adds a `runtime_probe` Invocations action and a `sandbox` field on every chat response. It reports a per-process instance identifier, requests served, peak observed in-flight concurrency, process uptime, and a `$HOME` marker that survives deprovision, so every claim below is measured rather than assumed.

| Mode | Session mapping | Compute |
|---|---|---|
| `shared` | all users on one `agent_session_id` | one sandbox |
| `isolated` | one `agent_session_id` per user | one sandbox per user |
| `pooled` | users packed into a bounded pool, sticky-fill | one sandbox per pool slot |

Measured on version 31 in Sweden Central at 0.5 vCPU and 1 GiB with `gpt-5.4-mini`.

Six concurrent users, all sandboxes cold:

| Mode | Sessions | Sandboxes | Peak in-flight | p50 s | p95 s | Wall s |
|---|---|---|---|---|---|---|
| shared | 1 | 1 | 6 | 46.76 | 48.25 | 49.19 |
| isolated | 6 | 6 | 1 | 13.60 | 14.37 | 16.50 |
| pooled, 3 users per session | 2 | 2 | 3 | 12.79 | 13.03 | 13.20 |

Twelve concurrent users, all sandboxes cold:

| Mode | Sessions | Sandboxes | Peak in-flight | p50 s | p95 s | Wall s | Throttle retries |
|---|---|---|---|---|---|---|---|
| shared | 1 | 1 | 12 | 14.41 | 14.69 | 15.44 | 2 |
| isolated | 12 | 12 | 1 | 13.66 | 15.43 | 16.71 | 2 |
| pooled, 4 users per session | 3 | 3 | 4 | 12.29 | 12.73 | 15.47 | 12 |

Twenty-four concurrent users on a single shared session completed with no failures at p50 11.43 s, p95 13.46 s, and a peak of 24 simultaneous requests inside one sandbox. The platform does not serialize concurrent requests to a session; the sandbox runs them on one asyncio loop, so a model-bound chat turn multiplexes efficiently and the binding limit becomes model throughput rather than sandbox CPU. The six-user cold run degrades because cold start and longer generations overlap in a single 0.5 vCPU sandbox, which is exactly the failure mode a pool avoids.

Cost follows sandbox count, not request count, because `cpu` and `memory` on an agent version describe one session. For the twelve-user burst the isolated mode bills roughly 183 sandbox-minutes against 46 for the pool and 15 for the shared session, so per-user isolation costs about twelve times the shared strategy for identical work.

Sessions are also stoppable, which removes the idle tail. `--stop-when-done` calls `POST {agent-endpoint}/sessions/{id}/stop` for every session the run created and the lab verifies the `active` to `idle` transition. A measured six-user pooled burst billed 1.4 sandbox-minutes after an explicit stop instead of 31.4 sandbox-minutes waiting out the idle timeout.

Re-running the same session identifiers after that stop returned new instance identifiers with `resumeCount` of 1, proving that the session filesystem is durable across deprovision while the process is not, and that resume is materially cheaper than a cold start: p50 fell from 42.74 s to 13.67 s.

Choose the strategy from the isolation requirement, not from the load. One session per user is the correct answer when a turn executes untrusted code or handles data that must never share a filesystem, and per-session VM isolation makes that guarantee real. A bounded pool is the correct answer for a conversational agent over shared data, because concurrent requests are a small fraction of signed-in users and the platform still isolates conversation history per user inside a shared session. Data the container stores itself is not partitioned, so showcase memory is keyed by user identity.

```powershell
$invocations = "<main-agent-invocations-url>"

uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\session_scaling_lab.py `
  --invocations-url $invocations `
  --users 12 `
  --max-users-per-session 4 `
  --stop-when-done `
  --out .artifacts\session-scaling-lab.json

uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\session_scaling_lab.py `
  --invocations-url $invocations `
  --mode shared `
  --users 24

azd ai agent sessions list --agent-name foundry-showcase-main --limit 200 -C foundry-showcase
azd ai agent sessions stop <agent-session-id> --agent-name foundry-showcase-main -C foundry-showcase
```

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
  --agent-version 33 `
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
  --agent-version 33 `
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

Push-Location foundry-showcase\annotation-review
uv run pytest -q
Pop-Location

azd ai agent invoke foundry-showcase-main `
  "Use the case tools to get CASE-1001." `
  --version 33 `
  --protocol responses `
  --new-session `
  -C foundry-showcase

azd ai agent invoke foundry-showcase-main `
  --version 33 `
  --protocol invocations `
  --input-file foundry-showcase\main-agent\smoke-invocation.json `
  --new-session `
  -C foundry-showcase

azd ai agent eval run `
  --config eval.yaml `
  --name foundry-showcase-v33-final `
  --no-prompt `
  -C foundry-showcase\main-agent
uv run --project foundry-showcase\experiences python foundry-showcase\experiences\run_local_red_team.py `
  --project-endpoint $projectEndpoint `
  --agent-version 33 `
  --out .artifacts\foundry-showcase-red-team

uv run --project foundry-showcase\main-agent python foundry-showcase\scripts\session_scaling_lab.py `
  --invocations-url "<main-agent-invocations-url>" `
  --users 12 `
  --max-users-per-session 4 `
  --stop-when-done
```

The current regression is 33/33 main-agent tests, 4/4 helper tests, 3/3 AG-UI tests, and 10/10 MCP tests. Final evaluation run `evalrun_ee8329679c7340d2a95047229a347878` evaluated 30 cases against version 26: the domain rubric passed 23, failed 5, and errored 2; task adherence passed 17, failed 10, and errored 3; intent resolution passed 12, failed 15, and errored 3. The aggregate result was 10 passed, 18 failed, and 2 errored. The bounded optimizer run `opt_8e32d2e5f7344b2ab65c2689acd5e9ea` scored the baseline at `0.5259027` and its candidate at `0.512111`; the baseline correctly remained the promoted behavior now deployed with the expanded version-31 capabilities.

Optimizer run `opt_0205d8caeaf54401ac82714d3621701e` against `foundry-showcase-optimize` explored both dimensions across four candidates and beat its baseline. Baseline `0.591`; `candidate_1` skills `0.574`; `candidate_2` system prompt `0.637`, promoted as best; `candidate_3` system prompt `0.584`; `candidate_4` skills `0.607`. Task-weighted average on a 0 to 1 scale, evaluated with `gpt-5.4-mini`, elapsed 54 minutes.

The dedicated Application Insights resource recorded 95 agent, model, and tool trace events across three operations after version 28 deployment (the same instrumentation is carried forward by version 31). The score alert queries portal evaluation telemetry for Task Adherence below `0.5`. Cost remains unset because the Azure Retail Prices API does not publish the contracted regional `gpt-5.4-mini` rate.

The bounded Qwen3-32B Global Standard SFT job `ftjob-d6e97df9e4cd4766ba81e754c848b635` completed one epoch over 20 training and five validation examples. It produced model `qwen3-32b.ft-d6e97df9e4cd4766ba81e754c848b635-foundry-showcase` with training loss `3.2383`, evaluation loss `3.0637`, 3,105 trained tokens, and 2,784 billed tokens. No hosting deployment was created.

## Known constraints

- The subscription disables public Storage endpoints, so the Container Apps environment uses VNet integration, private DNS, and a Table private endpoint.
- Container Apps capacity was unavailable in Sweden Central during deployment; compute is in North Europe while persistent resources remain in Sweden Central.
- Foundry Toolbox uses the Hosted Agent instance identity, not the Agent Identity Blueprint.
- The A2A connection uses Entra token passthrough because the current regional backend rejects the documentation's `AgenticIdentity` discriminator. The caller's instance and blueprint principals have only `Foundry Agent Consumer` on the target project.
- The current upstream client drops approval responses during service-managed continuation. The main agent contains a tested narrow override until the package fixes that behavior.
- The AG-UI BFF uses its managed identity as a federated client assertion for a secretless OBO exchange, so Foundry and the internal `UserEntraToken` A2A connection receive the signed-in user context.
- The Invocations gateway returns `502 Failed to forward request` for `stream=true` before the request reaches the agent. The thin BFF therefore uses a real non-streaming Invocations call and emits the returned text through the AG-UI event stream.
- Non-fatal hosted logs can report duplicate telemetry instrumentation and unavailable optional `agents` instrumentation.
- Agent Optimizer supports Responses targets but rejects agents that also expose Invocations. `foundry-showcase-optimize` is a dedicated Responses-only Hosted Agent kept for this purpose; the multi-protocol `foundry-showcase-main` cannot be optimized in place.
- Evaluation cases 20 and 28 fail before producing a response because the Hosted Agent service raises `'ContentFiltered' is not a valid ContentFilterCodes`. The final dataset and run retain these as explicit operational failures rather than replacing them with easier prompts or synthetic responses.
- Cloud red-team run `evalrun_d78033e5c5a746c1a238ad23d7ad79dc` completed against version 26 with zero attack items. Local SDK run `7627b190-4823-44f6-b265-2cb33da7836f` is portal-visible with six genuine version-27 conversations: protected material and code vulnerability passed, while both ungrounded-attribute attacks succeeded for an overall ASR of 33.33%.
- Harm-category inputs still expose the upstream Hosted Agent error `'ContentFiltered' is not a valid ContentFilterCodes`; the local runner rejects SDK-generated error placeholders instead of counting them as successful safety responses.
- The project now uses showcase-owned Application Insights and Log Analytics resources, and the deployed version emits content-rich user, assistant, and tool events. The trace curator still returns `DataGenerationJobNoTracesFound` for agent name, version, Hosted Agent GUID, and telemetry ID. All eight failed jobs still reject deletion with `unexpectedEntityState`.
- Immediate quality evaluation and the static scheduled validation pass, and the daily 07:00 UTC schedule is provisioned. Continuous evaluation samples only responses the evaluation service can read back. A Prompt Agent invoked through the project Responses API with an `agent_reference` produces a project-scoped `resp_...` id and the sampled run completes. A Hosted Agent called through its own endpoint produces a session-scoped `caresp_...` id, and the sampled run is created but fails with `403 session_not_accessible` before any evaluator runs. An `EvaluationRule` also needs several minutes to activate; responses emitted immediately after `create_or_update` are not sampled, and runs appear roughly six minutes after the response.
- `Purview` is attached to the model policy. Deterministic synthetic email, phone, and SSN examples use the custom blocklist because the advertised granular PII filters are rejected by the regional RAI policy API. Task Adherence is validated against the dedicated East US Content Safety preview because Sweden Central returns feature-unavailable and `TaskAdherence` is rejected inside a model RAI policy.
- Work IQ works for Tomas through delegated Microsoft 365 credentials, but the supported Foundry MCP connection requires tenant-admin consent plus a stored OAuth client secret. Application-only authentication is unsupported, so Work IQ is intentionally not added while the repository requires secretless managed identity and OBO.
- Agent 365 publication is submitted and the Activity/Teams infrastructure is deployed. Tenant-admin approval at `https://admin.cloud.microsoft/?#/agents/all/requested` is the only blocker to registry, Agent User, and Teams interaction validation.
- Concurrent hosted sessions map one-to-one to usable addresses in the delegated subnet by default, so a `/26` supports about 50 concurrent sessions, which is the documented maximum. Azure support can raise the mapping to ten sessions per address on request. A separate regional pool returns `429 regional_session_quota_exceeded`, and deleting your own sessions does not release it. One session per user is therefore infeasible, not merely expensive, for a large consumer-scale chat surface.
- Invocations binds a session only through the `agent_session_id` query parameter. The body fields and `x-agent-session-id` header reach the container but do not select the sandbox. Responses binds through the body `agent_session_id` or a `conversation` identifier.
- Short session identifiers are rejected with `400 invalid_session_id`. The exact rule is undocumented; the lab uses `lab-<run>-<user>` identifiers, which are accepted.
- The main agent still exposes Invocations protocol `1.0.0`, so the `x-ms-user-identity` delegation that protocol `2.0.0` uses for automatic per-user isolation inside a multiplexed session is not exercised by this showcase. Responses is already on `2.0.0`.
- Bursts hit two independent limits. Model throughput returns `429 rate_limit_exceeded` on chat turns, and the agent endpoint returns the same code for request bursts even on the probe action, which performs no model call. The lab retries both with exponential backoff and reports the retry count.
- The non-OpenAI deployments on the Deployments screen — `MAI-Image-2.5`, `DeepSeek-V4-Pro`, `Mistral-Large-3`, `Kimi-K2.6` — were created by hand and are deliberately absent from Terraform. No showcase code calls them; they exist so one screen can tell the multi-vendor story. Keeping them out of Terraform means showcase teardown can never delete models from the shared Foundry account. `Mistral-Large-3` runs on `DataZoneStandard` because `GlobalStandard` quota in Sweden Central is fully consumed by another subscription tenant.
- `terraform/experiences` declares guardrails, RAI policies, and two model deployments in the shared Foundry account but has no state file. `foundry-showcase-guardrail` and `foundry-showcase-gpt-5.2` exist in Azure tracked by nothing.

## Design constraints

- one primary agent and one bounded helper;
- native Foundry capabilities before custom substitutes;
- Terraform with `azapi`;
- Python managed with `uv`;
- scripts rather than portal-only operations;
- no stored credentials;
- no fake platform features or simulated deployment paths.
