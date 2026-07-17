# Foundry Showcase Plan

## Objective

Build one cohesive, presentation-ready Microsoft Foundry solution from the capabilities already proven in `progressive-agents`.

This is not another gradual tutorial and it is not a smaller version of `autopilots-on-azure`. It is the final-state Foundry reference demo:

- one primary business agent;
- one bounded specialist helper;
- platform-managed hosting, memory, tools, identity, scheduling, observability, evaluation, optimization, safety, and Microsoft 365 publication;
- no Hermes, OpenClaw, runtime fleet, m:n group chat, swarm learning, or custom autonomous-worker infrastructure.

## Implementation status

All implementation compatible with the repository's secretless architecture is deployed and live-validated:

- active Hosted Agent `foundry-showcase-main`, only retained version 27;
- active LangGraph Hosted Agent `foundry-showcase-policy-helper`, version 2;
- Responses `2.0.0` and Invocations `1.0.0`;
- Foundry Toolbox `foundry-showcase-support`, default version 3;
- published skills `support-style`, `escalation-policy`, and `profile-update-policy`, version 1;
- Entra-protected case MCP using the Hosted Agent instance identity;
- Azure Table Storage with public access disabled, reached through a private endpoint;
- user-scoped Foundry Memory;
- read tools and proposal creation without approval;
- write approval request, pre-approval blocking, successful continuation, single apply, and audit behavior;
- OpenTelemetry and Application Insights wiring;
- 10/10 local MCP tests, 20/20 main-agent tests, 4/4 helper tests, and 3/3 AG-UI tests;
- deployed Responses read, skill, memory, proposal, approval, write, and restore validation;
- deployed structured Invocations read validation;
- promoted-version evaluation `evalrun_ee8329679c7340d2a95047229a347878`: 30 cases, with 23 domain-rubric, 17 task-adherence, and 12 intent-resolution passes; two cases retain upstream Hosted Agent content-filter enum failures;
- checkpointed MAF case-resolution workflow with deterministic risk branching and restoration;
- authenticated RemoteA2A project connection and policy-delegation Toolbox;
- exact policy delegation from the workflow and structured Invocations;
- policy contradictions rejected before proposal creation;
- correlated trace operation spanning the primary agent, A2A Toolbox call, and helper;
- recurring weekday and one-time Routines with completed manual and timer-delivered runs;
- Entra-authenticated AG-UI web client and thin Container Apps BFF;
- secretless managed-identity federation and OBO exchange for delegated Foundry access;
- authenticated AG-UI smoke response `AG-UI showcase ready`.
- Agent 365 Activity endpoint, Bot Service Teams channel, AgentData permission grant, and publication `1.0.1`;
- bounded optimizer operation `opt_8e32d2e5f7344b2ab65c2689acd5e9ea`: baseline `0.5259027`, candidate `0.512111`, baseline retained;
- final multi-protocol canary and promotion as version 26;
- Application Insights operational metric export for latency, reliability, token use, and helper-call rate;
- cloud AI red-team run `evalrun_d78033e5c5a746c1a238ad23d7ad79dc` with a reviewed taxonomy and real tool descriptions.
- 12 Stored Completions and runtime-aligned persistent Memory records for Tomas;
- custom Guardrail `foundry-showcase-sensitive-data` with validated blocking;
- two rich PDF assets analyzed by Content Understanding with figure verbalization and chart extraction;
- Foundry IQ document and web sources, Search index, knowledge base, MCP connection, Toolbox integration, and cited retrieval;
- sequential `process_invoice` workflow alongside the durable human-in-the-loop case workflow;
- portal-visible local red-team run `7627b190-4823-44f6-b265-2cb33da7836f` with six genuine version-27 conversations and 33.33% ASR.

The current Agent Framework/OpenAI client drops approval responses on service-managed continuation turns. `ApprovalContinuationFoundryChatClient` restores only the current response while continuing to suppress replayed approval history. The override is unit-tested and should be removed when the upstream package includes the fix.

Hosted Toolbox token acquisition uses the Hosted Agent version's instance identity, not its Agent Identity Blueprint. Toolbox approval mappings use the composite names exposed by Foundry, such as `case-write___apply_case_update`.

Tenant-admin approval remains the Phase 4 dependency before Agent 365 registry, Agent User, and Teams interaction can be validated. The trace-derived dataset curator still reports no traces despite corrected RBAC and queryable content-rich telemetry. Work IQ is not added because its supported Foundry connection requires delegated admin consent and a stored OAuth client secret, while application-only authentication is unsupported. Harm-category requests also retain the upstream `'ContentFiltered' is not a valid ContentFilterCodes` defect. These are recorded as product or security boundaries rather than hidden behind simulated behavior.

