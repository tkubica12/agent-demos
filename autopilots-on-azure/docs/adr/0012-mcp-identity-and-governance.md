# ADR 0012: Use Sandbox federation for custom MCP and Agent 365 Tooling for Microsoft 365

## Status

Accepted.

## Context

OpenClaw and Hermes run inside ACA Sandboxes. They need three distinct tool paths:

- a VNet-only custom MCP server;
- Microsoft 365 tools owned by the Agent User;
- a public custom MCP server that can demonstrate Agent 365 BYO governance.

The Sandbox Group has a managed identity, but that identity represents Azure infrastructure rather than the Agent 365 agent. The Sandbox VNet connection provides network reachability only. A manually created Sandbox Connector Gateway connection was also evaluated, but its Work IQ Mail connection was authorized as the operator's human account rather than the Agent User.

Agent 365 Tooling supports Work IQ catalog MCP servers and blueprint permissions. Agent 365 BYO MCP supports public external servers, approval, runtime policy, and Defender telemetry, but its current preview invocation surfaces do not include custom OpenClaw or Hermes runtimes.

Neither OpenClaw nor Hermes natively implements the Agent 365 managed-identity to blueprint to Agent Identity client-assertion flow.

## Decision

1. Use the Sandbox Group managed identity as a secretless workload credential.
2. Add one federated identity credential to each Agent 365 blueprint, with the Sandbox Group principal as subject.
3. Exchange the managed-identity token through the blueprint into:
   - an Agent Identity token for autonomous custom MCP access;
   - an Agent User token for worker-owned Microsoft 365 access.
4. Run one loopback MCP identity adapter inside each Sandbox. Both runtimes connect to `127.0.0.1`; the adapter acquires short-lived tokens and streams requests directly to the configured upstream MCP server.
5. Reach the private incidents MCP directly through the Sandbox VNet connection. The server validates tenant, issuer, audience, Agent Identity, and the `Incidents.Read.All` application role.
6. Configure Work IQ Mail through `ToolingManifest.json`, Agent 365 blueprint consent, and a principal-scoped `Tools.ListInvoke.All` grant for the Agent User.
7. Deploy the public shipments MCP with Entra OAuth and scale-to-zero. Grant Agent Identities `Shipments.Read.All` for direct runtime access and expose delegated `Shipments.Read` for Agent 365 BYO clients.
8. Register the public shipments endpoint as Agent 365 BYO MCP. Treat BYO invocation from OpenClaw/Hermes as unsupported until Microsoft adds custom runtime support.
9. Remove the bridge MCP relay, shared MCP keys, direct Graph mail tool, bridge VNet integration, and human-bound Sandbox Connector Gateway connection.

## Operational evidence

- ACA Sandbox workloads expose `IDENTITY_ENDPOINT` and `IDENTITY_HEADER`; `DefaultAzureCredential` successfully obtains the Sandbox Group managed-identity token.
- The managed-identity token federates through both runtime blueprints into Agent Identity tokens containing `Incidents.Read.All` and `Shipments.Read.All`.
- Agent User `user_fic` exchange produces `idtyp=user` tokens with `Tools.ListInvoke.All`.
- OpenClaw and Hermes both called the private incidents MCP, public shipments MCP, and Work IQ Mail through the same loopback adapter.
- Entra v2 target tokens use the application client ID GUID in `aud`, while requested scopes use the application URI.
- The manually created Sandbox Connector Gateway Work IQ Mail connection was bound to the operator's human account and was removed.
- Agent 365 BYO `ext_Shipments` was registered and approved. The gateway accepted its generated public-client token and initialized, while raw `tools/list` remained empty without a supported-client connection handshake.

## Consequences

- Custom MCP servers see the Agent Identity as the authorization principal rather than the Sandbox managed identity.
- Agent User mailbox actions are attributable to the Agent User and do not borrow the invoking human's identity.
- The public bridge only handles Agent 365 messaging and Sandbox lifecycle; it does not proxy MCP traffic.
- Private MCP traffic stays inside the VNet.
- Microsoft 365 access uses the Agent 365 catalog and permission model rather than custom Graph wrappers.
- A small runtime-local adapter remains necessary because the selected runtimes do not implement Agent 365 client-assertion authentication natively.
- An approved BYO endpoint can authenticate and initialize through Agent 365, but raw MCP clients do not receive its tools without the supported-client connection/OAuth handshake.
- Human OBO remains a separate, per-turn flow and is not used for autonomous or shared-conversation work.

## Reconsider when

- OpenClaw or Hermes implements Agent 365 client-assertion authentication natively.
- Agent 365 BYO formally supports arbitrary custom runtimes as invocation surfaces.
- ACA Sandboxes expose a documented MCP attachment that preserves Agent Identity and Agent User authorization semantics.
- Work IQ or Connector Namespace provides a managed private-network path for customer-hosted VNet-only MCP servers.
