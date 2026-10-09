# Goal Card: Hermes and MCP upgrade

## OBJECTIVE

Card: G09 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine a supported newer Hermes/MCP combination that simplifies runtime
integration without breaking governance, single-writer state, telemetry or
Gateway-owned scheduled delivery. No in-Hermes always-on receiver.

## OUTPUT

Shared artifacts plus compatibility/source-diff matrix, isolated manifest/lock
and proposed removal/retention list for custom integration code.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | A newer approved Hermes distribution resolves with coherent MCP/client/server versions; actual API/hooks/cron contract changes inventoried | Query approved feed and resolve isolated lock; compare tagged source with current wrappers; record exact release/channel | BLOCKED if newer package absent; never source-install around feed | C01-C04 |
| C02 | Complete frozen session/SQLite/refresh/commands/protected-write/rollback tests; ADOPT requires no new hard-gate regression | Run actual newer runtime with adapted isolated existing tests; inspect profile artifacts and failures | Repair adapter/config; do not disable protections/checks; evidenced unsafe regression supports REJECT | C02-C04 |
| C03 | Complete duplicate/cancel/run-now/claim/resume/delivery queue tests; ADOPT requires correct receipts/settlement and one scheduling authority. Unsafe C02 rejection binds that evidence instead | Read actual durable claims, receipts and Service Bus settlement; compare provider hooks | Repair mapping; ambiguous effects stop; do not run unsafe rejected runtime further | C03-C04; C02 if state changes |
| C04 | Complete metadata-only span and benefit comparison: ADOPT requires parity plus useful capability/component removal and 10% p95 guardrail; evidence may support REJECT | Eligible paired 30-turn workload, exported spans, receipts and source diff; early C02/C03 rejection records branch evidence | Retain required middleware; missing measurement blocks rather than imply no benefit | C04 and affected checks |

## QUALITY

New native skill lifecycle hooks run after mutation and are not a replacement
for our transaction/provenance policy. Security fixes need applicability
analysis, not assumed vulnerabilities. Do not claim new native OTLP covers every
required execution span (C02/C04).

## CONTEXT

ADR 0008/0010/0015/0017/0018; `runtimes\hermes\start_hermes.py`,
`learning.py`, `azure_cron_provider.py`, `cron_runtime.py`;
`bridge\hermes_telemetry.py`; manifests/locks and related tests.
GitHub 0.21.6 exists; approved feed was still at 0.19.0 on October 9.

## CONSTRAINTS

Shared contract applies. Use copied disposable profiles, never an original disk.
No swarms, native conventional Teams replacement or disabled approval checks.

## STAGES

1. Establish supported distribution and exact changed contracts.
2. Exercise real isolated runtime and Gateway parity.
3. Compare retained/deleted complexity and deliver the verdict.

## STOP-CAPS

Pilot cap: 3 cycles, 180 minutes, two candidate resolutions/builds, 60 live model
requests and 40 queue operations. Stop after 3 cycles without a closed gap.
DONE requires C01-C04 PASS; distribution/authority missing BLOCKED, exhausted
limits CAPPED, cancellation CANCELLED.
