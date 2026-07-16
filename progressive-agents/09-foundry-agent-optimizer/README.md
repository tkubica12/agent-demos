# Step 09: Foundry Agent Optimizer

Step 09 keeps the complete step 08 Hosted Agent and makes its behavior
configuration measurable and optimizable through the Foundry Agent Optimizer.
The optimizer can propose instructions, Agent Skills, tool descriptions, and
model choices, but it cannot silently replace the active agent.

## What this step proves

- A checked-in baseline version controls instructions, skills, tool metadata,
  and the default model.
- `eval.yaml` reproducibly connects representative tasks, explicit criteria,
  evaluators, optimization models, and a quality/cost model search space.
- The runtime consumes baseline and candidate configuration through
  `azure-ai-agentserver-optimization`.
- Skill candidates affect the catalog while Microsoft Agent Framework keeps
  progressive skill loading and resource access.
- Tool optimization targets one real, read-only checked-in policy lookup.
- Optimization traffic uses an isolated Foundry Memory user scope.
- Applying and deploying a candidate requires an explicit review command.

## Architecture

```text
Responses (hosted) / AG-UI + Invocations (local)
             |
             v
Foundry Hosted Agent
  |-- load_config()
  |     |-- versioned instructions
  |     |-- skill catalog
  |     |-- read-only tool descriptions
  |     `-- selected model
  |-- MAF SkillsProvider -> checked-in SKILL.md + resources
  |-- lookup_support_policy -> checked-in JSON, read-only
  `-- Foundry Memory -> normal user or isolated optimizer scope

eval.yaml -> tasks + evaluators + model search
          -> Foundry Agent Optimizer
          -> scored candidates
          -> human review
          -> explicit apply
          -> held-out evaluation
          -> optional deploy
```

The step 08 memory, profile, conversation, audit, AG-UI, Responses, and
Invocations behavior remains available locally. The hosted step 09 manifest
advertises Responses only because the optimizer rejects agents that also
advertise Invocations.

## Optimizer inputs

| Input | Purpose |
| --- | --- |
| `.agent_configs/baseline/VERSION` | Human-readable baseline version. |
| `.agent_configs/baseline/metadata.yaml` | Model and paths consumed by `load_config()`. |
| `.agent_configs/baseline/instructions.md` | Instruction-tuning target. |
| `.agent_configs/baseline/tools.json` | Tool-description target. |
| `.agent_configs/baseline/skills/*/SKILL.md` | Skill-improvement targets shared with MAF. |
| `datasets/optimizer-train.jsonl` | Six representative optimization tasks. |
| `datasets/optimizer-validation.jsonl` | Three disjoint promotion tasks. |
| `evaluators/step09-optimizer-training/rubric_dimensions.json` | Catalog-backed support-quality rubric. |
| `eval.yaml` | Reproducible optimizer run. |
| `eval.baseline.yaml` | Baseline evaluation without optimizer-only fields. |
| `eval.validation.yaml` | Held-out evaluation after applying a candidate. |

The baseline model is `gpt-5.4-mini`. Model selection compares
`gpt-5.4-nano` and `gpt-4.1` against that baseline. `gpt-5.4` generates
candidates and `gpt-5.4-mini` evaluates them.

## Safety boundary

The optimizer invokes the real Hosted Agent for every task and candidate.
There is no optimizer dry-run sandbox. This step therefore exposes only
`lookup_support_policy` as an optimizable function tool. It reads
`policies/support-policies.json` and cannot mutate external state.

Foundry Memory remains attached because it is inherited agent behavior. During
optimizer candidate evaluation, `load_config()` reports an inline or resolver
source and the runtime changes the default memory identity to
`step09-optimizer-isolated`. Evaluation tasks contain no memory mutations.

## Prerequisites

- Python 3.11-3.13 and `uv`.
- Azure CLI, Azure Developer CLI, and authenticated `az`/`azd` sessions.
- `azure.ai.agents` extension 1.0.0-beta.4 or later.
- An existing Foundry project with these deployments:
  - `gpt-5.4-mini`
  - `gpt-5.4`
  - `gpt-5.4-nano`
  - `gpt-4.1`
  - `text-embedding-3-large`
- Agent Optimizer access for the subscription. The preview is allow-list
  gated; a non-enabled subscription returns HTTP 400.

## Validate locally

```powershell
cd progressive-agents\09-foundry-agent-optimizer

uv sync --frozen
uv run python smoke_optimizer.py
uv run python smoke_skills.py
uv run python smoke_memory.py
```

`smoke_optimizer.py` validates the baseline version, package loading, all three
skills, the one read-only tool, checked-in policy data, disjoint datasets,
evaluator wiring, and model search space.

Run the agent locally:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:MEMORY_STORE_NAME = "step-09-foundry-agent-optimizer"
$env:MEMORY_SCOPE = '{{$userId}}'
$env:MEMORY_STORE_PATH = ".data\memory_store.json"
$env:OPTIMIZATION_LOCAL_DIR = ".agent_configs"
$env:SUPPORT_POLICY_PATH = ".\policies\support-policies.json"

