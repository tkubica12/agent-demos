# Microsoft Foundry Technical Workshop - Internal Delivery Plan

## 1. Purpose

Design and deliver a full-day technical workshop that makes a credible case for Microsoft Foundry as a customer's enterprise platform for custom AI applications and agents.

The workshop should balance:

- **hands-on experience**, because participants remember what they use and enjoy working with a real system;
- **art-of-the-possible demonstrations**, because provisioning, access, deployment and troubleshooting make hands-on work too slow to expose the complete platform;
- **architecture discussion**, because Foundry's strongest differentiation appears when models, runtime, knowledge, tools, quality, security and governance are considered together.

The central proposition is:

> Build agents with the framework that fits the team, run them as managed Foundry hosted agents, ground and equip them with governed enterprise capabilities, and improve them continuously through integrated evidence, evaluation and controls.

This is a professional builder workshop, not a model catalogue tour. It focuses on customer teams creating custom solutions with code, models, enterprise data and APIs, from development through production operations.

The primary audience is engineering leaders, architects, AI engineers, data engineers and developers. Platform and security specialists are important stakeholders, but landing-zone and network implementation are not the center of the day.

## 2. Recommended strategic position

### Lead with the production loop, not individual features

Foundry is most compelling as a connected platform:

```text
                       BUSINESS OUTCOME
                              |
                              v
                    Agent experience/channel
                 Application, Teams or M365 Copilot
                              |
                              v
              Microsoft Agent Framework, LangGraph
                    or another agent framework
                              |
                              v
                   Foundry hosted agent runtime
              Identity, sessions, scale and protocols
                              |
              +---------------+---------------+
              |                               |
              v                               v
       Governed knowledge                Governed actions
    Foundry IQ and Microsoft IQ       MCP, Toolboxes and APIs
              |                               |
              +---------------+---------------+
                              |
                              v
                 Traces, evaluations, safety
                    monitoring and red teams
                              |
                              v
             Entra, Agent 365, APIM, networking,
                 regional controls and encryption
```

The platform message is not that every component is unique. It is that Foundry provides a coherent path from experimentation to a measurable and governable production system while retaining model and framework choice.

### Opinionated priority

| Priority | Capabilities | Workshop treatment |
| --- | --- | --- |
| **Tier 1: Platform proof** | Hosted agents; Foundry IQ; tracing, evaluations and red teaming | Demonstrate before the first break, then revisit through core hands-on chapters. |
| **Tier 2: Strategic control plane** | Microsoft Agent 365; identity, data boundaries, governance and AI Gateway | Establish before the first break; revisit only where it supports the agent lifecycle. |
| **Tier 3: Architecture enablers** | Framework choice; MCP and Toolboxes; multi-model strategy | Core explanation and guided hands-on work. |
| **Tier 4: Tactical accelerators** | Memory, skills, routines, Work IQ, Fabric IQ and Web IQ | Explain in context; use only where entitlement and status are reliable. |
| **Tier 5: Forward-looking** | Microsoft 365 publishing, Autopilots, Agent Optimizer and Agent ROI | Instructor demonstration, prepared evidence or roadmap discussion only. |
| **Do not teach as strategic** | Foundry visual workflows | Mention retirement and direct participants to code-based Agent Framework workflows. |

### Why the order differs from a conventional course

A conventional course would start with models, projects and SDK setup, then reach operations and governance late in the day. That would waste the strongest manager-attention window and leave the platform differentiation until many participants have departed.

The recommended order is:

1. show the complete enterprise destination;
2. prove the destination with a working agent;
3. unpack how the agent is built and hosted;
4. add governed knowledge and tools;
5. measure, secure and operate it;
6. reassemble the system through a capstone.

The audience sees why the platform matters before learning how every part works.

## 3. Attention-aware workshop design

### Design for the expected audience curve

| Time window | Expected audience condition | Design response |
| --- | --- | --- |
| 09:00-09:10 | Some participants may arrive late. | Start with the business problem and a visual map; do not put the only critical demo in the first minutes. |
| 09:10-10:20 | Highest combined manager and technical attention. | Present and prove the complete Foundry platform thesis. |
| First break | Some managers may leave. | By 10:20, complete every strategic, commercial, governance and pilot message at least once. |
| 10:35-12:30 | Strong technical attention. | Unpack agent development, hosting, knowledge and tools through guided work. |
| 13:15-14:35 | Primarily technical audience, still strong. | Deliver the deepest operations, evaluation, security and enterprise architecture chapter. |
| Afternoon break | Some technical participants may leave. | Introduce no essential strategic proposition afterward. |
| 14:50-16:30 | Smaller, technically committed group. | Integrate existing concepts in a capstone and turn them into pilot decisions. |

### What must land before 10:20

Assume a manager attends only the first 80 minutes. They must leave understanding:

- Foundry is a platform for building, running, grounding, evaluating and governing custom agents;
- Foundry supports Microsoft Agent Framework, LangGraph and custom code without making one framework the platform proposition;
- hosted agents provide a managed runtime boundary with identity, sessions, scale and standard protocols;
- Foundry IQ provides managed, cited and permission-aware enterprise grounding;
- Toolboxes and MCP provide a governed way to connect tools without embedding every credential and integration in agent code;
- traces and evaluations turn agent quality into evidence that teams can inspect and improve;
- red teaming tests different risks from ordinary functional evaluation;
- Microsoft Agent 365 is the enterprise control plane for observing, governing and securing agents, not another builder;
- Microsoft 365 and Teams are important distribution surfaces, but publishing prerequisites and current product status must be verified;
- model choice includes Microsoft/OpenAI, Anthropic, open and partner models, with different quality, cost, hosting and contractual properties;
- enterprise readiness includes identity, data boundaries, governance and platform controls, without requiring a landing-zone deep dive;
- the recommended pilot is bounded, read-only, measurable and avoids regulated or
  consequential decision data.

### Chapter rhythm

Use a repeated **see it - work with it - connect it** pattern:

1. **See it:** Demonstrate a complete technical outcome, including capabilities that would be too slow or fragile for every participant to configure.
2. **Work with it:** Let participants make one meaningful change or decision using a pre-provisioned environment.
3. **Connect it:** Discuss architecture, status, controls, operating model and applicability to the customer.

### Recommended content balance

