# ADR 0013: Regional placement and capacity fallback

## Status

Accepted.

## Context

The preferred deployment region can be temporarily unable to create a specific Azure resource even when other services in that region remain healthy. During the Terra migration, Sweden Central successfully hosted Foundry and ACA Sandboxes but rejected new Container Apps managed environments with `ManagedEnvironmentCapacityHeavyUsageError`.

Moving every component together is unnecessary and can introduce a second failure. In the earlier deployment, North Europe accepted the Container Apps environments, but new ACA Sandbox creation there was unreliable. The September topology keeps Foundry, service Sandboxes, and the Express private-ingress environment in Sweden Central, while the application network and ACR remain in North Europe over globally peered VNets and shared private DNS.

## Decision

Use this regional preference order for future deployments and capacity fallbacks:

1. Sweden Central.
2. Germany West Central.
3. Norway or France regions.
4. United States regions.

Evaluate capacity per Azure service rather than treating one regional failure as a reason to move the whole platform. Keep latency-sensitive or identity-coupled components together where practical, but split services when the preferred region cannot provision only one resource type.

The current North Europe application-network and ACR placement is an accepted existing exception created before this fallback order was formalized. Do not move healthy shared resources solely to satisfy the preference list; use the order when those resources must be rebuilt or replaced.

For the current topology:

- Foundry and `gpt-5-6-terra`: Sweden Central.
- Per-Worker/service-role ACA Sandbox Groups and Sandbox VNet: Sweden Central.
- Application network and ACR: North Europe.
- Private ingress: Sandbox Group linked to an Express managed environment, with Private Endpoint and DNS; no classic private MCP Container App.
- Private connectivity: global VNet peering plus private DNS links to both VNets. Group/environment links are irreversible and must be checked before apply.

## Consequences

- A capacity failure for one service does not force unnecessary relocation of healthy services.
- Terraform must expose application and Sandbox locations separately.
- Cross-region private traffic depends on global VNet peering and DNS links.
- Operators should try the documented fallback order before selecting another region.
- Service availability, model availability, quota, capacity, networking, and data residency must all be checked before a fallback is accepted.

## Operational evidence

The following July results explain the regional decision, not modernization parity. The shared platform was reapplied on 2026-09-05; both Workers' infrastructure applies passed. Express reports `Succeeded` / public access `Disabled`; linked private Groups also report `Disabled`. Both Workers serve native-model invocations and Hermes private/public MCP scenarios passed. The full-day September 6 query attributes the exact private `list_services` and public `list_demo_shipments` execution spans. Separate controlled runtime requests isolate parent rewriting to Sandbox-origin egress, preserving TraceId; exact proxy implementation is unidentified and platform intermediate parents are absent from AppInsights. Those runtime probes and successful MCP execution do not independently establish the private-MCP VNet route or MI-authentication mechanism. Private external probes reset connections and were cleaned up; they did not return an observed HTTP `403`.

- Sweden Central Foundry deployed `gpt-5.6-terra` version `2026-07-09` as Global Standard capacity 100.
- Sweden Central rejected new Container Apps managed environments with `ManagedEnvironmentCapacityHeavyUsageError`.
- North Europe created the Container Apps environments but did not reliably create new ACA Sandboxes.
- The split deployment passed OpenClaw and Hermes `/invoke` validation with private MCP access on 2026-07-15.
