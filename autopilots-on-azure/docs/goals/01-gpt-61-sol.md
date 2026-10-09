# Goal Card: GPT-6.1 Sol

## OBJECTIVE

Card: G01 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Prove a migration path for all major model-backed work to GPT-6.1 Sol without
changing existing Workers. The mandated direction is not an optional model
beauty contest. Failed correctness gates block migration rather than silently
selecting another model.

## OUTPUT

Use the artifacts and write rules in `docs\goals\README.md`. Decision includes a
model-use inventory and exact proposed defaults/deployment mapping.

## DONE WHEN

All rows are mandatory. Evidence goes into this run's `evidence.json`.

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Inventory covers primary turns, dreaming/reflection, learning evaluation, auxiliary LLM calls and script/config defaults | Executor traces config and call sites; records paths, actual baseline model/version and candidate deployment | Inspect unresolved call sites; block unavailable deployment/quota | C01-C04 |
| C02 | Frozen 12 independent cases run in both arms three times; candidate has zero new correctness/permission/side-effect failures and no lower aggregate case pass count | Read actual answers and tool/artifact read-backs against a predeclared rubric; record all 72 trials, not only summaries | Repair isolated request/config handling; escalate a model capability regression | C02-C04 |
| C03 | Candidate p95 meets the agreed 10% guardrail; cost per successful case and token usage are reported | Match arms and workload; record latency, errors, tokens and dated prices, or explicitly unavailable monetary evidence | Repeat within remaining cap if noise is identifiable; otherwise CAPPED/BLOCKED | C03-C04 |
| C04 | Saved recommendation accounts for every C01 surface, preserves deterministic retries, identifies remaining prerequisites and experiment cleanup | Executor reconciles final files with C01-C03 evidence; no unapproved existing-demo changes | Correct the proposal/evidence; missing mandatory evidence blocks DONE | C04 and affected checks |

## QUALITY

Freeze holdouts and judging rules before model trials (C02). Do not change the
evaluator to flatter the candidate. Bigger context is not quality evidence.
Model endpoint, deployment name and model version are distinct (C01).

## CONTEXT

Read `runtimes\hermes\start_hermes.py`, `scripts\evaluate_learning.py`,
`bridge\runtime\hermes.py`, manifests/locks and the Azure model source in the
shared contract. The current code default includes a GPT-5.6 Terra deployment
name; actual deployed bindings must be discovered, not assumed.

## CONSTRAINTS

The shared contract applies. Live model usage needs scoped approval. Proposed
configuration is isolated; do not edit current defaults or add LLM-based retries.
No silent fallback to another model when GPT-6.1 Sol is unavailable.

## STAGES

1. Freeze model inventory, baseline, holdouts and deployment availability.
2. Compare real candidate behavior; repair only isolated integration defects.
3. Reconcile evidence and deliver the migration recommendation.

## STOP-CAPS

Pilot cap: 3 cycles, 180 minutes from first assessment, 96 live model requests
including repairs. Stop after 3 cycles without closing a failed check or improving
a named metric beyond noise. Shared reserve and cumulative counter rules apply.
DONE requires C01-C04 PASS. Missing deployment/permission is BLOCKED; exhausted
budget is CAPPED; user stop is CANCELLED. Save partial decision and next gap.
