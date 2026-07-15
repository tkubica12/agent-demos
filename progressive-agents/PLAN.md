# Progressive Agents Plan

## Purpose

Build a small Microsoft Agent Framework agent one capability at a time. Every numbered directory is a complete runnable demonstration, not a partial branch of a future application.

The project answers:

- What Foundry capability was added?
- What changed in the architecture?
- How is it invoked locally and when hosted?
- What can be observed, evaluated, or governed?
- What limitation remains for the next step?

This is not the advanced autonomous-worker demo. OpenClaw, Hermes, m:n group conversations, general-purpose worker sandboxes, replica fleets, distributed learning, and swarm skill consolidation belong to `autopilots-on-azure`.

## Project rules

1. Copy the previous numbered directory.
2. Change only what the new step requires.
3. Keep the primary agent small, domain-focused, and easy to inspect.
4. Prefer Foundry-managed capabilities over custom substitutes.
5. Keep gateways thin; agent behavior belongs in the hosted agent.
6. Keep local and hosted smoke tests.
7. Add evaluations when behavior quality changes.
8. Document exact preview limitations rather than hiding them behind fallbacks.

## Core architecture

```text
Teams / direct client / AG-UI web or TUI
                    |
                    v
        Foundry Hosted Agent endpoints
        - Responses
        - Invocations
        - A2A where demonstrated
                    |
                    v
       small Microsoft Agent Framework agent
          |         |          |
          |         |          +--> bounded A2A helper
          |         +-------------> Foundry Memory
          +-----------------------> Toolbox, MCP, skills, tools

Traces and evaluations -> Foundry Observability / Application Insights
Routine trigger        -> existing hosted agent
```

## Implemented stages

| Step | Directory | Capability |
| --- | --- | --- |
| 01 | `01-foundry-responses-baseline` | Smallest Hosted Agent through the Responses protocol. |
| 02 | `02-foundry-invocations-baseline` | Custom JSON contract through Invocations while preserving Responses. |
| 03 | `03-a2a-enabled-responses-agent` | Foundry-managed incoming A2A endpoint and agent card. |
| 04 | `04-ag-ui-local-adapter` | Local AG-UI adapter and streaming client. |
| 05 | `05-ag-ui-authenticated-gateway` | Thin authenticated ACA BFF that calls the Hosted Agent. |
| 06 | `06-foundry-observability-evals` | OpenTelemetry, Application Insights, correlation, generated datasets, and evaluations. |
| 07 | `07-foundry-memory-profile` | Foundry Memory, scoped profiles, conversation history, summaries, and audit. |
| 08 | `08-skill-system-baseline` | Microsoft Agent Framework skills using versionable `SKILL.md` packages. |

## Planned stages

### Step 09: Evaluation-driven optimization

Directory: `09-foundry-agent-optimizer`

Prepare the Hosted Agent for the Foundry Agent Optimizer.

Demonstrate:

- a versioned baseline configuration;
- a reproducible `eval.yaml`;
- representative tasks and evaluators;
- instruction optimization;
- skill improvement;
- tool-description optimization;
- model selection for quality and cost;
- explicit review before applying and deploying a candidate.

The optimizer may propose improvements, but it must not silently alter the active agent. Tools used during optimization must be read-only or point to an isolated test environment.

### Step 10: Toolbox, tools, skills, and MCP governance

Directory: `10-foundry-toolbox`

Replace direct tool wiring with a versioned Foundry Toolbox exposed through its MCP endpoint.

Demonstrate:

- a toolbox definition and immutable version;
- an Entra-protected MCP server;
- managed authentication through Foundry connections;
- skill references attached to the toolbox;
- tool and skill discovery through MCP;
- least-privilege agent identity;
- a deliberate toolbox version promotion.

Keep the business scenario small. The purpose is governance and discovery, not a large tool ecosystem.

### Step 11: Agent 365 and Teams publication

Directory: `11-agent365-teams`

Publish the existing Hosted Agent through the native Foundry-to-Agent 365 path.

Demonstrate:

- Foundry registry synchronization;
- autopilot publication and admin approval;
- Agent 365 identity and observability;
- Teams 1:1 interaction and channel mention where supported;
- Responses-to-Activity platform bridging;
- exact documentation of tenant, licensing, and preview limitations.

Do not introduce a separately maintained classic Azure Bot compatibility path.

### Step 12: Foundry Routines

Directory: `12-foundry-routines`

Use a Foundry Routine for lightweight autonomous execution.

Scenario:

```text
Recurring schedule -> invoke hosted agent with read-only review request
                   -> inspect memory and tool data
                   -> produce daily support-quality digest
                   -> routine run history and trace
```

Demonstrate:

