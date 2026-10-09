# Autopilot improvement experiments

Owner: Tomas Kubica. Agreed on 2026-10-09. Versions and readiness are recorded
in each card.

These are work contracts, not permission grants or running automations. No
experiment has run or passed because its card exists.

## Confirmed direction

- Keep ACA Sandboxes. Foundry Hosted Agents and an AKS migration are out of scope.
- Hermes scales to zero. Channel ingress, detached completion, event handling
  and scheduled delivery stay outside the Hermes runtime.
- Keep dedicated Gateways for the current demo/hackathon. Shared Gateway work
  is deferred, not rejected. Revisit blueprint, role and business security
  boundaries in [ADR 0014](../adr/0014-per-worker-agent365-blueprints-and-bridges.md)
  before reactivating G05.
- Target GPT-6.1 Sol for major model-backed work. Do not add inference to
  deterministic document retries or queue handling.
- Use the newest compatible stable Microsoft OpenTelemetry release available
  through the approved feed, with Application Insights as the required backend.
- Each session ends with an evidence-backed recommendation. Integration into
  the existing demo requires separate owner approval.
- A supported rejection of an optional change is a successful investigation.
  Missing access, unavailable packages and inconclusive measurements are BLOCKED
  or CAPPED, not evidence that a product cannot work.

## Cards and ordering

| Card | Topic | Actual prerequisite |
|---|---|---|
| [G01](01-gpt-61-sol.md) | GPT-6.1 Sol migration | Existing independent cases; freeze them before trials |
| [G02](02-work-iq-agent-user.md) | Work IQ identity and API/MCP/A2A migration | Existing Worker token and permission inventory |
| [G03](03-document-authoring.md) | Reliable existing-document authoring | G02's proven Agent User route before new-Work-IQ live edits |
| [G04](04-microsoft-otel.md) | Microsoft OpenTelemetry into App Insights | Existing trace baseline |
| [G05](05-shared-gateway.md) | **DEFERRED: shared same-tenant Gateway** | Owner-approved revised card after revisiting ADR 0014; not an active experiment |
| [G06](06-entra-auth-sidecar.md) | Microsoft auth sidecar | Existing federation/token baseline |
| [G07](07-sandbox-managed-mcp.md) | Sandbox-managed MCP and egress auth | Identity routes from G02; G06 evidence if its sidecar is selected |
| [G08](08-fresh-worker-bootstrap.md) | Fresh Worker bootstrap | Approved disposable identity/resource scope |
| [G09](09-hermes-mcp-upgrade.md) | Hermes/MCP upgrade and scheduler parity | Approved newer Hermes distribution |
| [G10](10-registry-managed-identity.md) | Private-image conversion with MI | Approved disposable image/group |
| [G11](11-automatic-resume.md) | Automatic suspend/resume correctness | Existing known-good Worker image, not necessarily G09 |
| [G12](12-learning-evidence.md) | Real learning-quality evidence | Frozen independent cases; bind tested model/runtime versions |

Discovery for active cards can proceed independently. A dependency gates the
corresponding live check, not all reading. G05 is disabled and must not be
started or scheduled. Do not launch sessions from this document automatically.

## Shared contract: mandatory for every card

