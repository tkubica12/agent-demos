# Goal Card: Learning evidence beyond response text

## OBJECTIVE

Card: G12 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Design and prove an isolated evaluation path that measures native skill
discovery, real tool/artifact outcomes and retained learning, not only response
strings. Keep signed promotion and independent holdouts; no distributed learning.

## OUTPUT

Shared artifacts plus an independent declarative case inventory, protected
expected outcomes and isolated evaluation runner/procedure. Raw private
responses stay private and are not a repository deliverable.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Ten operator-approved independent cases cover discovery, real document/tool outcomes, privacy/generalization, regression and restart/role refresh retention | Owner confirms fixed cases and artifact assertions before execution; record suite version and provenance | Repair ambiguous cases before freezing; awaiting independent owner input BLOCKED | C01-C04 |
| C02 | Baseline/candidate execute each case three times with tools and native discovery enabled; same model/config/private-state baseline; every outcome inspected | Record 60 real trials, selected skill/tool traces, artifact read-backs, failures and latency/token totals | Repair isolated runner; never inject skill bodies to replace discovery | C02-C04 |
| C03 | Runner detects deliberate wrong-artifact, missing-discovery, privacy-canary and lost-retention controls; candidate adoption requires no new hard-gate failures or lower pass count | Approved negative controls and real profile restart/refresh; record candidate failure as evidence for rejection, not checker success by assumption | Fix checker and rerun both arms after judging repair; holdouts never enter learning | C03-C04; C02 after checker repair |
| C04 | Decision accurately reports measured benefit, variance and limits; does not certify learning merely from signed provenance or strings; outputs and cleanup reconciled | Executor reads saved evidence and proposed pipeline mapping; owner reviews material rubric disagreements | Correct overclaims; no demonstrated benefit means REJECT candidate, not reject evaluation integrity | C04 and changed checks |

## QUALITY

Authorship/provenance is not behavior quality. Passing self-authored scenarios
is not independent holdout performance (C01). Repeated small trials show bounded
evidence, not a general reliability percentage (C02-C04).

## CONTEXT

ADR 0010; `scripts\evaluate_learning.py`, `collective_review.py`,
`runtimes\hermes\learning.py`, relevant tests. Existing runner explicitly disables
tools/hooks/MCP and inserts skill bodies into the prompt. G01's model binding
must stay fixed within the comparison; G09 is not required.

## CONSTRAINTS

Shared contract applies. Owner-approved disposable resources only. No promotion
of a tested learning packet, reusable skill modification, evaluator weakening
or source-document export.

## STAGES

1. Freeze independent cases, native discovery and measurable assertions.
2. Run both arms and fault the checker with negative controls.
3. Reconcile retention/privacy/quality evidence and deliver recommendation.

## STOP-CAPS

Pilot cap: 3 cycles, 180 minutes, 90 live model requests and 40 external mutations.
Account for tool-driven extra model calls before starting a case. Stop after 3
cycles without closing a checker/outcome gap. DONE requires C01-C04 PASS; missing
independent suite/approval BLOCKED, limits CAPPED, user stop CANCELLED.
