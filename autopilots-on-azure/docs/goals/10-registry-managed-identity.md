# Goal Card: Registry conversion with managed identity

## OBJECTIVE

Card: G10 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Re-test the exact private OCI-to-Sandbox-disk conversion that previously failed
with `401 RegistryAuthFailed`. Determine whether ADR 0021's deployment-time ACR
token exception can be removed.

## OUTPUT

Shared artifacts plus a minimal scripted conversion/read-back procedure and
sanitized error/RBAC matrix. Do not modify current image references.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Actual group MI, image digest, registry authorization mode, roles, SDK/API version and prior failure are bound | Inspect ADR 0021 and deployment code; read approved test resource configuration | Resolve naming/role propagation versus service failure; missing permission BLOCKED | C01-C03 |
| C02 | Complete two MI conversions of the same private digest and denied-access probe; ADOPT requires both Ready/boot/content checks without deployer token. Resolved service failure supports REJECT | Read disk states, boot marker when conversion succeeds and sanitized diagnostics; missing RBAC approval does not qualify as product failure | At most one justified config repair; unknown conversion outcome blocks retry | C02-C03 |
| C03 | Remove exception only if all C02 adoption gates pass; otherwise preserve it and classify evidenced failure; approved cleanup complete | Read final registry/disk/resource states and proposed change; no fixed-status speculation | Repair report/cleanup; incomplete conversion evidence blocks | C03 and changed checks |

## QUALITY

Failure of MI runtime access and failure of platform registry conversion are
different. Do not infer a service fix from an unchanged/new SDK or unavailable
issue page (C01-C03). Native MI is the benefit; no invented latency saving needed.

## CONTEXT

ADR 0021; `scripts\sandbox_runtime.py`, `sandbox_services.py`; Sandbox disk-image
docs. SDK was `0.1.0b4`; the current service-side issue resolution is unverified.

## CONSTRAINTS

Shared contract applies. No public registry migration, exposed ACR credentials
or stored workload secret. RBAC changes need scoped owner approval.

## STAGES

1. Reconstruct exact conversion and role prerequisites.
2. Run bounded real conversion/boot checks.
3. Clean up and deliver removal or retention recommendation.

## STOP-CAPS

Pilot cap: 2 cycles, 75 minutes, three conversion attempts including repair,
two boots and one denied-access probe. Stop sooner if no new diagnostic evidence.
DONE needs C01-C03 PASS; REJECT requires the complete bounded C02 comparison,
not missing authority.
Missing permissions BLOCKED, limits CAPPED, user stop CANCELLED.
