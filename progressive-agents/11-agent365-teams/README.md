# Step 11: Agent 365 and Teams

Step 11 publishes the step-10 Hosted Agent through the native Foundry-to-Agent
365 autopilot path. The same container exposes Responses and Activity. Azure Bot
Service is only the Microsoft 365 relay to Foundry's Activity endpoint; there is
no separately hosted or maintained classic Bot Framework application.

## What this step proves

- The Hosted Agent keeps Responses, Invocations, A2A, Toolbox v2, Entra JWT,
  memory, skills, evaluations, and Application Insights behavior.
- The container adds Microsoft 365 Agents SDK Activity handling and explicitly
  converts a Teams Activity into the same Agent Framework run path.
- A channel mention is removed before inference; the Entra sender and Teams
  conversation become the memory user and session scopes.
- The agent version advertises `responses@2.0.0`, `invocations@2.0.0`, and
  `activity_protocol@v1`; its endpoint exposes Responses, Invocations, managed
  A2A, and Activity with `Entra` and `BotServiceRbac` authorization.
- Hosted Invocations memory uses Foundry's injected `x-agent-user-id`; caller
  body/header aliases are accepted only by the local smoke path.
- Foundry's managed agent identity blueprint is reused. The flow does not create
  a second Agent 365 CLI blueprint.
- Terraform/azapi enables Foundry Agent 365 collection and provisions the native
  Bot Service relay plus Teams channel.
- The Foundry publish API submits tenant-scoped autopilot metadata from
  `agent365.toml`.
- The Hosted Agent identity receives the documented
  `Agent365.Observability.OtelWrite` application role.
- Activity turns acquire an Agent 365 exporter token through the documented
  agentic S2S chain: Identity Proxy Manager application token, MSAL client
  assertion exchange, and the S2S observability endpoint.

## Architecture

```text
Teams 1:1 or @mention
  -> Azure Bot Service / Teams channel (native relay only)
  -> Foundry Activity endpoint (BotServiceRbac)
  -> Microsoft 365 Agents SDK FastAPI adapter
  -> Activity identity, mention, conversation conversion
  -> existing small Agent Framework agent
       -> Toolbox v2 -> AgenticIdentity MCP -> support policy
       -> Foundry Memory and local versioned skills

Direct clients
  -> Foundry Responses endpoint (Entra)
  -> the same Agent Framework agent

Both paths
  -> Application Insights
Activity path
  -> agentic S2S token exchange -> Agent 365 observability exporter
```

## Important files

| Path | Purpose |
| --- | --- |
| `main.py` | Shared Responses/Invocations/Activity host and Activity-to-agent conversion. |
| `agent365.toml` | Tenant publication metadata and agentic-user template contract. |
| `smoke_activity.py` | Mention, identity, conversation, and publication-manifest checks. |
| `infra/agent365` | Terraform/azapi collection setting, Bot Service relay, and Teams channel. |
| `scripts/Publish-Agent365.ps1` | Dual-protocol endpoint patch and native Foundry autopilot submission. |
| `scripts/Grant-Agent365Observability.ps1` | Hosted Agent identity role assignment. |
| `scripts/Get-Agent365State.ps1` | Foundry, Entra blueprint, Bot, Teams, and registry evidence. |
| `scripts/Configure-TeamsBlueprint.ps1` | Supported Teams Developer Portal backend configuration after approval. |
| `scripts/Provision-Step11.ps1` | No-argument classroom provisioning, validation, publication, and evidence flow. |
| `scripts/Cleanup-Step11.ps1` | Explicit Terraform cleanup and optional Hosted Agent deletion. |
| `DEPLOYMENT_EVIDENCE.md` | Sanitized live resource, trace, evaluation, and boundary evidence. |

The current native Foundry sample does not require a Teams app zip or icon.
Those artifacts belong to the Agent 365 CLI custom-engine package path and would
create a second publication contract here, so this step intentionally does not
generate them.

## Prerequisites and boundaries