| Activity | Approximate share | Purpose |
| --- | ---: | --- |
| Technical explanation and demonstration | 40% | Show the complete platform and advanced experiences. |
| Guided hands-on work | 35% | Create durable understanding without allowing setup problems to dominate. |
| Architecture and adoption discussion | 25% | Connect capabilities to enterprise controls, ownership and pilot decisions. |

Hands-on work must be guided rather than open-ended. Provision projects, identities, models, knowledge bases, tools, telemetry and hosted deployments before the workshop.

## 4. Shared storyline

Use one synthetic **enterprise knowledge agent** throughout the day. The default storyline is neutral retail operations. Select the final business domain with the customer during preparation rather than promising a specific scenario in the invitation.

Participants should progressively:

1. create a simple prompt-based agent;
2. explore prepared code implemented in a supported framework;
3. deploy or invoke it as a Foundry hosted agent;
4. create and attach a Foundry IQ knowledge base;
5. observe and evaluate its behavior;
6. improve it through a guided challenge.

The exact model, framework, tool and channel integrations remain flexible until the environment and tenant capabilities are validated.

### Prepared technical components

| Component | Prepared implementation |
| --- | --- |
| Agent entry point | Simple prompt-based agent in Foundry |
| Agent framework | Prepared implementation using LangGraph, Microsoft Agent Framework or another supported framework |
| Runtime | Local development assets, prebuilt container and a known-good Foundry hosted-agent deployment |
| Knowledge | Foundry IQ knowledge base over a small synthetic document set, with optional web grounding |
| Tools | Optional read-only API, Work IQ or Toolbox/MCP demonstration after tenant validation |
| Models | One premium reasoning deployment and one lower-cost alternative |
| Quality | Small domain evaluation dataset with known passing and failing examples |
| Operations | Application Insights traces and prepared monitoring views |
| Governance | Entra identity, Foundry RBAC, Agent 365 registration and a concise platform-control overlay |
| Channel | Prepared Microsoft 365 Copilot or Teams publishing demonstration where available |

### Explicit exclusions

- No regulated, personal or production customer data.
- No consequential recommendation or autonomous action.
- No live red-team run against a tool with side effects.
- No participant dependency on preview entitlements.

### Capstone failure

Seed a realistic but scenario-neutral failure using two or three of:

- stale or conflicting knowledge;
- an unsupported or weakly cited answer;
- untrusted content returned by a source or tool;
- a lower-cost model with weaker task adherence;
- instructions that do not require sufficient evidence.

Participants use traces, citations, evaluator results and model comparison to diagnose the failure and verify an improvement.

## 5. Detailed schedule

| Time | Duration | Session |
| --- | ---: | --- |
| 09:00-09:30 | 30 min | Executive platform briefing |
| 09:30-10:20 | 50 min | Chapter 1: Foundry in action: from idea to governed agent |
| 10:20-10:35 | 15 min | Break |
| 10:35-11:35 | 60 min | Chapter 2: Build and host the agent |
| 11:35-12:30 | 55 min | Chapter 3: Ground it with enterprise knowledge |
| 12:30-13:15 | 45 min | Lunch |
| 13:15-14:35 | 80 min | Chapter 4: Measure, secure and operate it |
| 14:35-14:50 | 15 min | Break |
| 14:50-16:10 | 80 min | Chapter 5: Improve and harden the agent |
| 16:10-16:30 | 20 min | Pilot and adoption decisions |

## 6. Executive platform briefing - 09:00 to 09:30

### Goal

Present the complete strategic position and prepare the audience to understand the first end-to-end demonstration.

### 09:00-09:05 - The business problem

Ask:

1. How do we know an agent's answer is correct enough for its intended task?
2. How does it use the same permissions as the person asking?
3. Which actions may it take, and which require approval?
4. Where can an operator see what happened and why?

Frame the shift from model experiments to operational AI systems.

### 09:05-09:12 - The platform map

Show one visual:

| Layer | Examples | Message |
| --- | --- | --- |
| Models | Microsoft/OpenAI, Anthropic, Meta, Mistral, DeepSeek, xAI and open models | Select by quality, cost, latency, hosting, geography and contractual fit. |
| Agent code | Microsoft Agent Framework, LangGraph, Semantic Kernel and custom code | Foundry does not require one orchestration framework. |
| Managed runtime | Foundry hosted agents | Standardize identity, endpoints, sessions, scale and operational integration. |
| Enterprise context | Foundry IQ, Work IQ, Fabric IQ and Web IQ | Give agents relevant, permission-aware and attributable context. |
| Tools and actions | MCP, Toolboxes, OpenAPI, functions and connected services | Separate agent reasoning from credentials and controlled capabilities. |
| Quality and operations | Tracing, evaluations, red teaming and monitoring | Treat quality, safety, latency and cost as measurable release criteria. |
| Enterprise control | Entra, Agent 365, APIM, Private Link, VNet and CMK | Govern the full system, not only the model endpoint. |

Clarify that the core Foundry platform and Agent Service are generally available, while individual capabilities have separate status.

### 09:12-09:25 - Complete scenario in thirteen minutes

Show:

1. A user asks a representative knowledge-intensive question.
2. A custom agent runs as a Foundry hosted agent.
3. It retrieves a cited answer through Foundry IQ.
4. It uses one prepared tool or enterprise context source where available.
5. Compare the result from two models.
6. Open the trace and an evaluation result.
7. Show the agent in Agent 365.
8. Show the Microsoft 365/Teams integration destination.

This is a strategic walkthrough. Chapter 1 repeats it with enough detail to make the architecture credible.

### 09:25-09:30 - Enterprise value and pilot

Conclude with:

- choice of model and framework without fragmenting the operating model;
- managed identity, context, tools and evidence;
- release criteria based on successful tasks rather than tokens alone;
- a focused pilot with clear data boundaries, measurable outcomes and human accountability.

### Opening guardrails

- Do not begin with portal navigation, subscriptions or a model list.
- Do not imply that every Foundry capability is generally available.
- Do not describe Microsoft Agent 365 as an agent builder.
- Do not claim that publishing to Microsoft 365 is frictionless or universally enabled.
- Do not reduce enterprise readiness to "the resource has a private endpoint."

## 7. Chapter 1: Foundry in action: from idea to governed agent - 09:30 to 10:20

### Why it is early

This chapter must prove the complete platform proposition before managers leave. It is a fast end-to-end demonstration, not the place to explain implementation details that later chapters cover.

