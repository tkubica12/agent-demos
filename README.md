# Agent Demos for Microsoft 365

This repository contains multiple demos of AI agents and their integrations with Microsoft 365, industry standards, governance patterns, and the Agent 365 ecosystem.

## Demos

### empty-demo

Showcases integration, UI capabilities, and authorization flows. It contains an empty agent (a demo agent that sends messages without a true AI system) and demonstrates:

- Activity Protocol via Microsoft 365 Agents SDK through Azure Bot Service
- Streaming events and messages
- Adaptive Cards
- Posting debug information
- Authentication and authorization: user in Teams talks to the agent, and the agent uses on-behalf-of (OBO) flow via Azure Bot Service token exchange to access services as the user
- Accessing Microsoft Graph for user profile via admin consent
- Accessing the empty API service via OBO flow, with user consent on the first message

See [empty-demo/README.md](empty-demo/README.md) for details.

### legacy-agent-publish

Showcases a proprietary AI chatbot projected into Teams without modifying legacy code. It contains:

- A proprietary backend with a RESTful API
- A Streamlit frontend that uses the backend
- A Teams translation service that uses Microsoft 365 Agents SDK via Azure Bot Service toward Teams and translates messages into REST calls to the proprietary backend

See [legacy-agent-publish/README.md](legacy-agent-publish/README.md) for details.

## Roadmap (TBD)

- [ ] Agents 365 solution in "empty" mode
- [ ] Microsoft Agent Framework integration to M365 demo
- [ ] Foundry Agent Service integration to M365 demo
- [ ] MCP layer into all demos between agent and API
- [ ] Multi-agent and A2A
- [ ] Copilot Studio + Foundry Agent Service + Microsoft Agent Framework interactions
- [ ] Foundry IQ integration with user fencing
- [ ] AG-UI interface and integration into Entra ID etc.
- [ ] MCP registry integration and governance