- Python 3.11-3.13 and `uv`.
- Terraform 1.7 or later.
- Azure CLI, Azure Developer CLI, and Agent 365 CLI.
- Owner on the Azure scope, Foundry User or Foundry Project Manager on the
  project, and permission to create Bot Service resources.
- AI Administrator or Global Administrator for Microsoft 365 approval.
- A Microsoft 365 license for creators, approvers, and users. At least one
  qualifying Agent 365 license, such as Microsoft 365 Copilot, must exist in the
  organization.
- Microsoft documents Agent 365 as generally available. The current Foundry
  autopilot sample still states that Frontier enrollment is required. Treat the
  live Foundry `a365Status` as the authoritative tenant data-flow state and keep
  the sample's Frontier requirement as a publication prerequisite until
  Microsoft removes it.
- Agent 365 registry Package Management APIs additionally require an Agent 365
  license and `CopilotPackages.Read.All`. Azure CLI's first-party app cannot
  request that scope unless Microsoft preauthorizes it.
- Admin approval and Teams instance creation remain Microsoft 365 control-plane
  actions. Do not report Teams validation until an approved instance exists.

## Local validation

```powershell
cd progressive-agents\11-agent365-teams
uv sync --frozen
uv run python smoke_mcp.py
uv run python smoke_skills.py
uv run python smoke_memory.py
uv run python smoke_activity.py
```

`smoke_activity.py` validates the exact channel mention removal, Entra user
scope, conversation scope, and TOML publication request.

## Primary classroom path

```powershell
cd progressive-agents\11-agent365-teams
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
.\scripts\Provision-Step11.ps1
```

With no existing azd environment, the script creates
`11-agent365-teams`. It then:

1. runs the local MCP, skill, memory, and Activity checks;
2. provisions step-specific Terraform/azapi resources and Entra MCP identity;
3. creates Toolbox and skill v1/v2 and deploys the dual-protocol Hosted Agent;
4. accepts pinned v2, publishes the unversioned v2 consumer, and proves v1/v2
   rollback;
5. enables Foundry Agent 365 collection;
6. provisions the native Bot Service relay and Teams channel;
7. grants Agent 365 observability to the Hosted Agent identity;
8. patches the endpoint to retain Responses plus Activity and submits the
   tenant-scoped autopilot request;
9. attempts supported Teams blueprint configuration and registry reads;
10. runs deployed behavior and model-judged evaluations; and
11. writes `evidence\deployment.json`, then exits nonzero if a real external
    approval, license, consent, or client boundary remains.

No failure is converted into a successful Teams result.

## Approval and Teams validation

After publication, an AI Administrator or Global Administrator opens:

`https://admin.cloud.microsoft/?#/agents/all/requested`

Approve and activate the pending title. Then run:

```powershell
.\scripts\Configure-TeamsBlueprint.ps1 -BlueprintClientId <blueprint-client-id>
```

In Teams, create an agent instance from **Apps > Agents for your team**. Validate:

1. a 1:1 support-policy question;
2. a channel `@mention` with the same question;
3. the reply contains the same policy result as Responses;
4. the Activity conversation and user remain isolated in memory; and
5. the invocation appears in Agent 365 observability and Application Insights.

These are manual acceptance steps because no supported CLI/API creates the
Teams agent instance or sends an interactive user chat before approval.

## Cleanup

```powershell
.\scripts\Cleanup-Step11.ps1
```

Add `-DeleteHostedAgent` to remove the Hosted Agent after dependent resources
are destroyed. Microsoft 365 approval/catalog cleanup remains an admin action;
the script does not pretend to remove a title it cannot query.

## Current deployed boundary

The live deployment and autopilot submission succeeded. Foundry reports Agent
365 enabled and the observability role is assigned. Microsoft 365 Package
Management reads are denied because the available first-party CLI clients lack
the required preauthorized `CopilotPackages.Read.All` token, and Teams
Developer Portal backend configuration returns 403 before admin activation.
Consequently, 1:1 chat, channel mention, and Activity-export trace validation
are not claimed complete. See `DEPLOYMENT_EVIDENCE.md`.

## Next capability

Step 12 adds a Foundry Routine only after this step's admin approval and real
Teams acceptance gate are complete.
