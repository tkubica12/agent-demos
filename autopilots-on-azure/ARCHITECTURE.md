# Autopilots on Azure architecture

This document describes the durable, currently implemented system architecture. It intentionally excludes deployment commands, troubleshooting steps, and future milestone plans.

## Document map

| Document | Responsibility |
| --- | --- |
| `ARCHITECTURE.md` | Current system shape, trust boundaries, identity flows, networking, and component responsibilities. |
| `AUTOPILOTS_SPEC.md` | Product requirements, implemented capability baseline, future design, milestones, and exit criteria. |
| `README.md` | Operator quick start, deployment, validation, troubleshooting, and cleanup. |
| `docs\runbooks\` | Detailed repeatable procedures for flows with multiple control planes or authentication steps. |
| `docs\adr\` | Why consequential architectural decisions were made and what would cause them to be reconsidered. |

## Architecture principles

1. Agent 365 is the only Microsoft 365 packaging and messaging lifecycle.
2. OpenClaw and Hermes are peer runtimes behind the same bridge and Sandbox contract.
3. The public bridge handles ingress and Sandbox lifecycle, not MCP data-plane proxying.
4. Private tools stay private; network reachability and authorization are separate controls.
5. The Agent Identity is the authorization principal for autonomous work.
6. The Agent User is used only for resources owned by the digital worker.
7. Human OBO is per-user and per-turn; it is never the default for autonomous or shared-conversation work.
8. Platform-managed identity and short-lived tokens replace shared application keys.
9. Preview services are isolated behind small adapters so runtime code remains portable.

## System context

```text
Microsoft Teams / Agent 365 / operator /invoke
                    |
                    v
       runtime-specific bridge Container App
       - Microsoft 365 Agents SDK ingress
       - auth-boundary envelope
       - Sandbox lifecycle and invocation
                    |
                    v
              ACA Sandbox
       +---------------------------+
       | OpenClaw or Hermes         |
       | Foundry token adapter      |
       | Agent Identity MCP adapter |
       +---------------------------+
          |          |          |
          |          |          +--> Work IQ Mail MCP
          |          +-------------> public shipments MCP
          +------------------------> private incidents MCP
                                      through customer VNet
```

## Deployment topology

### Shared platform layer

The platform Terraform state owns:

- Azure resource group and virtual network;
- Sandbox delegated subnet and Sandbox Group VNet connection;
- internal ACA environment and private DNS for private MCP servers;
- public ACA environment for bridges and public MCP servers;
- Azure Container Registry;
- Foundry account, project, and model deployment;
- ACA Sandbox Group and its managed identity.

### Runtime application layers

Each runtime has a separate Terraform workspace:

- `autopilot-openclaw`;
- `autopilot-hermes`.

Each workspace owns:

- one bridge Container App and bridge managed identity;
- one private incidents MCP Container App;
- one public shipments MCP Container App;
- runtime-specific settings and image digests.

The current public shipments deployment is repeated per runtime workspace. Only the OpenClaw endpoint is registered as Agent 365 BYO MCP. Moving public shared tools into a separate shared tools state is a possible future simplification, not an accepted decision yet.

## Bridge responsibility

The bridge:

- receives `/invoke` and Agent 365 `/api/messages`;
- translates Teams activities into the runtime-neutral request contract;
- adds the authorization boundary: selected identity mode, invoking human, Agent Identity/User identifiers, and conversation privacy boundary;
- creates, resumes, or reuses the runtime Sandbox;
- forwards turns to the runtime port;
- returns messages and Teams reactions through Microsoft 365 Agents SDK.

The bridge does not:

- proxy MCP traffic;
- hold private MCP API keys;
- impersonate the Agent User for Microsoft 365 tools;
- provide ambient human OBO.

## Sandbox runtime contract

Both runtimes receive the same categories of configuration:

- Foundry endpoint and model deployment;
- runtime image and persistent data volume;
- Sandbox customer VNet connection;
- Agent 365 tenant, blueprint, Agent Identity, and Agent User identifiers;
- fixed upstream MCP endpoints and required scopes.

The runtime connects only to loopback MCP endpoints. `autopilots_identity.mcp_proxy` acquires and refreshes upstream tokens without changing OpenClaw or Hermes authentication internals.

## Identity and authorization model

### Workload credential

The Sandbox Group system-assigned managed identity proves where code is executing. ACA Sandboxes expose it through `IDENTITY_ENDPOINT` and `IDENTITY_HEADER`; Azure Identity uses that endpoint.

The managed identity is not the agent authorization principal.

### Agent Identity federation

```text
Sandbox Group managed identity
  -> api://AzureADTokenExchange token
  -> Agent 365 blueprint federated identity credential
  -> blueprint token with fmi_path=<Agent Identity client ID>
  -> Agent Identity token for the target resource