### Key message

Foundry's platform value appears when a custom agent can be hosted, grounded, equipped, measured and governed through one connected operating model.

### See it - 25 minutes

Follow one agent across the platform:

1. create or open the agent;
2. show it running as a Foundry hosted agent;
3. ask a question grounded through Foundry IQ;
4. show one governed tool or enterprise-context interaction;
5. compare or switch between prepared models;
6. open one trace and one evaluation result;
7. show the agent in Agent 365;
8. show the Microsoft 365 or Teams publishing destination;
9. close with a single visual covering identity, data boundaries and governance.

Keep each stop brief. The purpose is to establish the connected journey and create curiosity for the later chapters.

### Work with it - 15 minutes

Participants:

1. ask two or three prepared questions;
2. judge each answer against the prepared acceptance criteria;
3. inspect citations and one tool or context interaction;
4. identify which answer should fail, and say which criterion it fails;
5. record one risk or control question.

Judgement here is deliberately qualitative and unrecorded. Chapter 2 turns the same
instinct into a machine-checkable contract with recorded evidence, so this segment
introduces acceptance criteria without pre-empting model selection.

Use a browser or prepared notebook so this segment has almost no setup dependency.

### Connect it - 10 minutes

Close the executive loops:

- which parts are GA, preview or status-transitioning;
- why Azure resource region, model deployment type, Microsoft 365 tenant geography and third-party hosting remain separate decisions;
- why human approval remains the boundary for operational writes;
- why cost per successful task is more useful than token cost alone;
- why visual Foundry workflows are not a strategic investment because the preview experience is scheduled to retire on 1 December 2026.

## 8. Chapter 2: Build and host the agent - 10:35 to 11:35

### Key message

Use the framework that fits the team, then standardize the production boundary through hosted agents and supported protocols.

### Product position

- Foundry is the platform proposition; no single framework should dominate the chapter.
- Hosted agents can package LangGraph, Microsoft Agent Framework, Semantic Kernel and custom frameworks.
- The hosting contract is a container and protocol boundary, not a requirement to rewrite orchestration in a Microsoft-only SDK.
- Hosted deployment provides value through identity, endpoint management, sessions, scale and integrated operations.
- Responses conversations provide short-term thread context; managed Memory can add
  scoped user-profile and chat-summary continuity across separate conversations when
  the use case justifies persistent data.
- Model deployment shape is an architectural decision, not a detail. Gateway-routed
  models are treated as bring-your-own models: they cannot use the `memory_search` tool,
  and agent-level responsible AI policy is accepted but not enforced on them. Agent
  Memory and enforced agent-level guardrails both require a standard project deployment.
- Guardrails attach to the agent version through a named responsible AI policy, so the
  safety control point is independent of the framework and of the calling application,
  provided the model is a project deployment. Verify enforcement rather than assuming
  that successful attachment implies effect.

### See it - 20 minutes

1. Create a simple prompt-based agent in Foundry.
2. Contrast context inside one conversation with two managed Memory types: recall a
   synthetic user-scoped preference in a separate conversation, then contextually
   retrieve the summary of a prior synthetic stock discussion.
3. Compare the roles of Microsoft/OpenAI, Anthropic, open and partner models.
4. Show prepared agent code, preferably LangGraph or the framework most relevant to the customer.
5. Identify the Foundry SDK integration and hosting boundary.
6. Run the code locally.
7. Show the prebuilt container and hosted deployment.
8. Explain where Microsoft Agent Framework, Semantic Kernel or custom code would fit.
9. Summarize supported invocation patterns without teaching every protocol.

### Work with it - 30 minutes

Attendee guide: [`docs/guides/chapter-2-build-agent.html`](docs/guides/chapter-2-build-agent.html).

One outcome: **a working agent, a first-hand comparison of two vendors, and a control the
attendee has watched refuse a request.** The agent is deleted at the end, so what attendees
leave with is the judgement, not a saved version. The lab is portal-only. There is
nothing to install, no shell and no code, so
nobody is blocked by a laptop, a proxy or a missing runtime, and the whole room starts at
the same place.

The built content currently runs 20 minutes against a 30-minute box. Two additions are
decided and not yet built: attendee-created guardrails, and a connected specialist agent
reached over A2A. Both are proven feasible on the live environment by the platform session -
guardrail creation with the exact shipped attendee role set, and A2A delegation end to end
including the marker - and both are pending per-seat provisioning and a portal walkthrough.
The remaining ten minutes are reserved for them. Do not treat the 20-minute shape as final.

Preparation - 2 minutes. Sign in to the Foundry portal with the seat account and confirm
the project named in the breadcrumb is the right one.

Create the agent - 3 minutes. Create a prompt agent, paste the triage instructions, and
save. Saving mints a new version, which is the first observation of the chapter: the agent
is a named, versioned platform object rather than a prompt in a notebook. The guide warns
here that nothing in the configuration pane reaches the playground until it is saved, and
that this will catch everyone at least once.

Model choice - 6 minutes. You:

1. read four things to look for before sending anything;
2. send the synthetic stock-triage task to the first of two cross-vendor models and read the
   answer **before** starting a new chat, because a new chat clears it from the pane;
3. select **New chat**, switch model, send the identical task to the second, and read that;
4. check both against two known failures, then name a provisional favourite.

The four things are: available stock equals 5, shortfall equals 9, the three required
headings are present, the answer stays operational, and no claim of live-system access is
made. "Stays operational" is the one that decides the exercise: it fails on a
conditional as readily as on advice, so an answer that makes any action depend on a
commercial or product judgement fails it even without naming a substitute product. The guide
withholds the exact failing phrase until the calibration step, so attendees judge rather
than pattern-match.

There is deliberately no scorecard. An earlier version had attendees fill a five-criteria
grid per model, and it was cut for two reasons. It did not fit, and more importantly it
taught a hand-rolled imitation of measurement in the morning that Chapter 4 then contradicts
after lunch with real evaluation runs. The guide now says plainly that this is an impression
and not an evaluation, names Chapter 4 as the place a defensible model choice gets made, and
asks only for a favourite and a reason. That turns a competing lesson into a setup for one.

A new agent already starts on an admin-connected model, so an attendee who picks the model
it is already using will find Save disabled. That is correct behaviour, and the guide says
so, because a disabled Save otherwise reads as a fault.