## Demo story

Use a support-operations scenario because it makes every capability visible without requiring a complicated domain.

The primary agent helps a support lead:

- answer policy and customer-support questions;
- inspect a synthetic but realistically hosted case system through governed MCP tools;
- remember user preferences and previous case context;
- delegate policy analysis to a specialist helper;
- run a deterministic approval workflow for high-impact actions;
- produce a scheduled daily quality digest;
- operate from Teams and a simple AG-UI web client;
- improve only after evaluation and human approval.

The business data may be synthetic, but every platform interaction must be real: real Hosted Agents, real A2A, real Toolbox MCP, real Foundry Memory, real traces, real evaluations, real optimization jobs, and real Agent 365 publication.

## Final architecture

```text
Teams / Microsoft 365                 AG-UI web or TUI
          |                                  |
          | Activity bridge                  | Entra auth
          v                                  v
     Agent 365                         thin ACA BFF
          |                                  |
          +---------------+------------------+
                          |
                          v
        Main Foundry Hosted Agent - Microsoft Agent Framework
        Responses + Invocations + Activity
        - conversation and policy reasoning
        - Foundry Memory
        - MAF declarative/code-first workflow
        - Toolbox MCP client
        - A2A client
             |                 |                    |
             v                 v                    v
      Foundry Toolbox     Foundry Memory      LangGraph helper
      tools + skills      user scoped         Hosted Agent + A2A
             |
             +--> case MCP
             +--> Foundry IQ document + web retrieval
             +--> controlled case-update tool

Foundry Routine -> Main Agent -> daily review workflow

OpenTelemetry / Application Insights / Foundry traces
        -> evaluations
        -> Agent Optimizer candidates
        -> AI Red Teaming Agent
        -> reviewed version promotion
```

## Agent roles

### Primary agent

Framework: Microsoft Agent Framework.

Responsibilities:

- own the user conversation;
- decide when tools, memory, workflow, or specialist delegation are needed;
- preserve user scope and conversation identity;
- enforce confirmation before high-impact changes;
- summarize helper output rather than forwarding it blindly;
- expose Responses for direct and Microsoft 365 use;
- expose Invocations for structured AG-UI and Routine requests.

### Specialist helper

Framework: LangGraph.

Purpose: demonstrate that Foundry Hosted Agents and A2A are framework-neutral.

The helper performs one narrow task: policy and escalation analysis. It receives a sanitized case summary, retrieves relevant policy context, and returns structured findings:

```json
{
  "risk_level": "low|medium|high",
  "applicable_policies": [],
  "required_approvals": [],
  "recommended_action": "",
  "reasoning_summary": ""
}
```

The helper:

- runs as a separate Foundry Hosted Agent;
- exposes an A2A endpoint and agent card;
- has no direct user channel;
- has read-only tools;
- receives only the minimum context needed;
- cannot execute case changes.

The primary MAF agent calls it through a Foundry A2A project connection and retains control of the conversation.

## Foundry capabilities demonstrated

| Capability | Demonstration |
| --- | --- |
| Hosted Agents | Separate MAF and LangGraph containers with immutable versions and managed endpoints. |
| Responses | Main conversational protocol and platform bridge to Microsoft 365. |
| Invocations | Structured AG-UI and scheduled-operation payloads. |
| A2A | Main MAF agent delegates policy analysis to the LangGraph helper. |
| Agent identity | Per-agent Entra identities with least-privilege access. |
| Foundry Memory | Per-user profile, durable preferences, and summarized case context. |
| Stored Completions | Twelve real retained Responses completions visible in the Data tab. |
| Guardrails | Custom sensitive-data blocklist with positive and negative validation. |
| Content Understanding | Rich PDFs analyzed with figure verbalization, chart extraction, and model aliases. |
| Foundry IQ | Search index, document and web knowledge sources, knowledge base, MCP connection, and Toolbox tool. |
| Sessions and conversations | Platform conversation continuity plus explicit user isolation. |
| Toolbox | Versioned collection of tools, MCP connections, and skills. |
| MCP | Entra-protected case service consumed through the Toolbox MCP endpoint. |
| Skills | Versioned support style, escalation policy, and profile-update policy packages. |
| Workflows | Durable HIL case workflow plus sequential invoice prepare, validate, and route workflow hosted inside the main agent. |
| Routines | Recurring daily support-quality review and one-time follow-up example. |
| Observability | End-to-end traces across BFF, main agent, workflow nodes, MCP, memory, and A2A helper. |
| Evaluations | Golden datasets, generated suites, built-in and custom evaluators, and trace evaluation. |
| Optimization | Foundry Agent Optimizer reviewed an instruction candidate; its regression was rejected and the baseline was promoted. |
| Red teaming | Cloud taxonomy run plus a portal-visible local scan with six genuine Hosted Agent conversations and inspectable ASR findings. |
| Work IQ | Delegated access works for Tomas, but Foundry integration is blocked by the documented client-secret requirement and secretless repository policy. |
| Versioning | Baseline, candidate, canary, and promoted Hosted Agent versions. |
| Agent 365 | Identity governance, Activity bridge, Bot Service, Teams channel, and autopilot publication are deployed; tenant approval remains external. |

