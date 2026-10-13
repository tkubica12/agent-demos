# Agent Sandbox on AKS

**BLOCKED - AKS exists, but the operator cannot establish TCP to its Kubernetes
API. Actual Kata execution and the full demo are not yet qualified.**

This second scenario uses Kubernetes SIG Agent Sandbox, AKS managed Kata,
agentgateway outside the sandbox, and Azure CNI powered by Cilium.
The earlier VM-only Substrate experiment was cancelled; this must run on AKS.

## Architecture and security tradeoff

One dedicated demo AKS cluster has two fixed 4-vCPU nodes:

- `platform`: Azure Linux, Secure Boot and vTPM enabled. Controllers and the
  credential-handling gateway belong here.
- `kata`: Azure Linux with managed `KataVmIsolation`, no Trusted Launch.
  A dedicated taint and workload selectors isolate scheduling.

AKS does not support Trusted Launch and managed Kata on the same node pool.
Keeping TL on the platform pool does not eliminate shared-cluster risks.
Do not place unrelated applications in this cluster. Defender for Containers
does not assess Kata runtime Pods. Never describe this as complete isolation.

Sandbox traffic must be constrained by network policy, not merely HTTPS_PROXY.
Credentials belong outside the Kata Pod VM. A gateway sidecar in the same
Kata Pod would not provide the intended outside-guest credential boundary.
Initial capacity is 8 vCPU. The owner-approved ceiling is 20 vCPU; the configured
platform upgrade surge adds at most one 4-vCPU node, for 12 vCPU during maintenance.

Agent Sandbox warm pools are prepared Pods. Its ordinary suspension removes
the Pod while preserving storage; restart does not restore process memory.
This demo must distinguish PVC restart from the cancelled VM experiment's
measured RAM resume, without claiming that AKS provides RAM resume.

## Prerequisites

Terraform 1.15+, the pinned AzAPI provider, uv/Python 3.13+, Azure CLI, kubectl,
kubelogin and Helm. Python has no external dependencies.
Use an authenticated Entra operator with permission to create the dedicated
cluster and manage its Kubernetes objects. No local AKS administrator accounts,
cloud keys or public node SSH are enabled.

Bind owner-approved subscription, tenant, dedicated RG, cluster name and operator
IPv4 /32 in ignored `.local\aks-20260922\approval.json`.
The API allowlist contains the observed operator /32 addresses. AKS automatically permits its
Standard load balancer outbound IP at creation. A real request confirmed that
the special `0.0.0.0/32` placeholder cannot be combined with an operator range;
the rejected request remains in the run evidence.

## Infrastructure commands

From this directory:

```powershell
uv run python -m unittest discover -s tests -v
uv run python scripts\infra.py plan --attempt plan1
uv run python scripts\infra.py apply --attempt apply1
uv run python scripts\infra.py observe --attempt observe1
uv run python scripts\infra.py status
uv run python scripts\qualify.py inventory --attempt inventory1
uv run python scripts\qualify.py create --attempt guest1
```

Attempt names are single-use. Check `.artifacts\aks-20260922` before running
anything: an existing apply receipt must be observed, not replayed. An apply
requires a create-only reviewed plan younger than 15 minutes with matching
source and plan hashes. State and kubeconfig belong in `.local`, never Git.

## Validation and remaining work

The third creation attempt succeeded after preserving two rejected requests
(system-pool upgrade settings and the special API-range placeholder). A fourth,
owner-approved Terraform apply added the second observed operator /32 only.
Both allowlist entries are present in ARM, but direct TCP still times out.
Independent public-IP services and a normal UDP STUN binding observed addresses
already on that allowlist. The failing network hop is not established.
No proxy, corporate network setting or unrestricted API range was introduced.

ARM and VMSS readbacks show the intended one-node pool counts and TL separation.
Kubernetes Ready status and Kata guest execution remain unverified; no
qualification Pods, Agent Sandbox controllers, gateway or synthetic credentials
have been deployed.

After the operator/network owner restores the permitted API path, resume with
new, unused evidence names:

```powershell
uv run python scripts\network_check.py --attempt network-resume1
uv run python scripts\qualify.py inventory --attempt inventory-resume1
uv run python scripts\qualify.py create --attempt guest1
```

The read-only network check exits nonzero on failure. Four Terraform applies
are consumed; do not repeat creation or reset the counters. Owner permission
to add observed operator IPs does not authorize broad CIDRs or unrelated changes.

The plan and local guard tests do not prove runtime behavior. [GOAL.md](GOAL.md)
defines real isolation, credential/HTTPS, bypass, lifecycle and handoff checks.
Native guest execution, Agent Sandbox installation, gateway identity and
credential injection, DNS containment, timings, scoped workload cleanup and
the final no-argument classroom path are not yet qualified.

`qualify.py` first checks ARM, VMSS and Ready-node readbacks, then compares
executed Kata/ordinary-container kernels using two non-root network-denied probes.
It downloads an Entra exec kubeconfig into the private run folder without
changing the global Kubernetes context. Native runtime execution is not replaced
by a RuntimeClass-name assertion.

There is no completed demo or cleanup command yet; do not infer one from these
infrastructure commands. Infrastructure is retained, not destroyed on a schedule.
The separate USD 200 allowance is additional to the existing Substrate budget
and historical spending. The runner records conservative elapsed-time allowance,
not billed usage; retained resources can continue to accrue costs.

Next capability: prove the two-pool security profile and actual Kata execution
before installing the sandbox lifecycle and gateway layers.

## Product references

- [AKS managed Kata](https://learn.microsoft.com/azure/aks/use-pod-sandboxing)
- [AKS Trusted Launch](https://learn.microsoft.com/azure/aks/use-trusted-launch)
- [API access restrictions](https://learn.microsoft.com/azure/aks/api-server-authorized-ip-ranges)
- [Agent Sandbox v1.0.3 AKS example](https://github.com/kubernetes-sigs/agent-sandbox/tree/v1.0.3/examples/kata-aks-sandbox)
- [agentgateway egress proxy](https://agentgateway.dev/docs/standalone/latest/documentation/configuration/egress-proxy/)
