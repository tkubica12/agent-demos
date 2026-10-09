# Goal Card: Shared same-tenant Gateway

## OBJECTIVE

Card: G05 | Version: 1.1 | Readiness: Draft - not ready to run
Mode: disabled | Decision owner: Tomas Kubica.

Deferred by the owner on 2026-10-09. Do not start, schedule or create an
experiment session for this card. Dedicated Gateways remain the current
demo/hackathon choice. Reactivation requires explicit owner approval and a
revised card after revisiting the blueprint/Worker/role/security-boundary mapping
in [ADR 0014](../adr/0014-per-worker-agent365-blueprints-and-bridges.md).

The criteria below retain the prior pilot proposal, not a decision that
independent per-Worker blueprints are the eventual shared-Gateway architecture.

Assess one always-running Gateway serving three Workers within one Entra tenant,
without combining profiles, Agent Users or blueprints. It owns transport,
eventing, scheduled delivery and wake-up; Hermes still scales to zero.

## OUTPUT

Shared artifacts plus routing/authority inventory, cost comparison and explicit
shared-compromise/failure-boundary analysis. No cross-tenant design.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Three approved registrations establish authenticated identity-to-Worker routing, or completed registration/contract evidence proves a supported routing restriction | Inspect contracts and real test registrations; record URL/recipient/token binding without secrets | Try separate per-Worker paths; missing authority is BLOCKED, not unsupported routing | C01-C04 |
| C02 | Complete own-Worker event/reply/cron/document-retry and conflicting-binding tests; adoption requires zero cross-Worker behavior. If C01 proves ineligibility, bind its REJECT evidence | Real approved fixtures and mismatch tests; compare receipts, profiles and queue/trace IDs | Repair isolated contexts/caches; any unresolved observed crossover supports rejection, never adoption | C02-C04 |
| C03 | If eligible and safe, complete three paired trials of six operations per Worker/arm; ADOPT requires 10% p95 guardrail and 20% less CPU and memory. Early C01/C02 rejection is explicitly evidenced instead | Record all 108 operations and allocations where eligible; no unsafe continued trials after identity crossover | Tune fairness within cap; inadequate measured benefit means REJECT, incomplete data does not | C03-C04; C02 after routing changes |
| C04 | Complete eligible fault/restart tests and record owner acceptance/rejection of shared blast radius; rejected branches have C01-C03 evidence and owner decision instead | Controlled faults/read-back where safe; owner reviews exact boundary and saved measured recommendation | Repair recovery; awaiting owner boundary decision is BLOCKED, not PASS | C04 and affected checks |

## QUALITY

Untrusted URL/payload Worker IDs are hints, not authorization (C01-C02).
Separate blueprints do not preserve full compromise isolation when one process
can act for them all. Functional denial tests are not proof against host compromise.

## CONTEXT

ADR 0014/0015; `bridge\app.py`, `servicebus_scheduler.py`,
`scripts\sandbox_services.py`. Current application initializes one process-wide
auth configuration. G08 may supply disposable Workers; existing approved test
Workers also suffice.

## CONSTRAINTS

Shared contract applies. No current endpoint update or federation widening.
Owner review in C04 concerns the proposal only, not permission to integrate it.
No experiment work is permitted while this card is deferred.

## STAGES

1. Establish registration and authority boundaries.
2. Exercise isolated shared routing, fairness, cost and recovery.
3. Obtain boundary decision and deliver adopt/reject recommendation.

## STOP-CAPS

Stop before execution while deferred. The following pilot limits are dormant,
not permission to run or a reason to reactivate the card automatically.

Pilot cap: 3 cycles, 180 minutes, 150 live model requests, two candidate Gateway
deployments and six test endpoint registrations. Scoped approvals required.
Stop after 3 cycles without a closed gap. DONE needs all checks PASS and C04
owner evidence. Missing registration/acceptance BLOCKED, limits CAPPED,
user stop CANCELLED.
