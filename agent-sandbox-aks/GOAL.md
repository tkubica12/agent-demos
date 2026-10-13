# Goal Card: AKS Agent Sandbox demo

## OBJECTIVE

Card: agent-sandbox-aks-v1 | Version: 1 | Readiness: Ready for approved bounded execution
Mode: repair loop | Decision owner: repository owner

Deliver a real classroom demo of Agent Sandbox on AKS managed Kata, with an
external agentgateway and Cilium enforcement. Keep the platform node on Trusted
Launch; the owner accepts its absence only on the dedicated Kata node.
This AKS demo is the sole remaining sandbox scenario. The VM-only Substrate
experiment was cancelled by the owner and is not an AKS demo.
Exclude models, kagent, queues, autoscaling, custom runtimes and RAM-resume claims.

## OUTPUT

Create implementation, tests and an exercised README in `agent-sandbox-aks`.
Keep private deployment inputs and kubeconfig in ignored `.local`.
Keep approvals, cumulative limits, command receipts, checks and resource IDs
in `.artifacts\aks-20260922`. Preserve failures and do not overwrite attempts.
Partial outputs must remain explicitly incomplete.

## DONE WHEN

All checks are mandatory. Record status, UTC time, tested source/version,
command or procedure, observation and evidence pointer.

| ID | Pass condition and verification | Evidence | Repair and recheck |
| --- | --- | --- | --- |
| C01 | ARM and Kubernetes readbacks show exactly one 4-vCPU platform node with Secure Boot/vTPM, one 4-vCPU managed Kata node, enforced placement, Cilium, Entra access and no local accounts or node public IPs. Prove actual Kata guest execution, not merely a RuntimeClass name. | Plan, approval, pool/VMSS/node identities, guest execution and kernel comparison. | Repair supported configuration only; stop on policy or TL failure. Recheck C01-C05. |
| C02 | Two actual sandboxes cannot read each other's private files, host files or platform credentials. Platform components stay off Kata nodes; sandbox Pods lack platform identity and Kubernetes API access. | Positive and negative native execution probes, UIDs and effective restrictions. | Repair permissions or placement, never substitute ordinary containers. Recheck C02-C05. |
| C03 | A real HTTPS origin confirms synthetic credential injection outside the sandbox. Guest probes and responses do not expose that credential. Host/method/path denials, upstream certificate verification, direct IP, metadata, arbitrary TCP/UDP port 53 and proxy bypass attempts demonstrate enforced boundaries. | Sanitized destination receipts, actual network tests, policy and trust configuration. | Repair bounded gateway/network settings; stop on unsupported identity or bypass. Recheck C02-C05. |
| C04 | Native Agent Sandbox claims execute a deterministic workload. Record three cold creations, three warm claims and three PVC-preserving suspend/restart trials; verify a durable file and explicitly demonstrate loss of the memory-only marker. Count time to usable execution and retain all failures. | Claim/Pod/PVC identities, native client results and individual timings. | Repair lifecycle configuration without claiming RAM resume. Recheck C02-C05. |
| C05 | The no-argument classroom command performs the final demo, local tests and deployed smoke pass, README matches exercised commands, and run-created disposable workloads are removed with UID-scoped absence checks while approved infrastructure is retained. | Final command receipt, check reconciliation, resource disposition and README. | Repair scripts/docs, rerun affected checks and C05. |

## QUALITY

Do not call warm-pool allocation or PVC restoration memory resume. No proxy
environment variable, admitted manifest or HTTP error alone proves containment.
C03 requires destination observations and guest-originated bypass tests.
Do not call Kata a complete security boundary: host boot integrity and Defender
runtime coverage have documented limitations. Keep this tradeoff visible.
No check passes merely because its code exists.

## CONTEXT

Owner approved the two-pool architecture and a separate additional USD 200
cloud budget on 2026-09-22. Start with two 4-vCPU nodes, no autoscaling and zero
model requests. Subscription/tenant and exact RG names are bound in the private
run approval, using the same subscription as the existing demo, not its RG.
North Europe zone 3 and AKS 1.36.3 are the preflight candidates.

Required sources: Microsoft Learn AKS Pod Sandboxing, Trusted Launch and Cilium
documentation; Agent Sandbox v1.0.3 and agentgateway v1.5.0 implementation.
The cancelled VM-only Substrate trial had a documented TCP port-53 egress bypass.
Its results do not satisfy any AKS check.
The new USD 200 is not taken from its allocation or historical USD 656.86.

## CONSTRAINTS

Write only this project and its new dedicated Azure scope. Use Terraform/AzAPI,
managed identities and Entra operator access. Keep API access source-restricted;
no public node SSH, stored cloud keys, platform credentials in guests, wildcard
RBAC grants, subscription policy changes or corporate automation changes.
Use existing inherited operator permissions; no new broad operator role.
Do not adopt unrelated resources, revive the old Docker lab or change Substrate.
Retain infrastructure for demonstration; no scheduled destruction.

Preserve action receipts before mutations and read back unknown outcomes before
any retry. Serialize run writers with an exclusive lock. Resume cannot reset
counters, clock or failures. Never weaken checks to make a run pass.

## STAGES

1. Prove authorized AKS, Trusted Launch/Kata separation and real network enforcement.
2. Add native Agent Sandbox lifecycle and external credential-injecting gateway.
3. Qualify the classroom command and reconcile all evidence and retained resources.

Use a loop for repairable configuration gaps: if C03 fails because a DNS rule
allows arbitrary destinations, narrow the rule, then rerun C02, C03 and C05.
Unsupported platform behavior or missing authority causes BLOCKED, not a pivot.

## STOP-CAPS

Owner limits: additional USD 200 cloud, 20 vCPU and zero model calls.
The owner expanded the vCPU ceiling after AKS rejected no-surge system-pool
upgrades. Initial capacity remains 8 vCPU; the configured one-node platform
upgrade surge can temporarily reach 12 vCPU. No autoscaling is enabled.
Owner also approved adding any actually observed operator public IP as an exact
/32 to the API allowlist. A fifth Terraform apply was approved on 2026-09-23
only for adding 178.17.15.151/32. Do not infer permission for wider CIDRs,
other cluster updates or additional apply attempts.

Conservative executor limits: 6 cycles, 5 Terraform apply attempts, 20 deployment
attempts, 2 simultaneous sandbox Pods including warm capacity, 4 hours from
first cloud mutation. These stopping limits do not expand owner authority.
The owner renewed only the execution time gate on 2026-09-23 for four hours
starting with the stopped cluster's restart. Retain the original start time,
all consumed attempts and repair cycles, and the same cumulative USD 200 budget.
Reserve USD 12 for verification; use USD 6/hour elapsed-time accounting until
billed usage is available, explicitly not a bill or a price guarantee.
Stop after three cycles without closing a required gap, or at any earlier cap.

DONE requires C01-C05 PASS on the final revision.
BLOCKED means a required permission, capability or check cannot be satisfied.
CAPPED means a limit ended work before success. CANCELLED means owner stop.
All non-DONE outcomes preserve partial evidence, costs, resource disposition
and the exact next action. Retained resources can accrue costs after a stop.