- recurring and one-time triggers;
- text or structured JSON input;
- enable, disable, update, and delete lifecycle;
- run history linked to agent responses and traces;
- idempotent, read-only scheduled behavior.

A Routine answers when one agent should run. It must not be used as a custom workflow engine.

### Step 13: Code-first Agent Framework workflow

Directory: `13-agent-framework-workflow`

Add a bounded deterministic workflow inside the Hosted Agent using Microsoft Agent Framework declarative YAML or code-first workflow APIs.

Scenario:

```text
classify request
  -> retrieve policy
  -> draft action
  -> branch on risk
  -> request human confirmation for high-impact action
  -> execute or return safe recommendation
```

Demonstrate:

- typed state passed between workflow nodes;
- sequential and conditional execution;
- one human-in-the-loop boundary;
- trace visibility for each node;
- deployment as a normal Foundry Hosted Agent.

Do not build on the retiring Foundry portal workflow designer. Microsoft documents its retirement for December 1, 2026 and recommends Microsoft Agent Framework workflows deployed as Hosted Agents.

### Step 14: Bounded cross-framework A2A helper

Directory: `14-langgraph-a2a-helper`

Keep the primary agent in Microsoft Agent Framework. Add one focused helper authored with LangGraph and hosted separately in Foundry.

Demonstrate:

- LangGraph Hosted Agent with an A2A endpoint and agent card;
- a Foundry project connection for A2A authentication;
- the main agent using the Foundry A2A tool;
- clear delegation criteria;
- the main agent retaining control and summarizing the helper result;
- trace correlation across both agents.

This is a single specialist delegation example, not group chat or a general multi-agent runtime.

### Step 15: Safety, red teaming, and continuous quality

Directory: `15-foundry-safety-quality`

Complete the learning path with a measured safety and quality loop.

Demonstrate:

- regression, task-adherence, groundedness, tool-selection, and cost evaluations;
- trace evaluation for Activity and A2A paths;
- AI Red Teaming Agent scans against the Hosted Agent;
- agentic risk tests for prohibited actions, sensitive-data leakage, task adherence, and prompt injection where supported;
- comparison of baseline and optimized versions;
- a promotion gate that requires evaluation thresholds and human approval.

Document red-team support boundaries. Current Foundry documentation supports Hosted Agents but not workflow agents, and some agentic tests support only specific Azure tool types.

## Removed roadmap directions

The previous roadmap included worker sandbox dispatch, multi-replica personalization, and cross-replica skill distillation. Those topics now belong to `autopilots-on-azure`, where persistent autonomous runtimes and learning architecture are the subject of the demo.

Progressive Agents ends with a well-governed Foundry agent and one bounded A2A helper, not an autonomous fleet.

## Technology choices

| Area | Choice |
| --- | --- |
| Language | Python |
| Package management | `uv`, committed `uv.lock` |
| Main framework | Microsoft Agent Framework |
| Helper framework | LangGraph only in the dedicated A2A stage |
| Runtime | Foundry Hosted Agents |
| Protocols | Responses first, Invocations when custom payloads are needed, A2A for bounded delegation |
| UI | Thin AG-UI adapter and authenticated BFF |
| Memory | Foundry Memory with per-user scope |
| Tools and skills | Foundry Toolbox MCP endpoint and versioned skills |
| Scheduling | Foundry Routines |
| Workflows | Microsoft Agent Framework declarative or code-first workflows |
| Observability | OpenTelemetry, Application Insights, Foundry traces |
| Quality | Foundry evaluations, Agent Optimizer, AI Red Teaming Agent |
| Microsoft 365 | Native Foundry and Agent 365 publication path |

## Completion standard for every step

A step is complete only when:

1. local behavior works where the capability supports local execution;
2. the Hosted Agent deploys and is invokable;
3. the smallest relevant tests and smoke scripts pass;
4. real traces or platform evidence demonstrate the added capability;
5. the README explains commands, validation, limitations, and the next step;
6. the following stage can copy it without carrying obsolete alternatives.

## Primary references

- [Foundry Hosted Agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Agent-to-Agent tool](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/agent-to-agent)
- [Foundry Routines](https://learn.microsoft.com/azure/foundry/agents/concepts/routines)
- [Microsoft Agent Framework workflows](https://learn.microsoft.com/agent-framework/workflows/)
- [Foundry Toolbox](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/toolbox)
- [Foundry Agent Optimizer](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-optimizer-overview)
- [Agent evaluations with azd](https://learn.microsoft.com/azure/foundry/observability/how-to/azure-developer-cli-evaluation)
- [AI Red Teaming Agent](https://learn.microsoft.com/azure/foundry/concepts/ai-red-teaming-agent)
- [Agent 365 integration with Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-365-integration)
