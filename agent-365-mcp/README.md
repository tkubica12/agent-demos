# agent-365-mcp

Work-in-progress demo for Microsoft Agent 365 that focuses on the **MCP / tooling gateway** pattern.

## Goal

- Start from a minimal Agent 365 agent (Activity Protocol) running on FastAPI.
- Evolve toward **tool-based** integrations where the agent invokes external capabilities via **MCP servers** through the Agent 365 tooling gateway (governed, auditable), instead of calling downstream APIs directly.

## Where to look

- The agent implementation and deployment instructions live in [agent/README.md](agent/README.md).

## References

- Agent 365 tooling servers overview: https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview
- Add and manage tools (MCP integration workflow): https://learn.microsoft.com/en-us/microsoft-agent-365/developer/tooling
