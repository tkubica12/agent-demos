# Agent Substrate microVM MVP

**Status: BLOCKED - native microVM execution, memory resume and credential
injection work, but destination port 53 bypasses the egress gateway.**

On 2026-09-22 the owner declined an additional host firewall workaround and
requested that this limitation remain visible. No firewall, replacement CNI,
or alternate runtime was added. The MVP is incomplete, not cancelled or DONE.

## Observed results

Qualified stack: Substrate `v0.2.0-beta5` (source
`8316538f31ccaf2d51277d9b775b6b4f8691c129`), Kata 4.0.0 guest assets,
Cloud Hypervisor 53.0, virtiofsd 1.14.0, one kind Worker on the Azure VM.
Released Substrate images report embedded version `dev`; tag and actual image
digests are recorded in the evidence rather than inferred from that string.

| Capability | Observed result |
| --- | --- |
| Real microVM execution | PASS: workload reached through authenticated, certificate-verified HTTPS routing; guest kernel `6.18.35`, host `6.17.0-1022-azure`. |
| Three Golden-snapshot starts | 1.0885 s, 1.0354 s, 1.0276 s to checked execution on a warm Worker. |
| Three memory resumes | 0.5669 s, 0.7062 s, 0.5615 s; independent memory and file markers survived unchanged. |
| Credential injection | Destination confirmed a synthetic Authorization credential injected outside the guest. Tested guest environment/private paths and allowed response did not expose it. |
| HTTPS L7 rules | Allowed host + GET + `/allowed` succeeded. Denied host, method and path returned 403. Direct-IP HTTPS was rejected; plaintext HTTP and the metadata probe returned 403. |
| Egress bypass | FAIL: an HTTP request to the controlled origin on TCP port 53 reached `/forbidden`, outside gateway enforcement. |
| Full isolation and classroom handoff | Incomplete: two-sandbox isolation, durable suspend/wake and the final no-argument demonstration have not been qualified. |

The bypass is not a credential leak: the destination received **no injected
credential** and returned 401. That response and the destination-side receipt
prove the forbidden request reached the origin anyway. Substrate's pinned
network-egress contract explicitly exempts TCP and UDP destination port 53;
this run demonstrated TCP, not arbitrary UDP behavior. An exemption by port is
not DNS protocol validation. No claim of complete egress containment is made.

Host/method/path enforcement uses the existing bundled agentgateway:
Substrate-native per-Actor hostname policy and credential resolution, plus
configured gateway method/path authorization. Plain HTTP and opaque TCP routes
were denied. Upstream HTTPS trusts the cluster service-DNS CA, without disabling
certificate verification. These settings do not intercept the port-53 exception.

Build a small classroom demonstration of Agent Substrate with native
microVM isolation: a Kata guest on Cloud Hypervisor, not the Kata containerd
shim. Start on one separate Linux VM with verified kernel, KVM, and Kubernetes
prerequisites. AKS remains the eventual target; kagent integration follows this
MVP. Queues and automatic scaling are out of scope.

## What this should prove

- Real sandbox execution with isolation and denied-access controls.
- Credential injection outside the sandbox: a permitted upstream request works,
  while the guest cannot retrieve the injected credential or bypass the policy.
- L7 egress: allowed and denied hosts, HTTP methods and paths, including HTTPS
  interception with certificate verification and direct-bypass controls.
- Three starts and three stateful pause/resume trials through the runtime API.
  Show elapsed times and all failures, without a statistical benchmark or promise.
- Visible template preparation, Golden snapshot, Actor/Worker assignment,
  routing, suspension and wake-up using a real deterministic workload.

