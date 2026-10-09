# Goal Card: Microsoft OpenTelemetry into App Insights

## OBJECTIVE

Card: G04 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine which configuration of the newest compatible approved stable Microsoft
OpenTelemetry distro exports useful, private-by-default Gateway/Hermes/tool
evidence into Application Insights. Reject an unsuitable tested configuration,
not the Microsoft-distro direction. Keep supported native middleware.

## OUTPUT

Shared artifacts plus proposed package/config changes and repeatable trace-query
procedures. Required backend is App Insights; A365 export is optional.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Approved version resolves in isolated lock; baseline lists ingress, runtime, inference, tool, schedule and publication spans and known platform gaps | Inspect registry settings, source and actual baseline traces; record exact versions and span inventory | Resolve compatible approved version; unavailable release is BLOCKED | C01-C04 |
| C02 | Complete 20 frozen operations and backend read-backs; ADOPT requires expected owned spans, Worker/model attribution and no duplicates; observed export defects support REJECT | Query App Insights after declared ingestion window; compare operation IDs/counts; distinguish missing backend access from observed export failure | Repair isolated propagation/export config; inaccessible required backend blocks | C02-C04 |
| C03 | Complete synthetic-canary/baggage/outage/performance checks; ADOPT requires no content/crossover, visible bounded failure and 10% p95 guardrail. Unsafe C02 rejection binds its evidence instead | Inspect actual exported data and outage results; use only synthetic canaries, never private bodies | Disable unsafe capture; retain metadata-only middleware; reject unsafe configuration | C03-C04; C02 if spans change |
| C04 | Saved decision recommends Microsoft's distro configuration only if C02-C03 adoption gates pass, otherwise rejects that tested configuration with evidence; retained middleware and cleanup recorded | Read final package/config diff and backend evidence; no automatic-full-tracing claim | Repair decision/cleanup; incomplete required backend evidence blocks | C04 and changed checks |

## QUALITY

Freeze expected owned span counts before testing. TraceId continuity is not
proof of an intact parent chain. Auto-populated baggage needs an allowlist;
private content and unnecessary personal identifiers are not acceptable (C03).

## CONTEXT

Read `bridge\telemetry.py`, `telemetry_http.py`, `hermes_telemetry.py`,
`tests\test_telemetry.py`, `tests\test_hermes_telemetry.py` and shared distro
documentation. Existing callback and executor handoff are implemented, not
hypothetical legacy code.

## CONSTRAINTS

Shared contract applies. App Insights access/export and test model usage need
scoped approval. No new backend or delegated A365 consent merely for this goal.

## STAGES

1. Freeze span/privacy inventory and select approved distro.
2. Export real operations; repair instrumentation and inspect failures.
3. Reconcile backend evidence and deliver configuration recommendation.

## STOP-CAPS

Pilot cap: 3 cycles, 150 minutes, 60 live model requests and 20 backend queries.
Reserve final ingestion/read-back time. Stop after 3 cycles without closing a
span/privacy gap. DONE requires C01-C04 PASS; missing backend/approval BLOCKED,
limits CAPPED, cancellation CANCELLED.
