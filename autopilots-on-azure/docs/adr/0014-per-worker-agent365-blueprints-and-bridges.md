# ADR 0014: Keep Agent 365 platform blueprints and bridges isolated per Worker

## Status

Accepted for the current implementation. Revisit when Worker count or platform capabilities justify a shared routing layer.

## Context

Autopilots uses two different concepts that both include the word *blueprint*:

- A **Role Blueprint** is the Git-backed definition of a job. Many Workers can use the same Role Blueprint and Role Release.
- An **Agent 365 platform blueprint** is a Microsoft 365 application object containing endpoint registration, inherited permissions, credentials, and packaging configuration.

`hermes` and `hermes2` both use the `junior-project-manager` Role Blueprint and Role Release 3.1.0. The current runtime architecture gives each Worker an isolated bridge, Terraform workspace, Data Disk, Sandbox, API key, approval identity, Agent Identity, and Agent User. Because the Agent 365 messaging endpoint is registered on the platform blueprint, each isolated bridge currently requires a separate Agent 365 platform blueprint.

This raises a future architecture choice:

1. Keep one Agent 365 platform blueprint and bridge per Worker.
2. Use one Agent 365 platform blueprint and a shared bridge that routes activities to Worker-specific Sandboxes and state.
3. Add an indirection layer in front of per-Worker bridges.

We do not have enough operational evidence to make the permanent scaling decision.

## Decision

Continue with one isolated Agent 365 platform blueprint and bridge per Worker for the current multi-Worker demonstration.

The platform blueprint is a deployment envelope, not the Worker role definition. Collective Learning Review groups Workers by `roleBlueprint`, `roleRelease`, and release commit, regardless of which Agent 365 platform blueprint or bridge delivered their activities.

Do not implement a shared multi-Worker bridge during the current A10 validation. Record it as a future option and reconsider it only against explicit scale, cost, routing, and isolation requirements.

## Current isolated model

Each Worker owns:

- Agent 365 platform blueprint and service principal;
- messaging endpoint and bridge Container App;
- bridge managed identity and SDK credentials;
- Agent Identity and Agent User;
- Terraform workspace;
- Sandbox Data Disk and Worker profile;
- API-server key and Collective Learning approval key pair;
- diagnostics and captured operator state.

Workers can still share:

- one Role Blueprint and Role Release;
- Foundry project and model deployment;
- Sandbox Group and platform networking;
- Azure Container Registry images;
- private and public MCP application APIs;
- Collective Learning Review tooling.

## Benefits of per-Worker isolation

- Direct and inspectable mapping from Agent User to bridge, Sandbox, volume, and Worker state.
- Small authorization and privacy blast radius.
- Independent deployment, rollback, diagnostics, throttling, and cleanup.
- No custom routing registry in the bridge.
- No risk of one Worker selecting another Worker's memory or Data Disk.
- Easier demonstration of independent learning and provenance.

## Costs of per-Worker isolation

- Repeated Agent 365 platform blueprints, service principals, credentials, permission inheritance, and consent.
- Repeated bridge, managed identity, private MCP, and public MCP Container Apps in the current Terraform layout.
- More Terraform workspaces and local operator state.
- Higher Azure and administrative overhead as Worker count grows.
- Role Blueprint and Agent 365 platform blueprint terminology can be confused.
- Shared fixes require coordinated deployment across several bridges.

## Potential shared-bridge model

A shared bridge would receive activities for several Agent Users and resolve a Worker configuration from trusted activity identity. The registry would select:

- Worker ID and assignment;
- Agent Identity and Agent User;
- Sandbox labels, Data Disk, and API key;
- Role Blueprint and Role Release;
- approval public key and learning state;
- per-Worker limits, diagnostics, and lifecycle policy.

Potential benefits:

- One Agent 365 platform blueprint, consent surface, and messaging endpoint for many Workers.
- Fewer Container Apps and credentials.
- Centralized lifecycle, routing, observability, and policy.
- Better alignment with Agent 365's blueprint-to-many-instances model.

Potential costs and risks:

- A custom security-critical Worker router.
- Larger failure and privacy blast radius.
- Stronger requirements for tenant, Agent User, and conversation-to-Worker validation.
- Noisy-neighbor, concurrency, and scaling concerns.
- More complex secret and configuration storage.
- More complex Worker-specific Agent Identity injection and Sandbox selection.
- Shared deployment failures can affect every Worker.
- Per-Worker rollback and debugging become less direct.

## Reconsideration triggers

Reopen this decision when one or more are true:

- more than a small number of Workers are operated continuously;
- duplicated Container Apps or platform blueprints create meaningful cost or consent burden;
- Agent 365 exposes supported per-instance endpoint or routing metadata;
- a Worker registry and administration plane is required for other reasons;
- centralized throttling, policy, or observability becomes more valuable than isolation;
- operational evidence shows per-Worker deployment is too slow or error-prone;
- security review demonstrates a shared router can preserve equivalent isolation.

## Consequences

- `hermes2` receives its own Agent 365 platform blueprint and bridge.
- `hermes` and `hermes2` still share the same Junior Project Manager Role Blueprint and Role Release.
- Documentation must qualify **Role Blueprint** versus **Agent 365 platform blueprint**.
- Multi-Worker Collective Learning Review can proceed without deciding the long-term bridge topology.
