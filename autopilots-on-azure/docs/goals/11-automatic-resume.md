# Goal Card: Automatic resume correctness

## OBJECTIVE

Card: G11 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine whether automatic suspend/resume preserves Worker state and refreshes
credentials on ACA Sandboxes, and which repair or optional mode is justified.
Keep the external Gateway responsible for post-ACK work and event delivery.
Observed failure must remain explicit; manual start does not prove resume.

## OUTPUT

Shared artifacts plus lifecycle/failure matrix and repeatable approved smoke
procedure. Identify whether a platform/config repair is actually necessary.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Automatic Disk and any proposed Memory mode have explicit timer, wake path, idle metric and persistent-state inventory | Read actual policy, profile/Data Disk layout and credential bootstrap; record known historical `invalid_scope` failure | Bind absent mode/contract; unsupported optional mode can be excluded with evidence | C01-C04 |
| C02 | Complete three automatic Disk cycles and an expired-token window; adopting a proposed repair/mode requires preserved state/receipts and correct token access | Real lifecycle read-back and token-server calls; no manual-start substitution; record actual failures too | Repair isolated bootstrap; observed platform failure can support rejection, not a claim existing resume works | C02-C04 |
| C03 | Complete stopped-runtime queue/post-ACK/duplicate/stale/recovery tests; ADOPT requires correct Worker/durable outcome. Unsafe C02 failure binds rejection evidence instead | Real events/Service Bus/receipts/DLQ/deliverables, not just wake HTTP status | Repair coordination; unknown send outcome stops | C03-C04; C02 after image/config repair |
| C04 | Comparison reports stopped compute, correctness, cold/warm latency and wake cost; ADOPT requires zero stopped compute, no regression and 10% guardrail for a new mode | Read three idle intervals, all timings and cleanup; retain known baseline failures explicitly in rejected proposal | Narrow claims; incomplete lifecycle measurement is BLOCKED/CAPPED | C04 and changed checks |

## QUALITY

Persisted files, preserved process memory and valid identity bootstrap are
different properties (C01-C02). Measure stopped time, not a short idle CPU dip.
Memory mode comparison is optional; Disk resume remains mandatory.

## CONTEXT

ADR 0008/0015/0019; `scripts\sandbox_runtime.py`,
`bridge\runtime\hermes.py`, `servicebus_scheduler.py`; Sandbox lifecycle docs.
Use the current known-good image; no dependency on a Hermes upgrade.

## CONSTRAINTS

Shared contract applies. Only approved test Workers. Do not enable auto-suspend
on the existing always-running Gateway or change canonical schedule ownership.

## STAGES

1. Freeze state/credential/wake inventory and timers.
2. Exercise real automatic transitions and stopped-runtime events.
3. Compare results, clean up and deliver recommendation.

## STOP-CAPS

Pilot cap: 3 cycles, 150 minutes, eight lifecycle transitions, 30 tool/model calls
and 12 queue events. Waiting counts toward wall time. Stop after 3 cycles without
closing a lifecycle gap. DONE requires C01-C04 PASS; inaccessible timers/token
window or approval BLOCKED, limits CAPPED, user stop CANCELLED.