uv run python main.py
```

In another terminal, run the inherited protocol smokes:

```powershell
uv run python smoke_test.py
uv run python smoke_invocations.py
```

Run the BFF and AG-UI smoke:

```powershell
$env:BFF_AUTH_MODE = "disabled"
$env:FOUNDRY_AGENT_INVOCATIONS_URL = "http://127.0.0.1:8088/invocations"
uv run python bff.py
```

```powershell
uv run python smoke_agui.py
```

## Provision, deploy, and optimize

Create or select an isolated azd environment, then run the primary classroom
path:

```powershell
cd progressive-agents\09-foundry-agent-optimizer
azd env new step09

$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
.\scripts\Provision-Step09.ps1
```

The script:

1. restores the frozen environment;
2. validates optimizer, skills, file memory, and Foundry Memory;
3. provisions the ACR and project-identity `AcrPull`;
4. deploys the step 09 baseline;
5. grants the hosted identity its Foundry and model roles;
6. verifies or generates the catalog-backed rubric evaluator;
7. validates Responses, skills, resources, the read-only policy
   tool, and cross-session memory;
8. runs the baseline evaluation;
9. submits a real optimization run, waits for ranked candidates, and verifies
   the remote job succeeded.

Use `-SkipOptimizer` when you only need to deploy and smoke the baseline.

Run optimization separately:

```powershell
azd ai agent optimize --config eval.yaml --no-prompt
```

Monitor an asynchronous run:

```powershell
azd ai agent optimize list
azd ai agent optimize status <operation-id> --watch
```

The command does not apply or deploy any candidate.

## Review and promote a candidate

Review candidate files, per-task evaluator results, composite score, average
tokens, strategy, and the Foundry portal evaluation link. A score increase
below 0.03 is treated as noise. Also compare token growth against the quality
gain.

Promotion requires all review values and the explicit `-Approve` switch:

```powershell
.\scripts\Review-And-Promote-Step09.ps1 `
  -OperationId <optimization-operation-id> `
  -CandidateId <candidate-id> `
  -Reviewer "<name>" `
  -Approve
```

The script reads score and token evidence directly from the completed
optimization operation. The operation must be the latest optimizer job bound
to the current azd environment. It rejects regressions and missing token
telemetry, applies the candidate transactionally, runs
`smoke_optimizer.py`, and invokes the applied candidate directly against the
held-out dataset. Deterministic policy and canary assertions run first; an
independent Foundry model then judges every held-out criterion semantically. It
does not deploy unless `-Deploy` is also supplied. Without `-Deploy`, it writes
ignored local review evidence and restores the baseline so a later ordinary
deploy cannot bypass the gate. After deployment it runs both deployed acceptance
and the model-judged
`eval.validation.yaml`, requiring zero failed or errored tasks. Small gains or
token increases above 25% require `-Justification`. Failed validation restores
the previous local and azd configuration; if deployment already started, the
script also redeploys that prior configuration. The optimizer service is
notified of the promotion only after every acceptance check succeeds.

Do not use `azd ai agent optimize deploy --candidate ...` in this demo. That
path updates the remote agent without preserving the reviewed local
configuration.

## Deployed acceptance

```powershell
uv run python smoke_deployed.py
azd ai agent doctor
```

The deployed smoke verifies:

- Responses protocol 2.0.0;
- support, escalation, and resource-backed skill behavior;
- unrelated-skill non-selection;
- read-only return-policy tool selection;
- Foundry Memory recall across fresh hosted sessions.

## Observability

See
[OBSERVABILITY_PORTAL_INVESTIGATION.md](OBSERVABILITY_PORTAL_INVESTIGATION.md)
for the deployed trace and optimizer evidence.

## Known gaps

- Agent Optimizer is preview, has no SLA, and requires subscription allow-list
  access.
- It supports Hosted Agents that advertise only the Responses protocol.
  Invocations and the AG-UI BFF remain local demonstrations in this step.
- Optimized skills are represented in the optimizer-composed skill catalog;
  MAF still owns on-demand `load_skill` and `read_skill_resource`.
- Preview package and CLI schemas can change. This step pins the resolved
  package graph in `uv.lock` and exports it to `requirements.txt`.
- Every file under `skill_dir` is classified as a skill by the optimizer
  preview. MAF-only resources therefore live under `skills/resources` and are
  merged with candidate skill bodies in a temporary runtime tree.
- Evaluation scores are model judgments. Held-out tasks and explicit review
  reduce, but do not eliminate, score noise.
- Candidate summaries in the validated run reported zero average tokens. The
  promotion gate rejects missing cost evidence rather than treating it as zero
  cost.
- Model deployments and quota remain project-specific prerequisites.

## Next capability

Step 10 replaces direct tool wiring with a versioned Foundry Toolbox exposed
through an Entra-protected MCP endpoint. Step 09 intentionally keeps the single
read-only function tool local so optimizer behavior remains inspectable.