## Tools and Toolbox

Create one Foundry Toolbox with immutable versions.

Initial contents:

- `search_cases`: read-only case search;
- `get_case`: read-only case detail;
- `propose_case_update`: creates a noncommitted proposal;
- `apply_case_update`: high-impact write requiring a confirmed workflow state;
- policy or Azure AI Search capability;
- support and escalation skills.

The case service should be a small Entra-protected MCP server on Azure Container Apps. Use workload identity and scopes or app roles; do not store API keys.

The main agent uses the Toolbox MCP endpoint rather than wiring every tool independently. Promote a new Toolbox version only after tool contract tests and agent evaluations pass.

## Memory model

Use Foundry Memory with explicit per-user scope.

Store:

- stable user preferences;
- concise support-lead profile;
- approved summaries of prior case discussions;
- explicit remember and forget requests.

Do not store:

- raw secrets or credentials;
- entire tool payloads by default;
- unverified profile guesses;
- adversarial red-team content;
- helper-agent scratch state.

Keep raw conversation history separate from distilled long-term memory. Audit memory creation, update, deletion, and summary generation.

## Workflow design

Use Microsoft Agent Framework declarative YAML or code-first workflows inside the main Hosted Agent.

Do not build a new dependency on the Foundry portal workflow designer. Microsoft currently documents that designer and in-portal execution as retiring on December 1, 2026. The recommended path is Agent Framework workflows deployed as Hosted Agents.

Workflow: `resolve_support_case`

```text
validate request
  -> load user and case context
  -> retrieve case and policies
  -> delegate policy analysis through A2A when needed
  -> create structured proposed action
  -> evaluate risk
       low    -> present proposal
       medium -> request explicit user confirmation
       high   -> require approval and refuse autonomous execution
  -> apply permitted update
  -> record audit and memory summary
```

The workflow demonstrates deterministic state, branching, tool boundaries, and human confirmation. A second `process_invoice` workflow runs prepare, validate, and route stages and produces `auto_post`, `finance_review`, or `rejected` outcomes. Both deliberately avoid group-chat orchestration.

## Routine design

Foundry Routines answer when the agent should run; the MAF workflow answers how the task executes.

### Recurring routine

`daily-support-quality-review`

- recurring weekday schedule;
- invokes the main Hosted Agent;
- sends structured read-only input through Invocations;
- reviews unresolved cases and yesterday's quality signals;
- produces a digest stored as a traceable run result;
- does not send external messages or mutate cases automatically.

### One-time routine

`case-follow-up-reminder`

- fires once at a specified time;
- asks the main agent to inspect one case and prepare a recommendation;
- becomes inactive after execution.

Demonstrate routine lifecycle, run history, response linkage, traces, enable/disable, and cleanup. Keep at least a five-minute interval where required by preview limits.

## Microsoft 365 and Agent 365

Publish the main Hosted Agent through Foundry's native Agent 365 autopilot path.

Demonstrate:

- automatic Agent 365 registry presence;
- blueprint approval;
- Agent User and Agent Identity behavior;
- Teams 1:1 conversation;
- channel mention where supported;
- Agent 365 activity collection and governance;
- clear data-residency and licensing notes.

Use the platform Responses-to-Activity bridge. Do not create a separate classic Azure Bot fallback.

The LangGraph helper remains internal and is not separately published to Teams.

## Observability

Instrument:

