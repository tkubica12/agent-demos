# ADR 0007: Support side-by-side autopilot deployments

## Status

Accepted. Updated 2026-09-06 for distinct per-Worker/per-role Sandbox Groups and user-assigned identities. Hermes is the primary modernization target; the existing OpenClaw adapter is not being expanded.

## Context

OpenClaw and Hermes need to be compared and demonstrated independently. They should be able to run in the same Azure platform environment without sharing Agent 365 registration metadata, bridge app identities, runtime secrets, or sandbox state.

Shared platform infrastructure includes the resource group, ACR, networking, private-ingress Express environment, private DNS, and Foundry model/project. The app layer owns each Worker's separate runtime, gateway, private-MCP, public-MCP, and generated-app Sandbox Groups and user-assigned identities. Tool resource applications can be shared without sharing deployed service identities.

A single bridge that dynamically routes multiple live runtimes would add routing, tenancy, secret, and UX complexity before Hermes parity is proven.

## Decision

Use one shared platform deployment and separate app deployments per autopilot instance.

Each autopilot app deployment has its own:

- `autopilot_name`.
- `agent_runtime`.
- gateway Sandbox name and user-assigned managed identity.
- separate Sandbox Groups and user-assigned identities for each other service role.
- runtime image reference and runtime port.
- Agent 365 package metadata.
- runtime secrets.
- sandbox disk/image names.
- local generated configuration directory.

Run `terraform\apps` in a separate workspace per Worker, using distinct generated state such as `.local\hermes\apps\generated.app.auto.tfvars.json` and `.local\hermes2\apps\generated.app.auto.tfvars.json`. The gateway cannot auto-suspend while it owns detached post-ACK work and continuous queue receive.

## Consequences

- OpenClaw and Hermes can run side by side without name, secret, bot, or sandbox disk collisions.
- A failure or redeploy of one runtime does not directly affect the other.
- Platform resources are reused rather than duplicated.
- Operators must choose the target autopilot instance when generating tfvars, packaging Agent 365 agents, deploying apps, and running validation.
- Documentation and scripts must make the instance/runtime context explicit in command output and local paths.