```

Custom MCP servers authorize:

- tenant and issuer;
- exact target audience;
- Agent Identity client/object identifier when the server is runtime-specific;
- application role such as `Incidents.Read.All` or `Shipments.Read.All`.

### Agent User

For worker-owned Microsoft 365 data:

```text
managed identity -> blueprint -> Agent Identity exchange token
  -> user_fic for the fixed Agent User
  -> delegated Work IQ token with idtyp=user
```

Work IQ Mail uses `Tools.ListInvoke.All` and acts as the Agent User mailbox, not as the invoking human.

### Human OBO

OBO requires a token and consent for the invoking human. It is valid only for an explicit user-owned-resource request. Group chat does not make one user's delegated token group-wide, and Teams channel scope does not provide normal Teams SSO.

OBO is not implemented in A7.

## Network boundaries

### Private MCP

`Microsoft.App/sandboxGroups/vnetConnections` attaches Sandbox network interfaces to the delegated subnet. It provides routing and private DNS only.

The private incidents MCP runs in an internal ACA environment with public network access disabled. The Sandbox calls it directly through the VNet. Entra authorization remains mandatory even though the network is private.

### Public MCP

The shipments MCP is a public HTTPS ACA endpoint with scale-to-zero. It accepts:

- Agent Identity application role `Shipments.Read.All` for direct runtime access;
- delegated `Shipments.Read` for Agent 365 BYO OAuth.

### Egress proxy

Sandbox egress inspection remains enabled. Python HTTP clients use the system certificate store through `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` because the Sandbox egress proxy terminates and re-establishes inspected TLS.

## MCP strategy

| Tool category | Preferred integration |
| --- | --- |
| Microsoft 365 | Agent 365 Tooling catalog and `ToolingManifest.json`; Agent User or explicit OBO. |
| Private custom MCP | Direct Sandbox VNet access plus Agent Identity application authorization. |
| Public custom MCP used by OpenClaw/Hermes | Direct Agent Identity authorization through the Sandbox-local adapter. |
| Public custom MCP governance demo | Agent 365 BYO registration, admin approval, supported-client invocation, and Defender telemetry. |

Agent 365 BYO currently requires a public endpoint and supported client connection/OAuth handling. Approval and gateway initialization were proven; a raw MCP client receives an empty tool catalog because it does not perform the supported-client handshake.

## State ownership

| State | Owner |
| --- | --- |
| Azure resources | Terraform platform/apps states. |
| Runtime filesystem | Sandbox Data Disk volume. |
| Agent 365 blueprint/package files | `.local\<runtime>\agent365`. |
| Agent instance identifiers | `.local\<runtime>\agent365\instance.*.json`. |
| Entra MCP API discovery state | `.local\a7-*-mcp-api.json`, recoverable by display name. |
| BYO registration state | `.local\<runtime>\agent365\byo.public-shipments.json`, recoverable from Entra and Agent 365 catalog. |
| Durable design rationale | `docs\adr`. |

Local state accelerates operation but must not be the only source of truth for cloud object discovery.

## Observability

- Bridge diagnostics record the selected authorization mode and privacy boundary, never tokens.
- Agent 365 Observability permission is configured on each blueprint.
- ACA and Sandbox diagnostics cover runtime and network behavior.
- Agent 365 BYO gateway execution is observable through Microsoft Defender when invoked from a supported client.
- `scripts.snapshot_system` captures redacted Azure, Entra, Agent 365, and local state for comparison.

## Current constraints

- ACA Sandboxes and BYO MCP are preview surfaces.
- OpenClaw and Hermes do not natively implement Agent 365 client-assertion authentication.
- Agent 365 BYO invocation is not supported directly from these runtimes.
- BYO registration may require repair of CLI-generated service principals, grants, public-client settings, and admin assignments.
- Agent 365 admin consent must use the intended tenant account; Windows account broker can select the wrong tenant.
- The platform Terraform configuration contains a pending naming migration from legacy `openclaw-*` resources to `autopilots-*`; it is intentionally not applied as part of A7.

## Related decisions

- [ADR 0001](docs/adr/0001-standard-aca-bridge.md): standard ACA bridge.
- [ADR 0002](docs/adr/0002-teams-event-routing-and-reactions.md): Teams delivery boundaries.
- [ADR 0004](docs/adr/0004-agent-identity-before-workiq.md): identity before broad Work IQ use.
- [ADR 0007](docs/adr/0007-side-by-side-autopilot-deployments.md): runtime-specific workspaces.
- [ADR 0012](docs/adr/0012-mcp-identity-and-governance.md): MCP identity and governance.
