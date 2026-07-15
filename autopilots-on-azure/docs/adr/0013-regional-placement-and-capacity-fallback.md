# ADR 0013: Regional placement and capacity fallback

## Status

Accepted.

## Context

The preferred deployment region can be temporarily unable to create a specific Azure resource even when other services in that region remain healthy. During the Terra migration, Sweden Central successfully hosted Foundry and ACA Sandboxes but rejected new Container Apps managed environments with `ManagedEnvironmentCapacityHeavyUsageError`.

Moving every component together is unnecessary and can introduce a second failure. North Europe accepted the Container Apps environments, but new ACA Sandbox creation there was unreliable. The final deployment therefore keeps Foundry and Sandboxes in Sweden Central while Container Apps and ACR run in North Europe over globally peered VNets and shared private DNS.

## Decision

Use this regional preference order for future deployments and capacity fallbacks:

1. Sweden Central.
2. Germany West Central.
3. Norway or France regions.
4. United States regions.

Evaluate capacity per Azure service rather than treating one regional failure as a reason to move the whole platform. Keep latency-sensitive or identity-coupled components together where practical, but split services when the preferred region cannot provision only one resource type.

The current North Europe application layer is an accepted existing exception created before this fallback order was formalized. Do not move a healthy deployment solely to satisfy the preference list; use the order when the application layer is next rebuilt or when North Europe must be replaced.

For the current topology:

- Foundry and `gpt-5.6-terra`: Sweden Central.
- ACA Sandbox Group and Sandbox VNet: Sweden Central.
- Container Apps environments and ACR: North Europe.
- Private connectivity: global VNet peering plus private DNS links to both VNets.

## Consequences

- A capacity failure for one service does not force unnecessary relocation of healthy services.
- Terraform must expose application and Sandbox locations separately.
- Cross-region private traffic depends on global VNet peering and DNS links.
- Operators should try the documented fallback order before selecting another region.
- Service availability, model availability, quota, capacity, networking, and data residency must all be checked before a fallback is accepted.

## Operational evidence

- Sweden Central Foundry deployed `gpt-5.6-terra` version `2026-07-09` as Global Standard capacity 100.
- Sweden Central rejected new Container Apps managed environments with `ManagedEnvironmentCapacityHeavyUsageError`.
- North Europe created the Container Apps environments but did not reliably create new ACA Sandboxes.
- The split deployment passed OpenClaw and Hermes `/invoke` validation with private MCP access on 2026-07-15.