- channel or BFF ingress;
- main-agent request;
- memory search and update;
- skill load;
- Toolbox and MCP calls;
- workflow nodes and decisions;
- A2A delegation and helper processing;
- Routine invocation;
- model name, latency, tokens, and estimated cost;
- confirmation and policy outcomes.

Use W3C trace context and one correlation ID across the full request. Hosted Agent protocol libraries provide OpenTelemetry integration and Foundry injects Application Insights configuration.

The demo dashboard should answer:

- Which path handled the request?
- Was the helper called?
- Which tools and skills were selected?
- What memory was read or changed?
- Where was time and cost spent?
- Did the workflow require approval?
- Which agent and Toolbox versions ran?

## Evaluation strategy

Keep evaluation assets in source control:

```text
eval.yaml
datasets/
evaluators/
redteam/
```

Evaluation layers:

1. Unit and contract tests for tools, workflow nodes, memory guards, and A2A schemas.
2. Direct target evaluations for synchronous Responses and Invocations behavior.
3. Trace evaluations for Teams Activity, A2A, streaming, and workflow trajectories.
4. Quality evaluators for task adherence, groundedness, policy accuracy, escalation correctness, tool selection, and response style.
5. Operational metrics for latency, token use, cost, tool failure rate, and helper-call rate.
6. Safety evaluations and red-team Attack Success Rate.

Use the same pinned `eval.yaml`, dataset versions, and evaluator versions when comparing agent releases.

## Optimization and guarded improvement

Prepare the main Hosted Agent for the Foundry Agent Optimizer.

Optimization targets:

- instructions;
- `SKILL.md` content;
- tool and parameter descriptions;
- model selection for quality-to-cost trade-offs.

Process:

```text
baseline version
  -> evaluation
  -> optimizer candidates
  -> candidate evaluation
  -> human review of score, behavior, cost, and diff
  -> apply selected candidate locally
  -> deploy immutable candidate version
  -> canary validation
  -> explicit promotion
```

Never auto-promote an optimizer result. Treat improvements below 0.03 as likely noise unless repeated evidence shows otherwise. Use isolated or read-only tools during optimization because every candidate evaluation can invoke external tools.

## Red teaming

Run the AI Red Teaming Agent against the main Hosted Agent in a dedicated test environment.

Cover:

- prohibited actions;
- sensitive-data leakage;
- task adherence;
- direct and indirect prompt injection;
- harmful content categories relevant to the scenario;
- attempts to bypass confirmation or invoke the write tool.

Current platform constraints must shape the test:

- Hosted Agents are supported targets;
- Foundry workflow agents are not supported targets;
- some agentic tests support Azure tools but not function, connected-agent, or arbitrary non-Azure tools;
- harmful data should never enter normal long-term memory;
- results require human review because ASR scoring can be nondeterministic.

Where a full live-tool red-team path is unsupported, test the core Hosted Agent with supported synthetic tools and separately run deterministic policy tests against the real MCP contracts.

The version 26 cloud run used seven enabled prohibited-action categories, direct and indirect attack strategies, and descriptions of the real case, memory, and A2A tools. The service completed with zero output items and no error. Version 27 was then scanned locally through its real Responses endpoint. Portal run `7627b190-4823-44f6-b265-2cb33da7836f` contains six baseline and tense attacks: protected material and code vulnerability passed, while two ungrounded-attribute attacks succeeded. The local runner rejects callback error placeholders so platform failures cannot be misreported as safe responses.

## Infrastructure and repository shape

Proposed structure:

```text
foundry-showcase/
  README.md
  PLAN.md
  pyproject.toml
  uv.lock
  azure.yaml
  main-agent/
  helper-langgraph/
  bff/
  tools/case-mcp/
  skills/
  workflows/
  evals/
  redteam/
  scripts/
  terraform/
```

Use Terraform with `azapi` for Azure resources and `azd` for Hosted Agent packaging and deployment where the platform requires it. Keep imperative publication, evaluation, optimization, and Routine operations in explicit `uv run` scripts.

## Delivery phases

### Phase 1: Consolidate the proven baseline — complete

- copy the latest useful implementation from Progressive Agents;
- remove step-specific naming and duplicated historical code;
- deploy one clean MAF Hosted Agent;
- establish tests, traces, and a concise README.

### Phase 2: Governed tools, skills, and memory — complete

- deploy the case MCP;
- create Toolbox and skill versions;
- connect Foundry Memory;
- validate identity, user scope, audit, and tool contracts.

### Phase 3: Workflow and cross-framework A2A — complete

