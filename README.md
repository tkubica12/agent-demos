# Agent Demos

Demonstrations and educational material for modern Microsoft agent architectures.

## Projects

- [Autopilots on Azure](autopilots-on-azure/README.md) - Hermes digital workers on Azure Container Apps Sandbox, with persistent state, Microsoft 365 identity, tools, and governed learning.
- [Agent Sandbox on AKS](agent-sandbox-aks/README.md) - dedicated Trusted Launch platform and managed Kata pools with Cilium; infrastructure deployed, runtime demo blocked on operator-to-Kubernetes API connectivity.
- [Progressive Agents](progressive-agents/README.md) - a step-by-step Microsoft Foundry learning path where each runnable stage adds one platform capability.
- [Foundry Showcase](foundry-showcase/README.md) - a cohesive Foundry solution combining hosted agents, memory, skills, observability, and evaluations, with additional capabilities developed in its [plan](foundry-showcase/PLAN.md).
- [Private Bot Proxy](private-bot-proxy/README.md) - tests whether an Azure Bot private endpoint can serve as Teams messaging ingress when republished through Application Gateway.

Educational material about agents is available under [docs](docs/).

Internal VM-only Substrate trial (not an AKS demo): native Kata execution,
credential injection, and memory resume worked, but HTTP to TCP port 53 bypassed
egress enforcement. The scenario was cancelled rather than published as a demo.
