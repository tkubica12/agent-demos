# ADR 0007: Support side-by-side autopilot deployments

## Status

Accepted.

## Context

OpenClaw and Hermes need to be compared and demonstrated independently. They should be able to run in the same Azure platform environment without sharing Agent 365 registration metadata, bridge app identities, runtime secrets, or sandbox state.

Most durable platform infrastructure is common: resource group, ACR, networking, ACA environments, private DNS, Foundry/Azure AI resources, SandboxGroup, RBAC, and private MCP services. The app layer is where runtime selection, branding, bot identity, bridge settings, and runtime image references differ.

A single bridge that dynamically routes multiple live runtimes would add routing, tenancy, secret, and UX complexity before Hermes parity is proven.

## Decision

Use one shared platform deployment and separate app deployments per autopilot instance.

Each autopilot app deployment has its own:

- `autopilot_name`.
- `agent_runtime`.
- bridge app name and managed identity.
- runtime image reference and runtime port.
- Agent 365 package metadata.
- runtime secrets.
- sandbox disk/image names.
- local generated configuration directory.

The first implementation should run `terraform\apps` separately for each autopilot instance, using distinct tfvars files such as `.local\openclaw\apps.tfvars` and `.local\hermes\apps.tfvars`.

A later refactor may convert the apps layer to a `for_each` map of autopilot deployments after both OpenClaw and Hermes are stable.

## Consequences

- OpenClaw and Hermes can run side by side without name, secret, bot, or sandbox disk collisions.
- A failure or redeploy of one runtime does not directly affect the other.
- Platform resources are reused rather than duplicated.
- Operators must choose the target autopilot instance when generating tfvars, packaging Agent 365 agents, deploying apps, and running validation.
- Documentation and scripts must make the instance/runtime context explicit in command output and local paths.