Expect a subtle failure rather than a loud one. In the last live pass Luna met all five
criteria in 4.1s while Mistral-Large-3 gave both quantities correctly and then offered to
release reserved stock on a conditional judgement it was not entitled to make, failing only
the advice boundary. That
is a better teaching outcome than a wrong number, and the room will need to be steered to
find it.

Reading by eye replaces the scored harness this lab used previously. The harness ran
repeated trials and printed a verdict, which is stronger evidence, but it required a
working Python environment on every attendee machine and it moved the judgement into the
tool. In the portal you read the answer that fails and find the wrong number yourself,
which is the skill that transfers. The harness survives as a teacher demonstration, where
trial statistics play well on a projector.

Insist on **New chat** before the second model. Switching model inside one
conversation lets the second model read the first model's answer, which turns a comparison
into an agreement.

Governance - 6 minutes. This half runs on the project-local `gpt-5.2` deployment, and the
reason is the point of the exercise. You:

1. predict, before running anything, which of the sentinel and the control will be stopped;
2. set the model to `gpt-5.2`, save, and establish a baseline by sending the sentinel with
   no guardrail attached, so that a working control stays distinguishable from a model that
   was never going to answer;
3. assign `foundryws-guardrails-strict` through Manage guardrail, without stopping to read the
   policy table in the dialog;
4. save, having first seen the warning that an assigned guardrail is not yet an applied one;
5. send both prompts again and confirm the sentinel is refused while the control still
   answers.

Reading the guardrail panel and comparing the policies is an optional extension rather than a
core step, and it is the whole of the governance reading load. The extension covers both
halves: that `Microsoft.DefaultV2` already controls jailbreak and protected materials while
leaving indirect prompt injections, sensitive data leakage and task drift as risks without
controls; and that the delta to the prepared policy is the same four content-safety categories
tightened to highest blocking plus the custom blocklist the sentinel trips, which the portal
counts as a fifth content-safety entry. A threshold moved and one list added is what real
policy work looks like, which makes it a strong "Connect it" talking point. It is not a strong
use of an attendee's clock: three review rounds named it the first thing to cut, so it is now
cut by default and available to anyone who finishes early.

The refusal carries an explicit notice attributing the block to the asset's Foundry
guardrail. That attribution separates three things which look identical in a chat window: a
policy block, a model declining, and a platform fault. It is better evidence for a live room
than the HTTP 400 payload the harness used to print, because nobody has to read JSON to
believe it.

The trace observation belongs to the teacher demonstration and the architecture close
rather than to the attendee path. A blocked run appears as `ResultCode="500"`,
`Success=false`, `error.type="server_error"` — the same shape a genuine platform fault
would take, with no policy identifier, no `content_filter` marker and no blocklist name
anywhere in the span. An operator watching only a dashboard would mis-triage a working
safety control as an outage. The portal lab no longer visits Application Insights: the
in-chat attribution is stronger evidence, and the trace ingestion delay of roughly fifteen
seconds does not fit the timebox.

The finding moved to the teacher demonstration. The gateway-enforcement gap - attach a
guardrail, switch only the model to a gateway-routed one, and watch the sentinel be answered
- was the lab's strongest moment and is now a 90-second teacher segment documented in
`teacher/demos/build-host-agent/OPERATOR.md`. Three reasons. It is a second-order lesson that
lands badly as somebody's first governance experience; it is a dated preview defect that may
be fixed before delivery, and twenty people discovering a defect under time pressure is worse
than one person showing it deliberately; and removing it is what made room for attendee-created
guardrails and the connected-agent beat. The operator note carries the documentation link, the
verified-preview-gap framing, the honest evidence scope, and an instruction to re-test before
every delivery and drop the segment if it has been fixed.

The sentinel is a deterministic teaching proxy chosen so the result is identical for
everyone in the room. Say so explicitly. The guide names it in the core path, before the
prediction step, so that attendees predict against a known custom blocklist term rather than
guessing what a model might find objectionable. Do not present it as general personal-data
detection: personal-data filtering is not available in this policy surface at all, and
synthetic personal data passes unfiltered on both paths.

Decision and room synthesis - 3 minutes.

1. One minute: name the model you would take forward and the one limitation that would stop
   you shipping it. Cost, geography and contractual fit are untested limits of that decision,
   not lab work.
2. One minute: two show-of-hands questions only. Which model would you take forward, and did
   your models hold the advice boundary. The second question is the one that reveals the
   quiet failure across the room.
3. One minute: two or three volunteered limitations, then delete the agent, which requires
   retyping its name exactly. Deletion comes last so nobody destroys their evidence before
   reporting it.

Fill a prepared three-column board, one column per prepared model, from those hands. The board
is what makes vendor breadth visible; comparing with the person next to you does not, because
adjacent pairs overlap and two people cannot establish a room-level result. Aim to reveal one
defensible pattern, not to discuss every model.

The prepared models are `gpt-5.6-luna`, `gpt-5.6-terra` and `Mistral-Large-3`. A fourth,
`Kimi-K2.6`, is deployed and reachable but is deliberately excluded from the lab: on the
scored task at room concurrency only 2 of 30 calls completed, the rest exhausting the output
budget without finishing. It belongs in "Connect it" as evidence, not in someone's hands as
a broken exercise. The guide names it and tells attendees to ignore it, because it is
visible in the model selector and would otherwise be chosen.

Buffer. The built content runs 20 minutes against a 30-minute box, so there is no cut line
at present and the guide carries none. When attendee-created guardrails and the connected
specialist agent land, re-budget the whole lab and reinstate a cut with a trigger placed at
the step it applies to, not after it.

Optional extension, untimed and after the workshop. Switch the agent to the
project-local model deployment and attach the Memory store, discovering first hand that
`memory_search` is rejected on gateway-routed models.

Hosted deployment stays a teacher demonstration. Attendee environments have no
hosted-agent capability host, and parallel preview provisioning is too slow and too
failure-prone for a live room. The hosted boundary lands through the demonstration and
the architecture close, not through an attendee step.

### Connect it - 10 minutes

Close on three decisions the lab evidence has just made concrete:

1. **Framework code versus managed hosting.** Where the boundary sits, what hosting
   provides through identity, endpoint management, sessions, scale and integrated
   operations, and when an existing application runtime is the better answer. Covers ACR,
   RBAC, quota and regional prerequisites, session state and lifecycle, and dedicated
   agent identity as the cost of that boundary.
