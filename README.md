# Agent Demos

Demonstrations and educational material for modern Microsoft agent architectures.

## Projects

- [Autopilots on Azure](autopilots-on-azure/README.md) - the primary demo: Hermes digital workers on Azure Container Apps Sandbox with persistent state, Microsoft 365 identity, tools, HTML widgets, and governed learning.
- [Agent Chat Simulator](autopilots-on-azure/chat-simulator/README.md) - a neutral Teams-like chat UI that emulates the Teams channel protocol, so Autopilots Workers can be deployed and tested without Agent 365 or real Teams. It lives inside Autopilots because the runtime images share its container start-up code.
- [Progressive Agents](progressive-agents/README.md) - a step-by-step Microsoft Foundry learning path where each runnable stage adds one platform capability.
- [Foundry Showcase](foundry-showcase/README.md) - a cohesive Foundry solution combining hosted agents, memory, skills, observability, and evaluations, with additional capabilities developed in its [plan](foundry-showcase/PLAN.md).
- [Agent Harness Comparison](agent-harness-comparison/README.md) - four native harnesses, one trusted research application, real Foundry/WebIQ measurements, and an HTML/PDF comparison.

Educational material about agents is available under [docs](docs/).

Messaging gateway security (English):

- Native Agent 365: [standalone HTML](docs/gateway-security/agent365-gateway-security.standalone.html) | [PDF](docs/gateway-security/agent365-gateway-security.pdf).
- Azure Bot Service Connector (standard app registration): [standalone HTML](docs/gateway-security/azure-bot-gateway-security.standalone.html) | [PDF](docs/gateway-security/azure-bot-gateway-security.pdf).
