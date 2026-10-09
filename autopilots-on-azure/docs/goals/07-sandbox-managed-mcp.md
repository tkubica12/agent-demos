# Goal Card: Sandbox-managed MCP

## OBJECTIVE

Card: G07 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine whether group/sandbox connectors or managed egress can remove the
in-container MCP auth adapter without replacing business Agent Identity/User
with infrastructure MI or human connector-owner authority.

## OUTPUT

Shared artifacts plus a comparison of direct adapter, attached connector and
authenticated egress-hook approaches. Name exactly which custom component, if
any, can be removed.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Contract inventory covers per-sandbox opt-in, immutable attachment, custom Hermes configuration, protocol, connection owner, token refresh and private reachability | Inspect platform docs and actual SDK/REST contract; record why current ADR 0012 rejected alternatives | Resolve contract ambiguity; do not equate Copilot auto-config with Hermes support | C01-C04 |
| C02 | Every selected candidate has completed identity/denial evidence or inspected contract evidence of ineligibility; ADOPT requires both business identity modes, correct schemas and forged-selector denial | Real approved attribution read-backs, or actual connection contract/catalog proving human-bound/MI substitution; missing access blocks | Repair isolated config; reject wrong-authority paths with evidence | C02-C04 |
| C03 | Eligible candidates complete resume/expiry/broker/private-DNS/two-Worker tests; ADOPT requires no crossover and fail-Deny. Rejected candidates bind C02 evidence | Record lifecycle/cache results; verify authenticated group/sandbox binding and fixed hook destinations | Repair allowed policy; reject insecure design, stop unsafe calls | C03-C04; C02 after auth repair |
| C04 | Complete comparison: ADOPT requires adapter removal or 25% step reduction and 10% p95 guardrail; otherwise evidence supports REJECT | Eligible paired 30-call workload, full broker cost/latency/maintenance accounting; no benchmark for C02-ineligible routes | Keep direct adapter if benefit absent; missing evidence is BLOCKED/CAPPED | C04 and changed checks |

## QUALITY

Connector attachment authorization and downstream service identity are different.
An egress webhook can mint/inject headers outside the VM but is still an operated
auth service. Do not trust agent-supplied Worker headers (C02-C03).

## CONTEXT

ADR 0012; `scripts\sandbox_runtime.py`, `autopilots_identity\mcp_proxy.py`;
shared connector/egress references. Connectors currently document human OAuth
and automatic Copilot config, not fixed Agent User Hermes parity. G02 supplies
eligible new M365 routes; G06 is needed only if selecting its sidecar.

## CONSTRAINTS

Shared contract applies. Do not attach broad operator-owned connections to
current groups, disclose tokens via hook diagnostics or silently weaken egress.

## STAGES

1. Recover the old limitation and identify exact supported candidates.
2. Test identity/protocol/lifecycle outside current Workers.
3. Compare full complexity and deliver a decision.

## STOP-CAPS

Pilot cap: 3 cycles, 150 minutes, 80 service calls, two disposable Sandbox
creates and four attachment/policy changes. Stop after 3 cycles without resolving
a gap. DONE requires C01-C04 PASS; missing contract/approval BLOCKED, limits
CAPPED, cancellation CANCELLED.