2. **Model deployment shape versus available platform capability.** Gateway-routed models
   are bring-your-own models. They cannot use `memory_search`, and an agent-level
   responsible AI policy attaches to them without error but does not take effect. Agent
   Memory and enforced agent-level guardrails both require a standard project deployment.
   The deployment shape chosen early therefore constrains what the platform can do for the
   agent later, and a silently ineffective control is more dangerous than a refused one.
   Name where enforcement would have to live for gateway traffic instead. Name what the
   lab did not test and what would still have to be established before a real selection:
   cost per successful task, deployment geography and data residency, contractual and
   licensing fit, and output variability across a far larger sample than two trials.
3. **Agent version plus policy evidence versus release.** A named policy on a versioned
   agent is a promotion and rollback artefact, not a prompt instruction, and it is
   auditable independently of the calling application.

Hand off: the agent now has behaviour and controls, but its knowledge is still whatever
the model happens to hold. Chapter 3 adds attributable, authorized enterprise knowledge,
and contrasts Memory continuity with Foundry IQ grounding.

### Models, workflows and routines position

- Present Microsoft/OpenAI, Anthropic, open and partner models as a portfolio rather than a preferred winner.
- Explain local development, model testing and the value of a common hosted boundary.

- Use Microsoft Agent Framework workflows or code-based orchestration as the strategic approach.
- Mention visual Foundry workflows only to explain their preview status and scheduled retirement.
- Position routines as lightweight preview scheduling for one prompt or hosted agent, such as a daily stock exception summary.
- Do not describe routines as a full multi-step workflow engine.

## 9. Chapter 3: Ground it with enterprise knowledge - 11:35 to 12:30

### Key message

An enterprise agent needs relevant, attributable and authorized knowledge, not only a larger model or longer prompt.

### See it - 20 minutes

1. Show the Foundry IQ knowledge-base experience and supported source patterns.
2. Ingest a small prepared document set.
3. Add web grounding where the feature and entitlement have been prevalidated.
4. Compare an ungrounded answer with cited, grounded output.
5. Attach the knowledge base to the existing hosted agent without changing its code.
6. Demonstrate Work IQ or one governed tool as an extension of the same pattern.

### Work with it - 25 minutes

Participants:

1. create or update a knowledge base from prepared documents;
2. test retrieval and inspect citations;
3. add a prepared web source or use a prebuilt equivalent;
4. attach the knowledge base to their agent;
5. compare grounded and ungrounded behavior;
6. identify one unsupported or conflicting answer.

Indexing must be tested at workshop scale. Keep a prebuilt knowledge base available so participants can continue immediately if ingestion or web grounding is delayed.

### Connect it - 10 minutes

#### Microsoft IQ distinctions

| Capability | Position |
| --- | --- |
| **Foundry IQ** | Managed knowledge bases and agentic retrieval for custom applications and agents. This is the core workshop hands-on path. |
| **Work IQ** | Workplace context from Microsoft 365 signals such as mail, meetings, Teams, files and people, under delegated identity. Demonstrate in the playground only if licensing, consent and the delegated identity path are prevalidated. |
| **Fabric IQ** | Business and semantic context from Fabric, OneLake, semantic models, ontologies and data agents. Position for governed analytical and operational context. |
| **Web IQ** | Fresh web grounding based on Bing infrastructure. Treat as entitlement-dependent and demonstrate only if prevalidated. |

#### Memory position

Chapter 2 demonstrates scoped cross-conversation recall. Use Chapter 3 to connect that
experience to the deeper governance and knowledge architecture. Managed Memory is
strategically relevant but currently preview. Explain:

- user-profile, chat-summary and procedural memory;
- why managed lifecycle, TTL, remember and forget operations matter;
- prompt-injection and memory-poisoning risks;
- why regulated or sensitive personal data classes should not be stored in a preview memory service.

Chapter 2 demonstrates GREEN-742 as the synthetic profile preference and STOCK-318 as
the synthetic summarized discussion. Do not repeat the live demonstration here; use
the Connect it discussion for governance and adoption depth.

#### Skills position

Skills are useful versioned behavioral packages, but remain preview and have private-networking limitations. Show the concept or a prepared skill, not participant administration.

#### Tools and AI Gateway position

Keep tools secondary to Foundry IQ in this chapter:

- demonstrate Work IQ or one read-only MCP/Toolbox integration;
- explain identity, credential isolation and approval boundaries;
- mention AI Gateway as the policy, quota and logging layer for model and API access;
- do not provision APIM or build a private MCP integration during the workshop.

## 10. Chapter 4: Measure, secure and operate it - 13:15 to 14:35

### Why this chapter is central

This is the deepest technical platform chapter. It must finish before the afternoon break because observability, evaluation and red teaming are essential to the Foundry proposition.

### Key message

Agent quality is not a demo impression. It is a release and operating discipline based on traces, task-specific evaluations, adversarial testing and measurable outcomes.

### See it - 25 minutes

1. Open an OpenTelemetry trace with model, retrieval and tool spans.
2. Follow latency, tokens, cost, retries and tool arguments.
3. Use trace replay where available, clearly labelling preview status.
4. Show a domain evaluation dataset.
5. Compare groundedness, task adherence, tool-call correctness, latency and cost across two models.
6. Show a prepared red-team result for leakage, prohibited actions or task adherence.
7. Show monitoring and alert concepts.

### Work with it - 40 minutes

Participants:

1. run the small evaluation dataset;
2. identify the lowest-performing test case;
3. inspect the corresponding trace;
4. classify the failure as retrieval, instructions, model, tool or policy;
5. change one instruction, tool description or model selection;
6. rerun the evaluation;
7. compare quality, latency, token use and cost;
8. decide whether the result meets the release threshold.

### Connect it - 15 minutes

#### Evaluation and red-team distinction

- Functional and quality evaluations test expected behavior against task-specific criteria.
- Red teaming searches adversarially for unsafe, disallowed or exploitable behavior.
- Both are needed; neither replaces deterministic authorization or human approval.
- Live red teaming must use mocked or side-effect-free tools.

#### Concise enterprise overlay

Reserve no more than five minutes for one production architecture visual:

- Entra identity and scoped permissions;
- model and data geography;
- public versus private connectivity;
- supported encryption boundaries;
- Agent 365 governance and telemetry;
- AI Gateway policy, quotas and logging.

The purpose is to show that these controls exist and must be designed together. Do not turn the chapter into a landing-zone, VNet or APIM implementation lesson.

## 11. Chapter 5: Improve and harden the agent - 14:50 to 16:10

### Purpose

Attendance and energy may fall after the afternoon break. This chapter deepens concepts already introduced rather than adding a new strategic proposition. It combines an evaluation-driven improvement lab with a focused demonstration of enterprise hardening.

### Improvement setup - 10 minutes

Present a scenario-neutral failure using two or three prepared symptoms:

1. retrieval returns stale or conflicting evidence;
2. the answer lacks adequate citation support;
3. a source or tool contains untrusted text;
4. one model has lower task adherence;
5. the agent still produces a confident answer.

Show the failing evaluation and trace without revealing the diagnosis.

### Hands-on improvement loop - 35 minutes

Teams:

1. inspect citations and source freshness;
2. identify the untrusted tool content;
3. compare model behavior;
4. choose one or two bounded changes;
5. rerun relevant evaluations;
6. verify the improvement against the acceptance criteria.

Every participant or pair should be able to complete the improvement loop using prepared assets and step-by-step checkpoints.

### Enterprise hardening demonstration - 20 minutes

Use the improved agent to show how a production design adds:

- dedicated agent identity and scoped access;
- controlled knowledge and tool permissions;
- approval boundaries for consequential actions;
- AI Gateway policy, quotas and logging;
- private connectivity and explicit data-flow boundaries;
- supported encryption and regional deployment choices;
- Agent 365 inventory, governance and security integration.

This is an architecture-led demonstration, not a landing-zone implementation lesson.

### Optional extensions and technical clinic - 15 minutes

Faster groups can:

- compare a second model;
- add another evaluation case;
- test an adversarial variation;
- improve retrieval or source selection;
- inspect the hosted trace in more detail;
- discuss an optional tool or channel integration.

This segment is also the primary schedule-recovery buffer. Remove extensions before compressing the hands-on improvement loop.

During the clinic, draw the final system together:

- model and routing decision;
- framework and hosted boundary;
- identity and permissions;
- knowledge source and ACL behavior;
- tool trust and approval;
- trace and evaluation evidence;
- network and geography;
- Agent 365 ownership;
- release, monitoring and incident boundaries.

As time permits, ask each group to name:

1. one control that should be centralized;
2. one decision that should remain with the product team;
3. one metric that would stop a release;
4. one condition that would trigger human escalation.

## 12. Closing: Pilot and adoption decisions - 16:10 to 16:30

Use this as a concise wrap-up rather than another working chapter:

1. recap the Foundry platform loop;
2. identify one or two candidate customer use cases;
3. name the most important readiness question;
4. suggest practical next steps and owners;
5. retain time for final questions.

If earlier chapters overrun, this segment can be shortened while preserving a five-minute conclusion. Detailed pilot scoring can happen in a follow-up session.

## 13. Capability treatment matrix

| Capability | Explain | Demo | Hands-on |
| --- | ---: | ---: | ---: |
| Platform thesis and status | Strong | Executive walkthrough | No |
| Framework choice | Moderate | Show prepared code | Inspect one prepared implementation |
| Hosted agents | Strong | **Core** | Scripted prebuilt deployment or invoke fallback |
| Prompt-based agents | Moderate | Yes | **Core** |
| Multi-model strategy and Model Router | Strong | Before 10:20 | Compare prepared deployments |
| Claude procurement through Foundry | Strong | Optional prepared endpoint | No live Marketplace setup |
| Open models | Strong | Include in model map | Optional prepared endpoint |
| Foundry IQ | Strong | Before 10:20 | **Core** |
| Work IQ, Fabric IQ and Web IQ | Explain distinctions | Work IQ if prevalidated | No |
| MCP and Toolboxes | Moderate | Optional read-only example | No core dependency |
| Skills | Brief, preview | Optional | No |
| Memory | Benefits, risks and limits | Synthetic only | No |
| Tracing and evaluations | Strong | Before 10:20 | **Core** |
| Red teaming | Strong | Prepared result | No live agentic scan |
| Monitoring | Strong | Yes | Inspect prepared view |
| Agent 365 | Strong | Before 10:20 | Architecture discussion |
| Microsoft 365 and Teams publishing | Prerequisites and status | Instructor only | No |
| Autopilots and Scout | Brief, forward-looking | Screenshot or video | No |
| AI Gateway and APIM | Brief | Optional prepared policy | No provisioning |
| Identity, data boundaries and enterprise controls | Moderate | Concise architecture overlay | No implementation lab |
| Fine-tuning | Decision criteria | No | No |
| Agent Optimizer and Agent ROI | Preview roadmap | Recorded if available | No |
| Visual Foundry workflows | Retirement only | No | **Never** |
| Routines | Brief, preview | Optional | No |

## 14. Models and optimization position

### Multi-model strategy

Present a portfolio rather than a model winner:

- a high-quality reasoning model for difficult or high-value tasks;
- a cost-oriented model for high-volume work;
- an open or independently hosted option where control or portability matters;
- evaluation-based routing criteria for quality, latency and cost.

Foundry's value is governed choice and lifecycle management, not simply access to many catalogue entries.

### Anthropic position

Claude availability through Foundry can reduce procurement friction, but explain accurately:

- procurement uses an Azure Marketplace subscription;
- provider and hosting variants have their own terms and geography;
- documented availability and supported billing models must be checked;
- purchasing through Azure does not automatically mean all processing remains in the selected European Azure region.

Preapprove Marketplace terms and quota if Claude is part of a live demonstration.

### Open-model position

Include at least one open or partner model in the architecture map. It demonstrates:

- task-specific model choice;
- portability and deployment control;
- the option to optimize high-volume workloads;
- why common evaluation and gateway controls are valuable.

### Fine-tuning and hill climbing

Position improvement as a measured ladder:

1. improve instructions and context;
2. improve retrieval and tool descriptions;
3. select or route to a better-fit model;
4. use prompt caching or architecture changes;
5. fine-tune only when repeated evidence justifies the added lifecycle and cost;
6. evaluate the complete agent again after every material change.