- implement the MAF case-resolution workflow;
- deploy the LangGraph helper;
- enable A2A and project connection;
- validate delegation, state, confirmation, and trace correlation.

### Phase 4: Routines and user surfaces — external approval pending

- create recurring and one-time Routines — complete;
- deploy the thin AG-UI BFF — complete;
- publish the main agent to Agent 365 and Teams — publication submitted;
- validate direct, web, scheduled, and Teams paths — direct, web, scheduled, Activity configuration, and Teams channel complete; tenant approval blocks Teams conversation validation.

### Phase 5: Quality, optimization, safety, and portal experiences — complete with preview limitations

- finalized a 30-case generated dataset and three pinned evaluators;
- ran direct quality evaluation and exported trace metrics; two cases retain upstream Hosted Agent content-filter enum failures;
- ran Agent Optimizer, rejected the regressing candidate, and retained the baseline;
- submitted reviewed AI Red Teaming Agent scans and uploaded a six-conversation local scan with actionable findings;
- canary-tested the baseline winner, then promoted the Foundry IQ, workflow, and content-tracing expansion as immutable version 27;
- populated Stored Completions, Memory, Guardrails, Content Understanding, and Foundry IQ;
- investigated trace curation and Work IQ to their supported product and security boundaries.

## Completion criteria

The showcase is complete when:

1. the main MAF Hosted Agent works through direct Responses, structured Invocations, AG-UI, and Teams;
2. the LangGraph helper is independently hosted and invoked only through authenticated A2A;
3. Toolbox tools and skills are versioned and accessed through MCP;
4. Foundry Memory recalls user-scoped information across sessions without leakage;
5. the MAF workflow demonstrates branching and human confirmation;
6. both Routine types run and have inspectable history and traces;
7. end-to-end traces connect ingress, memory, tools, workflow, and helper;
8. evaluation results compare immutable versions reproducibly;
9. optimizer candidates are reviewed and only an approved winner is deployed and canary-tested without automatic promotion;
10. red-team findings and mitigations are documented;
11. Agent 365 registration, governance, and Teams interaction are validated;
12. no classic bot fallback, stored secret, simulated platform feature, or Autopilots runtime code is present.

Criteria 2 through 10 and 12 are satisfied. Criteria 1 and 11 await tenant-admin approval for Agent 365 and Teams. Trace-derived dataset generation remains blocked by the preview curator, and Work IQ remains excluded because the only documented Foundry connection requires a stored client secret.

## Research conclusions

- Hosted Agents can use Microsoft Agent Framework, LangGraph, or custom code and can expose Responses, Invocations, Activity, and A2A protocols.
- A2A is the appropriate lightweight boundary for one main agent delegating to one specialist while retaining control.
- Routines provide one trigger and one agent action with run history; they do not replace orchestration.
- The Foundry visual workflow designer is retiring on December 1, 2026. New workflow work should use Microsoft Agent Framework declarative YAML or code-first workflows deployed as Hosted Agents.
- Foundry Toolbox is the preferred governed surface for tools, MCP connections, and skills.
- The Agent Optimizer can improve instructions, skills, tool descriptions, and model choice from evaluation signal.
- Direct target evaluations fit synchronous Responses and Invocations; A2A, Activity, streaming, and complex trajectories should use trace evaluation.
- AI Red Teaming supports Hosted Agents, but tool and workflow support has important preview constraints that must be demonstrated honestly.
- Work IQ uses delegated user authorization and honors Microsoft 365 permissions, but its current Foundry connection does not support application-only or managed-identity authentication.

## Primary references

- [Foundry Hosted Agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Connect to an A2A endpoint](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent)
- [Foundry Routines](https://learn.microsoft.com/azure/foundry/agents/concepts/routines)
- [Foundry workflow migration guidance](https://learn.microsoft.com/azure/foundry/agents/concepts/workflow#migration-guide)
- [Microsoft Agent Framework workflows](https://learn.microsoft.com/agent-framework/workflows/)
- [Foundry Toolbox](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox)
- [Foundry Agent Optimizer](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-optimizer-overview)
- [Agent evaluations with azd](https://learn.microsoft.com/azure/foundry/observability/how-to/azure-developer-cli-evaluation)
- [AI Red Teaming Agent](https://learn.microsoft.com/azure/foundry/concepts/ai-red-teaming-agent)
- [Connect Foundry to Work IQ](https://learn.microsoft.com/microsoft-365/copilot/extensibility/work-iq/mcp/quickstart/foundry)
- [Agent 365 integration with Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-365-integration)
