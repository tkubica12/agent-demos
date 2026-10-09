# Goal Card: Work IQ under Agent User

## OBJECTIVE

Card: G02 | Version: 1.0 | Readiness: Ready
Mode: repair loop | Decision owner: Tomas Kubica.

Determine which new API, unified MCP and A2A routes can replace our current
Microsoft 365 integration while acting as the fixed Worker Agent User. Produce
an adoption decision per route, not one blanket migration claim.

## OUTPUT

Shared artifacts plus a route matrix: audience, scopes, token subject/actor,
entitlement, policy, operation, result and affected code.

## DONE WHEN

| ID | Pass | Verify and evidence | If it fails | Recheck |
|---|---|---|---|---|
| C01 | Matrix covers legacy catalog, per-server audience migration, unified MCP, REST and A2A; each has inspected contract and code mapping | Review `tokens.py`, proxy/config/catalog and current official schemas; record publication/retrieval dates | Resolve ambiguity through primary sources; required unavailable contract is BLOCKED | C01-C04 |
| C02 | Each proposed route has complete identity/denial evidence: correct fixed Agent User access, or a supported rejection from an inspected contract or completed real negative test | Executor records sanitized verified claims, server result and resource permission read-back; human login substitution never qualifies | Repair exact scope/audience/config once; missing authority blocks | C02-C04 |
| C03 | For C02-eligible mutation routes, complete draft/artifact, refresh and permission-denial tests; rejected routes have explicit C02 evidence binding this branch | Record real operation IDs and state read-backs; distinguish policy denial from token failure; no outbound send required | Stop for missing tenant approval; an approved observed unsupported operation can support REJECT | C03-C04 |
| C04 | Each route has an evidenced verdict; ADOPT requires correct C02-C03 behavior, component removal or 25% step reduction and latency guardrail; otherwise a completed comparison supports REJECT | Compare the same operation where eligible; reconcile final matrix, costs and cleanup; no benchmark needed for a C02-ineligible route | Narrow unsupported recommendations; inconclusive live requirements remain BLOCKED | C04 and changed checks |

## QUALITY

MCP protocol 2.x, A365 tooling v2 audiences and unified Work IQ are different
transitions (C01). An Agent User token being delegated does not itself prove
service eligibility. A2A context must stay Worker-bound (C02).

## CONTEXT

Read ADR 0012, `autopilots_identity\tokens.py`, `mcp_proxy.py`,
`scripts\setup_agent365.py` and shared Work IQ sources. Mutations are documented
as blocked by default; initial policy control is tenant-wide. Baseline uses
`user_fic`, not a human OBO assertion.

## CONSTRAINTS

Shared contract applies. No tenant policy, consent or billing change without
explicit scope approval. Do not migrate every workload because one route works.

## STAGES

1. Inventory contracts and exact identity paths.
2. Test approved real reads, permission denials, draft mutations and refresh.
3. Compare and deliver per-route decisions.

## STOP-CAPS

Pilot cap: 3 cycles, 150 minutes, 60 live service calls including at most 12
mutations. Stop after 3 cycles without a closed check or resolved contract gap.
DONE requires C01-C04 PASS, including evidenced rejection where applicable.
Missing service/approval is BLOCKED, limits CAPPED, user stop CANCELLED.