Fine-tuning is an explanation topic, not a same-day lab. Agent Optimizer and Agent ROI should be labelled according to their current preview availability and shown only through prepared evidence.

## 15. Microsoft Agent 365, publishing and Autopilots

### Microsoft Agent 365

Position Agent 365 as the cross-platform enterprise control plane to:

- inventory agents;
- assign and govern agent identities;
- visualize relationships and activity;
- apply security and compliance capabilities;
- support agents built on Microsoft and third-party platforms.

Foundry builds and operates the custom agent. Agent 365 governs the broader enterprise agent estate.

### Microsoft 365 Copilot and Teams publishing

Strategically, publishing is important because it brings custom agents into an employee channel with identity and governance. Operationally, it is a risky lab dependency because it may require:

- Azure Bot Service permissions and resources;
- Microsoft 365 tenant and application administration;
- organization approval;
- public-network or REST-path considerations;
- licensing and feature availability.

Use an instructor-only, prevalidated demonstration. The public documentation changed rapidly in July 2026 and publishing was not clearly represented in the central GA matrix. Describe it as available but status-transitioning and revalidate immediately before delivery.

### Autopilots

Explain:

- Autopilots are an emerging category of always-on agents with governed identity;
- Microsoft Scout is the first Microsoft example;
- the concept is strategically useful for understanding proactive agents;
- current preview, administrative and licensing requirements make it unsuitable for participant hands-on work.

Do not make Autopilots central to the workshop's production recommendation.

## 16. Environment preparation

Complete before the workshop:

- Foundry project in the selected region;
- two prevalidated model deployments;
- Marketplace acceptance and quota if Claude is used;
- development environment for the selected framework;
- prepared local project and known-good dependencies;
- ACR and hosted-agent deployment;
- participant RBAC and Entra identities;
- Foundry IQ knowledge base and synthetic content;
- optional read-only API, Work IQ or Toolbox/MCP integration;
- Application Insights and prepared traces;
- evaluation dataset and baseline results;
- prepared red-team report;
- APIM instance, policy and metrics if demonstrated;
- Agent 365 registration and views;
- prevalidated Microsoft 365/Teams channel demonstration;
- fallback presenter account and screenshots or completed runs.

### Participant preflight

Automate checks for:

- browser access and tenant authentication;
- repository or sample access;
- Python or .NET runtime;
- package restore;
- local agent invocation;
- access to the prepared model deployments;
- access to Foundry IQ;
- hosted-agent invocation;
- telemetry visibility.

Run the preflight with the customer several business days before delivery.

### Chapter 2 attendee access: open, and blocking for that lab

Two things about attendee access to the Chapter 2 portal lab are unresolved, and both sit
with the platform environment rather than with this repository.

The seat attendee originally held only Contributor on their own resource group, which carries
no Foundry data-plane permission. Each seat user now also holds `Foundry User`
(`53ca6127-db72-4b80-b1b0-d745d6d5456d`), scoped to that seat's own Foundry account, whose
`dataActions` are `Microsoft.CognitiveServices/*`. On role definitions that covers agent
create, run and delete and agent-level guardrail assignment. It has not been observed. Note
that `Foundry Account Owner`, which Microsoft's guardrails documentation names, carries no
`dataActions` at all and would not on its own be sufficient.

Everything documented in the Chapter 2 guide was verified in the live portal by the workshop
operator, a Global Administrator. No portal affordance in that lab is yet proven at attendee
privilege. If a run as a seat user finds either the **Manage guardrail** menu or the model
selector's admin-connected and Deployments groups missing, the lab needs changing, not just
retesting.

The attendee sign-in path itself is also unproven. Temporary Access Pass issuance needs
tenant-admin Graph consent that has not been granted, so no seat has completed a real
sign-in. This gates the whole day, not only Chapter 2.

Both must close before delivery. Neither blocks further content work.

## 17. Demonstration reliability and fallbacks

| Scenario | Primary demonstration | Prepared fallback |
| --- | --- | --- |
| Hosted-agent deployment is slow | Invoke live prepared endpoint | Use a completed invocation and inspect deployment configuration and trace. |
| ACR or RBAC fails | Instructor shows container deployment | Run the framework locally and invoke the known-good hosted endpoint. |
| Model quota or endpoint is unavailable | Compare two live deployments | Use saved evaluation results and responses from the same dataset. |
| Claude Marketplace is not approved | Live Claude comparison | Use another premium model and explain the procurement path with prepared evidence. |
| Foundry IQ indexing is delayed | Update a source and retrieve | Query the prebuilt index and inspect prior source-update evidence. |
| Toolbox or private MCP access fails | Invoke live read-only tool | Inspect a completed trace with arguments, identity and response. |
| Evaluation run is delayed | Run the dataset live | Open a completed run and improve one case locally. |
| Red teaming is unavailable | Show controlled live configuration | Use a prepared report and trace from the synthetic environment. |
| Agent 365 access is unavailable | Open the live registry | Use current screenshots and an architecture/data-boundary walkthrough. |
| Microsoft 365 publishing is blocked | Invoke agent in Teams or Copilot | Use a prepared recording and inspect the package, identity and approval flow. |
| Participant setup is incomplete | Individual hands-on | Pair participants and use browser/notebook tasks. |

Fallbacks must preserve the technical insight rather than replay a marketing video.

## 18. Facilitation and positioning

### Keep six artifacts visible

1. **Agent code:** What behavior and framework are we deploying?
2. **Knowledge evidence:** Which sources support the answer?
3. **Tool call:** What capability was requested under which identity?
4. **Trace:** What happened across retrieval, model and tool steps?
5. **Evaluation:** Does the result meet the release criteria?
6. **Governance record:** Who owns and controls the deployed agent?

### Say

- "Foundry standardizes the production boundary without requiring one agent framework."
- "A model answer becomes trustworthy through evidence, evaluation and bounded authority."
- "Foundry IQ provides managed enterprise grounding; it is not memory."
- "Agent 365 governs the agent estate; Foundry builds and operates custom agents."
- "Choose models using task-specific quality, latency, cost, hosting and geography evidence."
- "Private networking is one control in a complete data-flow design."
- "Measure cost per successful task, not cost per token in isolation."

### Do not say

