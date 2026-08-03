# Chapter 2 teacher demonstration: build and host the agent

This demonstration takes a synthetic stock-exception assistant from a declarative
prompt agent to local LangGraph code and a managed Foundry hosted-agent endpoint.

## Supported delivery environment

- Windows 11 with PowerShell 7
- Python 3.13 managed through `uv`
- Azure CLI authenticated to the lab tenant and subscription
- The deployed `labtest` teacher platform from `D:\lab-env-setup`

The demo reads the teacher project endpoint, connected model reference, and
observability resource IDs from `labctl status`. It does not duplicate platform
infrastructure or read Terraform state directly.

## Prepare before the session

From the repository root:

```powershell
uv sync
.\teacher\demos\build-host-agent\scripts\demo.ps1 preflight
.\teacher\demos\build-host-agent\scripts\demo.ps1 prepare
.\teacher\demos\build-host-agent\scripts\demo.ps1 verify --trace --timeout 300
```

Expected preparation state:

- `foundryws-chapter2-prompt` has a reusable prompt-agent version.
- `foundryws-chapter2-memory` has a reusable prompt-agent version and an owned Memory store.
- `foundryws-chapter2-langgraph` has an active immutable hosted version.
- The hosted endpoint routes 100 percent of traffic to that version.
- The first conversation turn returns `PRIORITY`, `NEXT ACTION`, and `ESCALATE WHEN`;
  the follow-up demonstrates short-term context in one Responses conversation.
- A separate conversation recalls the synthetic GREEN-742 profile preference, and a
  contextual SDK search retrieves the summarized STOCK-318 discussion. The isolated
  scope can see neither.
- At least one trace record reaches the dedicated teacher Log Analytics workspace.

Re-running `prepare` reuses versions whose content fingerprint matches the local
source. Use `--new-version` only when intentionally demonstrating version creation.

## Twenty-minute teacher flow

### 0:00-3:00 — Establish the prompt baseline

Run:

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 prompt
```

Show the `foundryws-chapter2-prompt` agent in Foundry. Explain that its behavior is a
declarative combination of model and instructions. Point out the connected model
name, `shared-ai-gateway/gpt-5.6-luna`: the project consumes a governed shared
deployment rather than owning model capacity.

Expected result: two related turns demonstrate short-term context retained inside one
Foundry-managed Responses conversation. This is not the optional persistent Memory
feature, which stores selected information across separate conversations and requires
its own model and storage configuration.

### 3:00-7:00 — Recall across conversations

Run:

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 memory --timeout 120
```

Show that the script teaches a synthetic GREEN-742 queue preference, discusses and
resolves synthetic incident STOCK-318, then closes that conversation. Contrast the
two stored memory types and their documented retrieval strategies:

- `user_profile` retains a stable preference; the prompt agent retrieves it in a new
  conversation;
- `chat_summary` distills a prior topic; the script retrieves it contextually by
  calling `search_memories` with the latest user message.

Contrast both with the baseline: a Responses conversation retains short-term thread
context, while Foundry Memory extracts selected information into a persistent store.
Point out that the isolated-user result sees neither memory because
`x-memory-user-id` scopes prompt-agent operations within the store, while direct
Memory API calls pass the same scope explicitly.

Expected result: `memory_kinds` contains `user_profile` and `chat_summary`,
`profile_recalled_in_new_conversation` contains `GREEN-742`,
`chat_summary_recalled_contextually` contains `STOCK-318` and the no-transfer
decision, both isolated API result counts are zero, and the isolated agent response
contains neither identifier.

State the safety boundary before moving on:

- Memory is a preview capability; reassess its limits before production adoption.
- GREEN-742 and STOCK-318 are synthetic because regulated personal data, sensitive
  profile attributes, credentials, and live customer data do not belong
  in this demo store.
- Scope isolation is necessary but not sufficient: validate inputs and adversarially
  test for prompt injection and memory corruption.

### 7:00-10:00 — Move triage behavior into LangGraph