Read this document together with the selected card. All paths below are relative
to `autopilots-on-azure` unless absolute. Required local sources are `SPEC.md`,
`DEPLOYMENT.md`, relevant `docs\adr\` decisions, and the source/tests named in the
card. The inspected checkout was `8b6635e`; bind the actual checkout and dirty
file inventory at run start rather than assuming it is unchanged.

Bind a run ID, start time, source cutoff and timezone at run start. Use the
current host-provided session artifacts directory and create
`goal-runs\<card ID>\<run ID>\` there. Resolve and record its absolute path before
writing. On resume, use the recorded run, not a new run to reset caps.

Each card's OUTPUT means:

| Artifact | Location in that run directory | Rule |
|---|---|---|
| Decision | `decision.md` | Create; on repair replace only this run's version, retaining prior check results |
| Evidence | `evidence.json` and named sanitized test outputs | Append records; no raw tokens, private documents or prompt bodies |
| Experiment | `experiment\` | Isolated scripts/manifests/locks/tests; never overwrite another run |
| Partial result | `decision.partial.md` | Mark BLOCKED, CAPPED or CANCELLED; do not label it final |

The decision must identify the candidate, baseline, exact measured workload,
results, limitations, adoption bar, and changed code surfaces. It recommends
ADOPT, REJECT or CONDITIONAL. CONDITIONAL is not adoption approval. G01's mandated
model direction cannot be silently rejected; failed gates require escalation.
G04 targets Microsoft's distro, but may reject an unsuitable release/configuration.

Every check starts NOT_RUN. Record check ID, PASS/FAIL/BLOCKED, run/card/artifact
version, timestamp, checker or command, observed result, and evidence pointer.
Freeze the check inputs and rubric before comparing candidates. A repair
invalidates its check and named dependents. Final reconciliation reads the saved
artifacts, not only conversation claims.

In decision experiments, a candidate failing an adoption test is not the same
as an investigation failing its evidence check. A REJECT branch needs the
required inspected contract or completed real comparison and an explicit failed
adoption condition. Missing approval, incomplete trials and ambiguous failures
cannot qualify. Later candidate-only checks pass only through their stated
rejection branch, never through an invented "not applicable".

Allowed now: read relevant local/public evidence and previously authorized
internal evidence; create isolated prototype files in the run directory.
Internal material must stay in private session artifacts, never in this
repository or a public/shareable decision export. Newly required internal
access still needs authority. A required inaccessible source/check is BLOCKED.

Before any live experiment, obtain one scoped owner approval that binds the
tenant, subscription/resource group, Workers, test users/documents, permitted
mutations, resource cleanup and model/connector spend. Existing permission to
research is not permission to change a current endpoint, grant, policy or Worker.
Authentication prompts, consent and elevated tenant changes require owner action.
No production integration, commits, pushes, session spawning or scheduling.

Use real product flows. No mocks, simulated delivery or replay as proof of live
behavior. Do not introduce secrets or developer-user ambient access as a
success-shaped substitute for Agent User authorization. Install dependencies
only in an isolated manifest after inspecting effective registry settings;
query and resolve versions from the approved Microsoft feed, preserve locks,
and never install upstream source to bypass an unavailable version.

External writes require an operation ID and read-back before retry. If a send,
registration, upload or deployment has unknown outcome, stop until read-back
resolves it. Cleanup may target only named experiment-owned resources included
in the approval. Otherwise report the remaining resource and stop. Cleanup
belongs in the evidence and must not touch existing Workers.

Pilot budgets below limit activity, not monetary billing. Actual money is not
measurable from these cards alone. No new paid service or unapproved consumption.
Counts include retries/delegation; do not delegate by default. Reserve the last
20% of wall time and operation allowance for verification, delivery and cleanup.
Do not start work that cannot fit that reserve.

## Agreed adoption rubric

Hard gates: zero observed wrong-identity operations, cross-Worker access, lost
edits, private-content telemetry or undocumented success-shaped failures in the
specified tests. These bounded tests do not prove absence of all vulnerabilities
or establish a production reliability rate.

For optional auth/MCP simplification: remove an owned custom component, or reduce
serial auth/forwarding steps by at least 25%. Count the same successful operation,
including new hops and cache-miss token exchanges. Moving custom code to another
process does not count as deleting it.

The deferred G05 pilot retains its previously agreed 20% lower aggregate
reserved Gateway CPU and memory target for three Workers. This is not an active
experiment or acceptance of its grouping model; revisit the pilot when G05 is
revised. Report monetary projections only with dated pricing and the full added
cost. No claim of lower total cost may exclude brokers, identity services,
telemetry or connector charges.

For performance comparisons: candidate p95 end-to-end latency must not exceed
baseline p95 by more than 10%. Compare identical workloads, allocation, model,
network placement and sampling. Include errors and cold starts separately; no
cherry-picked successful requests. An ambiguous/noisy comparison is inconclusive,
not a pass. Capability changes have additional card-specific hard gates.

These thresholds were owner-approved as pilot targets. A specification or
measurement defect requires an approved card revision; an executor cannot lower
the bar, change test inputs or remove a failing requirement.

## Evidence behind the hypotheses

Bounded public research, retrieved 2026-10-09 using Microsoft Learn search/fetch,
WebIQ web and direct public page fetch. Each row is an inspected primary source
supporting the named hypothesis, not a live parity result.

| Source | Version/update evidence | Supports |
|---|---|---|
| [Sandbox connectors](https://sandboxes.azure.com/docs/sandboxes/connectors) | Search reports September 22 update; full page fetched | Group attachment, per-sandbox opt-in, human-OAuth examples, Copilot configuration |
| [Egress webhook](https://sandboxes.azure.com/docs/sandboxes/sandbox/egress/webhook) | September 1 update in search | External dynamic token/header decisions; authenticated hooks and sandbox-scoped caching |
| [Entra sidecar token flows](https://learn.microsoft.com/en-us/entra/agent-id/microsoft-entra-sdk-for-agent-identities) | Full current page fetched | Autonomous Agent User token parameters, distinct from human OBO |
| [Sidecar configuration](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration) | Full current page fetched | MI-backed assertions and per-request identity overrides; deployment fit unproved |
| [Identity architecture](https://learn.microsoft.com/en-us/entra/agent-id/how-to-plan-agent-identity-architecture) | August 14 update | Blueprint count follows trust boundaries, not just audit labels |
| [Work IQ MCP tools](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/tool-reference) | August 21 metadata; full relevant sections fetched | Graph v1.0 introspection, binary fetch, generic mutations; not Word coauthoring proof |
| [Work IQ policy](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq/mcp/policy-governance-mcp) | August 21 update | Mutations blocked by default; initial controls tenant-wide |
| [Word reference](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-word-tools) | September 30 update | Creation/reads/comments only; workload-specific compatibility warning |
| [A365 setup](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/reference/cli/setup) | October 2 update | Blueprint without endpoint; later endpoint registration; permission transition |
| [Microsoft OpenTelemetry](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/microsoft-opentelemetry) | October 2 update | Unified exporters, baggage, A365 S2S; generic content collection still needs inspection |
| [Hermes release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.21.6) | October 8 release | Upgrade candidate; approved Python distribution still not demonstrated beyond 0.19.0 |
| [Azure models](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure) | October 8 update | GPT-6.1 Sol catalog entry; regional quota/capacity not verified |

No source established that unified Work IQ accepts our fixed Agent User or that
existing Word body editing is now coauthoring-compatible. Shared A365 endpoint
routing and sidecar deployment in a Sandbox also require experiments.

## Handoff

For a later nested session: select an active Ready card, read it with this shared
contract, bind its run, assess existing evidence, work on the next failed check
within the allowed scope, and return DONE, BLOCKED, CAPPED or CANCELLED with
evidence. G05 needs explicit owner reactivation and revision before selection.
Stop at the recommendation. Do not integrate the result.
