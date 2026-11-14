# Step 09 observability and optimizer evidence

Step 09 exports Hosted Agent, Agent Framework, model, skill-tool, memory, and
optimizer runtime spans to the Application Insights resource connected to the
Foundry project.

## Verified deployment

Validated on July 16, 2026 with active hosted agent version 5. The hosted
manifest advertises Responses 2.0.0 only, as required by the Agent Optimizer
preview.

The full deployed smoke passed:

- Responses invocation;
- explicit `support-style` skill loading;
- escalation skill and matrix resource loading;
- unrelated-skill exclusion;
- read-only `lookup_support_policy`;
- Foundry Memory recall from a fresh hosted session.

Runtime traces show the deployed container loaded the local baseline from
`/app/.agent_configs/baseline` with model `gpt-5.4-mini`, three skills, and one
optimizable function tool. The runtime then materialized the candidate-compatible
MAF skill tree under `/tmp`.

Application Insights dependencies recorded successful calls for:

```text
execute_tool load_skill
execute_tool read_skill_resource
execute_tool lookup_support_policy
memory.read
memory.write
```

## Evaluation evidence

The active version 5 baseline evaluation completed with all six tasks passing
under both evaluators:

```text
Evaluation: eval_93b430b242324d728714b1de373da2b9
Run:        evalrun_359ba58bcfe84ce6a8e868ad8d5c2684
Agent:      step-09-foundry-agent-optimizer v5
Result:     6 passed, 0 failed, 0 errored
```

The disjoint held-out dataset also passed locally through the direct Agent
Framework candidate gate, including deterministic assertions and independent
Foundry-model semantic judging, and remotely against version 5:

```text
Evaluation: eval_93b430b242324d728714b1de373da2b9
Run:        evalrun_60daba19b6f542888358ace44199b9bf
Result:     3 passed, 0 failed, 0 errored
```

The final two-candidate optimizer job targeted version 4. Version 5 changed
only the inherited local file-memory path identity; the version 5 evaluation
then reconfirmed all six tasks:

```text
Operation:  opt_a948eccd58a048c6b03e394411626da6
Status:     succeeded
Baseline:   cand_402439e0062b4559a664d3c9485d7734, score 0.773208
Candidate:  cand_bf33e98e1c88419db32fca0594b52b7f, score 0.737820
Mutation:   system_prompt
Best:       baseline
```

The candidate regressed and was rejected by
`Review-And-Promote-Step09.ps1`. Nothing was applied or deployed. The preview
reported `avg_tokens: 0` for both candidates, so the promotion gate would also
reject the run for missing cost evidence even if quality had improved.

## Queries

Inspect optimizer runtime configuration:

```kusto
traces
| where timestamp > ago(24h)
| where message has_any (
    "optimizer.config_loaded",
    "optimizer.skills_materialized",
    "skills.local_provider_configured",
    "memory.foundry_tool_configured"
)
| project timestamp, message, severityLevel
| order by timestamp desc
```

Inspect real skill, resource, policy, and memory operations:

```kusto
dependencies
| where timestamp > ago(24h)
| where name has_any (
    "load_skill",
    "read_skill_resource",
    "lookup_support_policy",
    "memory.read",
    "memory.write"
)
| project timestamp, operation_Id, name, duration, success
| order by timestamp desc
```

## Current limitations

- Agent Optimizer, Foundry Memory, and Agent Skills are preview capabilities.
- Optimizer jobs can return exit code zero before remote failure; the
  provisioning script explicitly verifies the final remote status.
- Optimizer candidate summaries currently report zero average tokens in this
  environment. Promotion remains blocked until usable cost evidence exists.
- Every file under optimizer `skill_dir` is treated as a skill. MAF-only
  resources therefore live outside that directory and are merged into a
  temporary runtime tree.
- The Foundry Conversations view can lag or omit Agent Framework conversation
  indexing even when correlated Application Insights spans are present.

## Validated package set

```text
agent-framework-core 1.11.0
agent-framework-foundry 1.10.1
agent-framework-foundry-hosting 1.0.0a260709
azure-ai-agentserver-core 2.0.0b7
azure-ai-agentserver-optimization 1.0.0b1
azure-ai-agentserver-responses 1.0.0b8
azure-ai-projects 2.3.0
azure-monitor-opentelemetry 1.8.9
openai 2.45.0
```