Memory remains attached to the prompt agent in this demonstration. The next step moves
the stock-triage behavior, not the Memory integration, into LangGraph so the audience
can see the separate framework and hosting boundary.

Open `agent\main.py`. Focus on four boundaries:

1. `calculate_shortfall` is deterministic application code.
2. `build_graph` compiles the LangGraph orchestration with `create_agent`.
3. `ChatOpenAI` calls the model through the Foundry project endpoint.
4. `ResponsesHostServer` supplies the supported hosting protocol.

Run the same source locally:

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 local
```

Expected result: the local `/responses` endpoint triages the synthetic exception and
uses the deterministic shortfall tool. No sensitive data or live system is involved.

### 10:00-14:00 — Create an immutable hosted version

The known-good version is already active. To demonstrate a fresh deployment:

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 deploy --new-version --timeout 900
```

Explain while provisioning continues:

- source upload removes Docker and ACR work from this learning path;
- Foundry resolves dependencies and creates an immutable version;
- the runtime receives a dedicated agent identity and endpoint;
- Responses protocol version 2.0 is the portable application boundary;
- CPU and memory describe one isolated session sandbox.

Expected result: status reaches `active`, then endpoint routing moves to the new
version. If provisioning exceeds the timebox, stop waiting and use the already active
known-good version with `showcase`.

### 14:00-17:00 — Invoke the managed endpoint

Run:

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 showcase
```

Compare the prompt and hosted outputs. Emphasize that Foundry standardizes identity,
endpoint, conversations, sessions, scaling, versioning, and telemetry without forcing
the orchestration code into a Microsoft-only framework.

### 17:00-20:00 — Connect to production concerns

In Foundry, show the active version, endpoint, identity, and Application Insights
connection. Run:

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 verify --trace --timeout 300
```

Connect the visible trace to later evaluation and operating chapters. Close with the
decision boundary: choose hosted agents when managed sessions, identity, lifecycle,
and observability are valuable; keep an existing runtime when those controls already
exist and migration adds no operational value.

### Where the control actually applies — 90 seconds, immediately after the attendee lab

Run this on the teacher project once attendees have watched their own guardrail block
the sentinel. It is deliberately not in the attendee guide: it is a second-order lesson
that lands badly as somebody's first governance experience, and it reads far better as
one thing you show the room than as twenty people discovering it under time pressure.

Have an agent already carrying an assigned, saved guardrail that blocks the sentinel on
the project-local `gpt-5.2` deployment. Then, in front of the room:

1. Change only the **Model**, to an admin-connected gateway model. Change nothing else.
2. Select **Save**, then **New chat**.
3. Scroll the configuration pane to the Guardrail panel and show it still names the
   guardrail. Say out loud that nothing about the control has been touched.
4. Send the sentinel prompt again.

The sentinel is answered. Nothing warns you: the guardrail did not detach, no error
appears, and the configuration reads exactly as it did when the control was working.

Talking points, in this order:

- Microsoft documents that a guardrail assigned to an agent fully overrides the guardrail
  on the underlying model, and states no exception for gateway-routed models
  ([guardrails for agents versus models](https://learn.microsoft.com/azure/foundry/guardrails/guardrails-overview#guardrails-for-agents-vs-models)).
  What the room just saw contradicts that.
- Call it a **verified preview gap, not designed behaviour**. A defect gets fixed and
  should not drive anyone's architecture; a designed limitation would. Observed 30 July
  2026 on this configuration.
- The lesson that outlives the defect: **verify where enforcement actually happens rather
  than trusting that attaching a control was enough.** A control that silently accepts
  configuration and then does not apply is more dangerous than one that refuses outright,
  because it buys confidence nobody earned.
- Scope the claim honestly. One prompt against one control on one configuration is enough
  to stop a release and to justify a retest; it is not a survey of every control on every
  gateway model.

**Re-test this before every delivery.** If it has been fixed, do not perform the segment —
say instead that it was a real finding on this platform in July 2026 and that the habit of
testing enforcement is the reason you caught it. Never present a fixed defect as current.

## Optional segment: the scored model harness

`harness/` holds the deterministic scoring harness that Chapter 2 used as an attendee
lab before the lab became portal-only. It is retained as teacher material because trial
statistics read well on a projector and badly on thirty individual laptops.

Use it when the room pushes back on judging models by eye, or in the "Connect it"
close as evidence for the `Kimi-K2.6` exclusion. Budget four minutes.

```powershell
uv run python .\teacher\demos\build-host-agent\harness\lab.py preflight
uv run python .\teacher\demos\build-host-agent\harness\lab.py contract --trials 5
uv run python .\teacher\demos\build-host-agent\harness\lab.py cleanup
```

Operator flow:

- **0:00-1:00** Show the contract in `harness/contract.py`. Every check is lexical: it
  reads text and does not understand it. Say that limitation out loud before showing a
  verdict, because the harness flags candidates rather than making the decision.
- **1:00-3:00** Run `contract --trials 5` and read the scorecard. The point is the row
  that passes sometimes: a model that meets the contract on four attempts out of five is
  a different risk from one that never meets it, and only repeated trials make that
  visible. The portal lab cannot show this, which is precisely why it stays here.
- **3:00-4:00** Open one flagged output with `show --model <model-name>` and read the
  wrong quantity aloud. Connect it to what the room just did by eye.

The harness resolves its environment the same way the demo does, and accepts an explicit
`--contract <path>` before the command word when automatic resolution fails. Run
`cleanup` before leaving; it deletes only the agents that run created and reports
anything it could not remove.

Do not offer the harness as an attendee exercise. It needs a working Python environment
per seat, which is the constraint that moved the lab into the portal.

## Recovery

| Symptom | Recovery |
| --- | --- |
| Preflight reports the wrong subscription or tenant | Run `az login --tenant <tenant-id>` and `az account set --subscription <subscription-id>`, then rerun preflight. |
| Local agent does not become ready | Read `.workshop\chapter-2\local-agent.log`; resolve authentication or dependency errors and rerun `local`. |
| Hosted version remains provisioning | Keep the known-good version active and continue with `showcase`. Inspect the version error in Foundry after the session. |
| Hosted invocation returns authorization denied | Allow RBAC propagation, then retry. Keep the prevalidated version active and ask the platform operator to verify capability-host identity admission and Foundry User assignment; do not grant ad-hoc roles during delivery. |
| Memory models are absent from the contract | Apply the documented lab environment Memory deployment first. Do not substitute an arbitrary model during delivery. |
| Memory extraction or model retry exceeds 120 seconds | Stop the live path and run `memory-fallback`. Its output is explicitly marked `prevalidated` and includes the validation timestamp; do not claim a current live recall. |
| Prepare reports that the owned store lacks profile or summary memory | Run `.\teacher\demos\build-host-agent\scripts\demo.ps1 memory-reset`, then rerun `prepare`. This removes only the exact owned Memory agent and store. |
| `memory-reset` reports a partial failure | Read its `removed` and `failed_or_retained` JSON, resolve the reported service error, then rerun the same command. The operation accepts only typed already-absent results and retains the prevalidated fallback state until the store is removed successfully. |
| The isolated user recalls GREEN-742 or STOCK-318 | Stop the demonstration and run `cleanup`; user-scope isolation is a required safety property, not an optional check. |
| Trace verification times out | Continue with the invocation result and show the last prevalidated trace. Do not claim the current trace arrived. Check Azure CLI token health and ingestion delay after the session. |
| A retry encounters an existing version | Run `prepare` without `--new-version`; content-addressed deployment reuses the matching active version. |

## Cleanup

```powershell
.\teacher\demos\build-host-agent\scripts\demo.ps1 cleanup
```

Cleanup is restricted to the three exact demo agent names and the exact Memory store
name. It refuses to proceed if any resource lacks the workshop ownership marker. It
does not delete the teacher project, model deployments, connections, observability
resources, or shared platform.
