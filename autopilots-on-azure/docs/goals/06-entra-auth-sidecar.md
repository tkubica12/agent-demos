# Goal Card: Microsoft Entra auth sidecar

## OBJECTIVE

Card: G06 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine whether Microsoft's sidecar can replace our custom token exchange/cache
while preserving Agent Identity and fixed Agent User authority on ACA Sandboxes.
An evidenced rejection is valid. Evaluate operational separation separately
from actual security isolation.

## OUTPUT

Shared artifacts plus token-flow parity matrix and tested topology recommendation:
co-located process/container or separately isolated private service.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Supported Sandbox topology/MI and pinned candidate established, or inspected contracts plus an approved real probe prove the proposed topology unsupported | Record process/filesystem/network/identity access and version; unavailable authority is BLOCKED | Do not assume Kubernetes sidecars exist; inspect a separately isolated alternative | C01-C04 |
| C02 | Complete application/fixed Agent User and wrong-Worker/audience tests; ADOPT requires correct tokens and denials. C01-ineligible branch binds its REJECT evidence | Approved calls and sanitized verified subject/actor/audience/scope with server read-backs; compare `user_fic` baseline | Repair allowlists; never substitute human OBO; observed unsupported flow supports rejection | C02-C04 |
| C03 | Complete refresh/concurrency/restart/resume/outage tests; ADOPT requires no crossover or unsafe failure. Early rejection binds C01/C02 evidence | Record real cache/lifecycle/negative-call results; no token logging | Repair auth/caches; reject ineffective isolation without continuing unsafe calls | C03-C04; C02 after auth changes |
| C04 | ADOPT only with parity and component removal or 25% step reduction, p95 within 10%; otherwise completed evidence supports REJECT | Eligible paired 30-request workload, cold/cache-hit results, source diff and boundary review; no benchmark for unsupported topology | Keep custom provider if benefit absent; relocation is not deletion; incomplete evidence blocks | C04 and changed checks |

## QUALITY

Detailed Microsoft docs explicitly support `AgentIdentity` with `AgentUserId`
for autonomous user-account tokens. Live Sandbox parity remains unproved.
Returning a bearer token to Hermes does not eliminate token theft risk.
Same-VM root-access separation is not a cryptographic boundary (C01/C03).

## CONTEXT

ADR 0012; `autopilots_identity\tokens.py`, `mcp_proxy.py`; shared sidecar and
configuration sources. Use MI-backed federation, no client secret. An eligible
new Work IQ route depends on G02; existing proven audiences remain baseline.
Compare the supported Microsoft Agents MSAL token provider as a simpler
counterfactual: inspected 1.8.0 source includes `get_agentic_user_token` and
`user_fic`. A sidecar needs additional operational/isolation value, not merely
the same algorithm in another process.

## CONSTRAINTS

Shared contract applies. Expose no public token endpoint. Bound permitted
Agent Identity/User/audience combinations server-side, not from model input.

## STAGES

1. Verify topology/credentials and freeze token matrix.
2. Exercise real parity, negative selection and lifecycle.
3. Measure and recommend replacement or rejection.

## STOP-CAPS

Pilot cap: 3 cycles, 150 minutes, 80 token/API calls, two sidecar deployments and
four lifecycle transitions. Stop after 3 cycles without a closed gap. DONE
requires C01-C04 PASS; missing deployment/identity approval BLOCKED, limits
CAPPED, user stop CANCELLED.
