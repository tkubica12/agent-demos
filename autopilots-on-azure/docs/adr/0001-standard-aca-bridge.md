# ADR 0001: Unify service hosting on ACA Sandboxes

## Status

Accepted modernization, reviewed 2026-09-06 at 22:33 CEST. Deployment, model, operator GET/nonowner preflight, same-ID/Data-Disk resume, MCP, schedule, Dream, and bounded evaluation evidence stands. All 49 reviewed native parents resolve; 105 audited spans contain allowed metadata only. Two controlled requests isolate changed parent IDs to Sandbox-origin egress while TraceId survives. Platform intermediate parents are absent from AppInsights; exact proxy implementation is unidentified. Preserve this native-platform waterfall limitation without custom trace headers, fake parents, or egress bypass. Tool execution is not independent VNet-route or MI-authentication proof. Targeted inbound and automatic eight-hour idle behavior remain unverified. Entra conversion remains deployment-only and distinct from blocked native MI conversion.

## Context

The bridge must acknowledge Agent 365 callbacks, continue detached work after ACK, receive scheduled Service Bus messages, and wake the persistent Worker. A normal webhook-only lifecycle is insufficient: HTTP idleness does not mean the process is idle.

Earlier standard ACA hosting avoided an Express outbound TLS trust failure and kept a warm replica because measured cold starts exceeded the Activity Protocol response budget. The approved redesign now uses Sandbox native ingress and explicit lifetime policies for every service role. Express is used as a linked private-ingress managed environment, not as a classic Container App workload.

## Options considered

1. Keep standard Container Apps for gateway/MCP and Sandboxes only for the Worker.
2. Unify runtime, gateway, private MCP, public MCP, and generated applications on separate ACA Sandbox Groups.
3. Move Worker compute, reminders, or resilience to Foundry Hosted Agents.
4. Add relays to emulate missing ingress or wake behavior.

## Decision

Choose the all-Sandbox model. Each Worker has a distinct Group and user-assigned managed identity per service role: `runtime`, `gateway`, `private-mcp`, `public-mcp`, and `generated-apps`.

- The gateway's auto-suspend is disabled. Detached post-ACK processing and a continuous Service Bus receiver must survive between HTTP requests.
- The runtime remains OnDemand with a persistent Data Disk. Expensive Worker compute can suspend independently; the complete system is not scale-to-zero.
- Gateway native HTTPS ingress permits anonymous transport; the Agent 365 SDK authenticates activities. MCP service applications enforce Entra JWT audiences and roles behind their native ingress.
- Private MCP uses a Sandbox Group linked to an Express ACA managed environment with Private Endpoint and private DNS. There is no classic MCP Container App.
- Group-to-environment linking is irreversible. Verify the target before linking; replacing a wrong Group is an explicit infrastructure operation.
- Keep egress inspection and system trust-store validation. Do not disable certificate checks to revive an old path.
- Terraform creates infrastructure; explicit operator stages convert images, create service Sandboxes, and capture endpoints. No `local-exec`, conditional repeated applies, or compatibility relay.

Foundry supplies model inference, external-agent registration, and keyless telemetry. It does not host the Worker compute. Hosted compute migration and relays are rejected for this modernization.

## Consequences and validation

- One service-hosting abstraction replaces classic Container App workloads without merging service identities.
- An always-running gateway has an explicit idle cost. Native HTTP wake alone would not restart queue receive.
- Fast ACK, detached completion, private DNS/routing, negative MCP authorization, runtime resume, Service Bus recovery, and removal of obsolete resources require real validation.
- A successful platform apply proves provisioning only. It does not prove application ingress, model parity, or private connectivity.
- Return the gateway to auto-suspend only after a supported wake mechanism preserves both detached work and queue receive. No such proof is currently claimed.

## References

- [ACA Sandboxes overview](https://learn.microsoft.com/azure/container-apps/sandbox-overview)
- [ACA Express overview](https://learn.microsoft.com/azure/container-apps/express-overview)
- [Service Bus scheduling decision](0015-service-bus-backed-hermes-cron.md)
- [Deployment evidence and commands](../../DEPLOYMENT.md)
