# Goal Card: Agent Substrate microVM MVP

## OBJECTIVE

Card: agent-substrate-v1 | Version: 2 | Readiness: Approved for bounded execution
Mode: bounded repair loop after execution approval | Decision owner: repository owner

Deliver a reproducible classroom demo of sandbox execution, out-of-sandbox
credential injection, L7 egress rules, visible orchestration, and stateful
resume. Use native Substrate microVMs (Kata guest on Cloud Hypervisor) on one
separate Linux VM. The owner explicitly selected this before kagent integration.
AKS is the eventual deployment target, not part of this MVP. Exclude queues,
autoscaling, kagent integration, and the OpenSandbox/ACA comparison.

## OUTPUT

Create the implementation in `agent-substrate`: Python managed with uv, a locked
dependency set, thematic Terraform/AzAPI files, minimal upstream configuration,
targeted tests, and a README with exercised setup/run/check/cleanup commands.
Do not invent commands before they exist.

Use ignored `.artifacts\<new-run-id>\` for a small status record, owned resource
IDs, command receipts, failures, and raw measurements. Keep private material
separate from shareable evidence. Partial results must say incomplete.

## DONE WHEN

The executor records each check as NOT_RUN, PASS, FAIL, or BLOCKED with time,
tested version, procedure/command, observed result, and an evidence pointer.
All checks are mandatory; none is passed at design time.

| Check | Pass condition and verification | Evidence | If it fails; recheck |
| --- | --- | --- | --- |
| C01 - Authorized, compatible environment | Owner approves the exact VM, network, identity, budget interpretation, and deployment plan. Read back actual ownership/capacity; inspect kernel/cgroups/API support and prove a real Actor can execute through the Substrate API. | Approval, reviewed plan, native readbacks, actual execution receipt. | Repair supported configuration within approval; otherwise stop. Recheck C01 and every affected runtime check. |
| C02 - Isolation | Two bounded test sandboxes cannot access each other's private state, host filesystem, platform credentials, or denied network destinations. Intended allowed execution still works. Verify actual requests and native runtime configuration, not only admitted manifests. | Positive/negative probes, effective restrictions, sandbox identities and versions. | Repair policy/runtime configuration; never disable isolation to pass. Recheck C02-C05. |
| C03 - Credential injection and L7 egress | A real upstream request receives the synthetic credential injected outside the guest. The guest cannot retrieve it through environment/files or an allowed response. Demonstrate an allowed host/method/path and denied host/method/path with HTTPS interception and normal certificate verification. Probe direct-egress bypass and document DNS handling explicitly. Verify destination-side receipt without saving the credential. | Sanitized upstream receipt, guest negative probes, enforced policy, trust configuration, and trusted-component placement. | Repair supported gateway/policy configuration; unsupported secret separation or ineffective required L7 rules blocks. Recheck C02-C03 and C05. |
| C04 - Start and stateful resume | Run three initial-start trials and three pause/resume trials on a warm Worker via the runtime API. Check filesystem state and a memory-only marker in the resumed process. Time request to successful checked execution, not HTTP acceptance. Report individual elapsed times and failures, separating first boot, Golden snapshot start, and resumed Actor. No percentile study or speed threshold. | Simple timing table, runtime IDs, state checks, and all attempted outcomes. | Repair supported lifecycle configuration; retain failed attempts. Unresolved state loss blocks. Recheck C02-C04 and C05. |
| C05 - Explainable orchestration | The classroom path shows template preparation/Golden snapshot, Actor creation, Worker assignment, request routing, suspension, and wake-up with native IDs and observed transitions. A real deterministic workload produces a checked result through the Substrate consumer API/router; no fake agent/model or kubectl-exec replacement. | Short native lifecycle trace, request/result receipts, and architecture explanation matching the deployment. | Repair the narrow runtime integration; unsupported behavior is explicit. Recheck C04-C05 and affected isolation/network controls. |
| C06 - Classroom handoff | The documented no-argument path demonstrates the above on the final revision. Local tests and deployed smoke succeed; owned-resource inventory matches the retention decision and scoped cleanup is exercised. Verify saved outputs and commands, not a summary alone. | Final run receipt, check reconciliation, actual disposition and README. | Repair scripts/docs or approved cleanup; recheck C06 and invalidated checks. |

## QUALITY

Report observation separately from documented capability and hypothesis.
Do not call gVisor a microVM. Distinguish same-Worker memory resume, filesystem
restoration, cross-Worker restore, cold provisioning, and LLM latency.
Report cold and cross-Worker behavior separately if investigated; never mix
those samples into warm-resume statistics.

C02-C05 require real behavior. C04 has no promised speed threshold; a fast
response without usable execution or preserved state cannot count as resume.
Unresolved failures remain visible and cannot become PASS by being omitted.
Keep credentials and private inputs out of logs.

## CONTEXT

The owner approved a clean pivot on 2026-09-21 and a separate Linux VM first.
The latest scope replaces the 30-run measurement study with a few repetitions
(three starts and three resumes as the MVP default), adds explicit L7 egress
and visible orchestration, and prioritizes native Kata/microVM over kagent.
The prior local trial
failed at overlay mounting on Linux 5.15; it is not a working foundation.

Recheck current compatible Substrate/kagent releases and their prerequisites
against official documentation and pinned implementation before selecting
versions. Use public sources only for product research unless separately
authorized. Prior beta/alpha pins are historical observations, not final pins.

Approved on 2026-09-21: subscription `673af34d-6b28-41dc-bc7b-f507418045e6`,
new resource group `rg-substrate-mvp-20260921`, North Europe, one Ubuntu 24.04
VM with at most 8 vCPU, 32 GiB RAM and 256 GiB disk. Updated approval on
2026-09-22: direct public SSH is prohibited by policy. Use Azure Bastion Standard
with native tunneling and Entra SSH, a system-assigned VM identity, and a
VM-scoped administrator-login role. Privileged runtime components and `/dev/kvm`
are approved only inside this isolated lab. Use synthetic test credentials.
Retain the VM for the demo; no scheduled destruction. Model integration is deferred.
The selected preflight configuration is D8s_v3, zone 3, Ubuntu server
24.04.202609040, Substrate v0.2.0-beta5.

## CONSTRAINTS

Read public primary sources and this project. Write only this project's new
implementation and its scoped evidence. Dependency retrieval uses approved
Microsoft feeds; commit uv.lock when dependencies are introduced.
Use Terraform/AzAPI and platform-native features, not custom control planes.

The owner supplied a new pilot ceiling of USD 200 cloud / USD 1,000 model.
Preserve the old USD 656.86 conservative cloud allowance separately and expose
old plus new totals. The maximum combined allowance would be USD 856.86 if
the full new cloud allocation is consumed. **The owner explicitly approved the
new USD 200 as additional and superseding the old shared USD 750 ceiling.**
Do not interpret a budget as authority for arbitrary resources or role grants.

Concrete provisioning approval is recorded above and in the new run. Do not upgrade or reset the
Windows/WSL host, change corporate shutdown, grant broad permissions, reuse the
old Docker lab, or delete unrelated resources. The old local Docker lab is
owner-deferred, not successfully cleaned.

Keep attempts, failures, budget use, next gap, resource identities, and unknown
effects durable. Resume does not reset limits. Read back accepted or unknown
operations rather than replaying them. Changes to this finish line require
owner approval.

## STAGES

1. Approve and verify one compatible, bounded VM environment.
2. Qualify real Substrate isolation, credential injection, and stateful resume.
3. Demonstrate native orchestration, record the small timing table, and reconcile the handoff.

Use a repair loop only after execution boundaries are approved. For example,
if C03 fails because the allow rule selects the wrong path, correct that rule
within the approved policy, then rerun C02, C03, and the dependent task check.
Progress means an evidenced gap closes, not merely another deployment.

## STOP-CAPS

Zero model requests for this deterministic MVP. Cloud changes stay within the
approved single-VM scope and the new USD 200 allowance.

The first runtime clock expired during the access-policy hold; keep its record.
The owner explicitly renewed the gate on 2026-09-22: at most two hours after
the first working Entra SSH connection through Bastion to demonstrate
real execution and a successful memory pause/resume. On failure, stop with the
specific blocker rather than adding another runtime or platform.

Approved deployment bounds: one VM of at most
8 vCPU, one Worker, at most two Actors including Golden/template Actors;
six repair cycles, two environment creations, twenty deployments, four image
builds, and zero model requests for this deterministic MVP. Count retries and helper work.
The owner increased the combined deployment-attempt ceiling from twelve to
twenty on 2026-09-22 after reconciliation found three Terraform attempts and
nine runtime stages already consumed. Failed attempts remain counted.
The six-cycle, two-creation, twenty-deployment and four-build ceilings are
conservative executor stopping limits, not authority to expand the approved scope.
The approved Bastion host and its public IP count against the same USD 200
cloud allowance; they do not authorize another workload VM or more Actors.
No autonomous schedule or fixed destruction time is enabled.
Retain infrastructure until owner-approved cleanup; do not inherit an obsolete
deadline. Reserve capacity and budget for verification and cleanup.

Stop after three consecutive cycles without closing a required gap or improving
a named measured defect without regression, or earlier at any approved cap.
Unknown side effects block retries.

DONE requires C01-C06 PASS on the final revision.
BLOCKED means a required approval, prerequisite, check, or capability is missing.
CAPPED means a limit stops work before completion.
CANCELLED means the owner stops the new run.
Persist partial outcomes, actual resource disposition, remaining gaps, and the
specific next decision. Never report an exhausted budget or cancellation as success.