- "Everything in the Foundry portal is GA."
- "Foundry requires Microsoft Agent Framework."
- "Foundry IQ, Work IQ, Fabric IQ and Web IQ are interchangeable."
- "Memory is safe for sensitive personal profiles by default."
- "Purchasing Claude through Azure guarantees Azure-only European processing."
- "A private endpoint makes the entire solution private."
- "Agent 365 is where agents are built."
- "Microsoft 365 publishing or Autopilots will work in every tenant."
- "Red teaming proves an agent is safe."
- "Fine-tuning is the first step to better quality."

### Keep AI claims credible

- Show a failure and correction, not only a successful response.
- Inspect tool arguments and citations.
- Compare models on the same dataset.
- Label GA, preview and roadmap items.
- Explain identity and approval before demonstrating actions.
- Separate functional evaluation, adversarial testing and deterministic authorization.

## 19. Decisions to confirm before delivery

- Participant count and role mix.
- Exact manager departure expectation.
- Preferred workshop language and code language.
- Current Foundry tenant and project availability.
- Allowed Azure region and data-residency constraints.
- Whether private networking is required for the workshop environment.
- Model entitlements, quota and Anthropic Marketplace approval.
- Availability of Agent 365 and qualifying Microsoft 365 licenses.
- Whether Microsoft 365/Teams publishing is permitted.
- Availability of Work IQ, Fabric IQ and Web IQ.
- Whether preview features may be demonstrated.
- Customer candidate use cases and prohibited data classes.
- Whether the hands-on environment can be provisioned centrally.
- Named technical and business owners for a follow-up pilot.

## 20. Current product-status guardrails

Research cutoff: **16 July 2026**. Revalidate status shortly before delivery.

| Capability | Current workshop assumption |
| --- | --- |
| New Microsoft Foundry portal and Foundry projects | GA core platform |
| Foundry Agent Service | GA, with feature-specific exceptions |
| Hosted agents | Core capability; recheck tracing and private-network details |
| Microsoft Agent Framework | Stable preferred pro-code framework |
| Visual Foundry workflows | Preview; scheduled to retire 1 December 2026 |
| Routines | Preview |
| Tool catalogue and core tool framework | GA, with per-tool status differences |
| Toolboxes | Treat as core after tenant verification |
| Skills | Preview |
| Foundry IQ | Knowledge-base API GA; portal and source combinations vary |
| Memory | Preview; no VNet integration |
| Evaluations and red teaming | Core capabilities, with feature and region exceptions |
| Monitoring dashboard and Trace Replay | Preview |
| Microsoft 365/Teams publishing | Available but status-transitioning; verify tenant and current documentation |
| Microsoft Agent 365 | GA enterprise control plane |
| Autopilots and Microsoft Scout | Preview or restricted frontier experiences |
| Claude through Foundry | Marketplace-backed; model status, hosting and geography vary |
| Fine-tuning | Core GA path for supported models and regions |
| Agent Optimizer and Agent ROI | Preview or restricted availability |

## 21. Public research references

### Canonical status and architecture

- [Microsoft Foundry general availability overview](https://learn.microsoft.com/en-us/azure/foundry/concepts/general-availability)
- [What is Microsoft Foundry?](https://learn.microsoft.com/en-us/azure/foundry/what-is-foundry)
- [Foundry regional support](https://learn.microsoft.com/en-us/azure/foundry/reference/region-support)
- [Foundry deployment architecture and geography](https://learn.microsoft.com/en-us/azure/foundry/concepts/architecture)

### Agents, frameworks and orchestration

- [Foundry hosted agents](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/hosted-agents)
- [Hosted-agent virtual networks](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/virtual-networks)
- [Foundry workflows and retirement notice](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/workflow)
- [Foundry routines](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/routines)
- [Build and run agents at scale with Microsoft Foundry at Build 2026](https://devblogs.microsoft.com/foundry/agent-service-build2026/)

### Knowledge, memory and tools

- [What is Foundry IQ?](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq)
- [Foundry managed memory](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-memory)
- [Foundry tool catalogue](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/tool-catalog)
- [Foundry Toolboxes](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/toolbox)
- [Foundry skills](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/skills)
- [Work IQ API overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/api-overview)
- [Work IQ connector for Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/work-iq)
- [Fabric IQ overview](https://learn.microsoft.com/en-us/fabric/iq/overview)
- [Fabric IQ connector for Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/fabric-iq)
- [Microsoft Web IQ announcement](https://blogs.bing.com/search/June-2026/Announcing-Microsoft-Web-IQ)

### Quality, observability and safety

- [Foundry observability](https://learn.microsoft.com/en-us/azure/foundry/concepts/observability)
- [Agent tracing](https://learn.microsoft.com/en-us/azure/foundry/observability/concepts/trace-agent-concept)
- [Trace Replay](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-replay)
- [AI Red Teaming Agent](https://learn.microsoft.com/en-us/azure/foundry/concepts/ai-red-teaming-agent)
- [Agent Optimizer](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-optimizer-overview)

### Models

- [Foundry Model Router](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router)
- [Fine-tuning in Foundry](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/fine-tuning)
- [Claude models billing through Azure Marketplace](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/claude-models-billing)

### Agent 365 and channels

- [Microsoft Agent 365 overview](https://learn.microsoft.com/en-us/microsoft-agent-365/overview)
- [Microsoft Agent 365 integration with Foundry](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/agent-365-integration)
- [Publish Foundry agents to Microsoft 365 Copilot and Teams](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/publish-copilot)
- [Publish a Foundry agent as an Agent 365 Autopilot](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/agent-365)
- [Microsoft Scout announcement](https://www.microsoft.com/microsoft-365/blog/2026/06/02/introducing-microsoft-scout-your-always-on-personal-agent/)

### Enterprise controls

- [Foundry RBAC](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry)
- [Configure Private Link for Foundry](https://learn.microsoft.com/en-us/azure/foundry/how-to/configure-private-link)
- [Customer-managed keys for Foundry](https://learn.microsoft.com/en-us/azure/foundry/concepts/encryption-keys-portal)
- [Enable Azure API Management AI Gateway](https://learn.microsoft.com/en-us/azure/foundry/configuration/enable-ai-api-management-gateway-portal)

### Build 2026

- [Microsoft Build 2026 announcement index](https://news.microsoft.com/build-2026/)

Snapshot the critical status pages and recheck them immediately before the workshop. Hosted agents, publishing, Toolboxes and several Build 2026 capabilities are changing quickly.
