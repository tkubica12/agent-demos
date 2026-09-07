# ADR 0012: Agent Identity federation and managed Microsoft 365 tools

## Decision

Use distinct runtime/gateway managed identities as workload credentials, federated through the Worker's Agent 365 blueprint. A loopback MCP adapter exchanges them for:

- Agent Identity application tokens for autonomous custom MCP;
- fixed Agent User delegated tokens for Worker-owned Microsoft 365 resources.

Private incidents requires VNet/private-DNS reachability **and** Entra audience/Worker/app-role validation. Public shipments uses HTTPS and Entra authorization. Work IQ comes from the Agent 365 Tooling catalog and consent manifest; reviewed Graph wrappers cover concrete missing document operations.

Agent 365 BYO registration is a separate public-endpoint governance/client path, not a replacement for direct runtime MCP access.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Authorize business tools as the Sandbox identity | Represents infrastructure rather than the digital Worker's business identity. |
| Human-bound connector as ambient authority | Gives autonomous work an operator's data access. |
| Gateway MCP relay or shared API keys | Adds an unnecessary data-plane hop and credential store. |
| Treat a private network as authorization | Reachability does not identify or authorize the caller. |

Runtime and gateway have separate federation credentials; MCP/app roles do not inherit the same business trust. Native Foundry model authentication does not implement Agent Identity/Agent User federation, so it does not eliminate this adapter.

Reconsider when a managed runtime-native integration supplies the same identity and private-network boundaries. See [identity runbook](../runbooks/identity-mcp.md) and [current limitations](../../SPEC.md).
