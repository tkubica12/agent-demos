# ADR 0014: Isolate each Worker's deployment envelope

## Decision

Each Worker owns its Agent 365 platform blueprint, messaging endpoint/gateway, Agent Identity, Agent User, Terraform workspace, Data Disk, API/approval keys, and local operator state. Its five service-role Sandbox Groups each have a distinct user-assigned identity.

Workers may share the Git Role Blueprint, Role Release, images, Foundry deployment, networking, and MCP resource applications—not profile state or workload identities. Collective review groups evidence by role/release/commit, not platform blueprint.

## Alternatives and rationale

| Alternative | Trade-off |
| --- | --- |
| One blueprint and shared Worker router | Fewer endpoints/consents, but requires a security-critical identity-to-profile registry and introduces shared failures. |
| Indirection in front of per-Worker gateways | Retains duplicated services while adding routing and authorization complexity. |
| Per-Worker blueprint and gateway | Selected: direct, inspectable Agent User → endpoint → compute → state mapping. |

Isolation costs repeated gateway/MCP services, identities, and consent administration. Reconsider only when measurable Worker count/cost or supported per-instance endpoint routing justifies the larger authorization boundary.

Endpoint changes are owner-sensitive and potentially destructive; [DEPLOYMENT.md](../../DEPLOYMENT.md) defines the preflight and isolated-login procedure.