The intended path is operator -> Substrate API/router -> Worker -> Kata microVM.
Credential handling stays in the trusted platform, outside the sandbox.
Verify the actual request path and enforcement on the selected versions.
The owner selected this native microVM path before kagent integration:
[kagent 1.x currently compiles gVisor ActorTemplates](https://kagent.dev/docs/kagent/1.x/substrate-runtime/sandboxing/),
whereas [Substrate provides a native microVM runtime](https://github.com/kagent-dev/substrate/blob/v0.2.0-beta5/docs/dev/microvm-local.md).
Do not patch kagent to claim a supported integration or silently switch runtimes.

## Prerequisites and deployment

The owner selected the VM-first approach and a new pilot budget of **USD 200
cloud and USD 1,000 model**, with zero model calls in this deterministic MVP.
The new USD 200 cloud allocation is explicitly additional to historical spending.

Approved deployment: one Ubuntu 24.04 D8s_v3 VM in North Europe zone 3,
8 vCPU / 32 GiB RAM / 256 GiB disk, in dedicated group
`rg-substrate-mvp-20260921`. Direct public SSH is prohibited by corporate policy.
The owner approved Azure Bastion Standard with native tunneling on 2026-09-22;
operator login uses Entra and VM-scoped authorization. Retain the VM for demos,
with no scheduled destruction. Verify compatible product pins, kernel, cgroup
mode, and Kubernetes certificate APIs. The previous
local Linux 5.15 environment failed before the first Actor could execute.
The previous implementation's kernel check used 6.8 as a conservative stock
kernel floor; a newer kernel alone is not evidence of runtime compatibility.

Terraform created the VM after repairing the Compute API version to 2025-11-01.
The first rejected request and the successful repair are both retained.
Native remote inspection verified kernel `6.17.0-1022-azure`, cgroup v2, KVM API
12 and successful `KVM_CREATE_VM`. Subsequent native Actor execution and memory
resume succeeded through Bastion; KVM alone is not the isolation evidence.

The approved SSH /32 allow rule was subsequently removed. Azure activity logs
show a different principal writing the NSG at 20:25 UTC on 2026-09-21; current
native readback had no custom rules, and direct Entra SSH timed out. Do not reapply the
rule repeatedly, expand access, change corporate automation, or use another
transport to route around the restriction. The owner has selected the permitted
Bastion access path instead of an exception, and that path was used successfully
for runtime deployment and qualification. Its cost remains inside the
same USD 200 limit; public retail gateway pricing is USD 0.29/hour before IP
and transfer charges, not a billed-usage measurement. The VM public IP is
currently retained for outbound access only; no public SSH allow rule remains
in Terraform. Bastion reaches the VM on its private address.

Run state and receipts are in `.artifacts\mvp-20260921`; Terraform state and
short-lived SSH material are in ignored `.local\deployment`. The VM and its
resources remain retained and can accrue charges while blocked.
There is **no completed no-argument classroom command**. [GOAL.md](GOAL.md)
defines the unchanged finish line. Deployment attempts were reconciled across
Terraform and remote stages: 14 of the owner-extended limit of 20 are consumed,
with four of six repair cycles and zero model calls.

Primary saved evidence (ignored, local to this run):

- `status.json`: terminal BLOCKED decision, cumulative limits and retention.
- `lifecycle-results.json`: all three start/resume trials and native identities.
- `security-check1-results.json`: positive/negative probes and sanitized
  destination receipts, including the failed bypass check.
- `security-setup-results.json`: scoped synthetic credential setup and effective
  gateway configuration hashes.
- `owner-dns-boundary-decision.json`: refusal of the additional firewall.

The remote scripts are one-shot qualification stages, not safe-to-replay demo
commands. Inspect their existing receipts before any future execution.
Read local results without touching the deployment:

```powershell
Get-Content .artifacts\mvp-20260921\status.json
Get-Content .artifacts\mvp-20260921\security-check1-results.json
```

## Reset and known gaps

The previous OpenSandbox/ACA comparison was cancelled, not completed. Its Azure
AKS cluster, managed node resource group, registry, and parent resource group
were removed and their absence checked on 2026-09-21. Its implementation,
generated project files, and reviewed run-specific private files were removed.
A small audit record remains outside this repository in the coordinating
session's `files\sandbox-reset-audit` directory.

The prior **USD 656.86** cloud figure is a cumulative conservative allowance,
not billed usage; it is not erased by the new pilot budget. Delayed bills can
still arrive. Keep old and new costs distinguishable and expose their sum.
The two budget figures are separate run allocations, not proof of actual cost.
The owner explicitly superseded the former USD 750 shared ceiling for this
additional pilot. Actual billed usage has not been established.

**Owner-deferred cleanup:** Rancher Desktop was stopped. The old Docker lab
`sbp-c17lab1`, its data/network, and the recorded OpenSandbox image were not
deleted or declared absent. Do not reuse them for this clean deployment.
Their exact recorded IDs and the unresolved Actor resume are retained in the
external audit for a later, explicitly scoped cleanup.

The runtime viability gate was met within two hours of first working Bastion
SSH. C01 and C04 passed; C03 failed on the demonstrated bypass. C02, C05 and C06
remain incomplete. No further deployment is authorized by the BLOCKED handoff.
VM, Bastion and runtime resources remain retained under the existing decision
and may continue accruing charges; this handoff does not stop or delete them.
The last runtime observation retained `mvp/trial-3` on one Worker. No new cloud
inventory was taken when recording the owner's stop decision.
