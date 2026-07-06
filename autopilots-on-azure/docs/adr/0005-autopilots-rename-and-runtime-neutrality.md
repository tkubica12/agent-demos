# ADR 0005: Rename the project around runtime-neutral autopilots

## Status

Accepted.

## Context

The project started as OpenClaw-on-Azure and initially used OpenClaw-specific names across documentation, scripts, Terraform variables, image names, bridge prompts, Teams metadata, and generated local configuration.

The next implementation track adds Nous Research Hermes as a second runtime. Hermes should not be treated as a fork of the project or as a separate Azure hosting stack. Most Azure infrastructure, Teams/Agent 365 integration, bridge routing, and private MCP plumbing should be common.

Keeping OpenClaw as the project-level name would create the wrong abstraction: it implies that Azure resources, bridge code, and Teams packages are OpenClaw-owned even when the selected runtime is Hermes.

## Decision

Rename the project concept to `autopilots-on-azure`.

Use neutral project-level names for common components:

- `autopilot` for the deployed assistant instance.
- `agent_runtime` or `runtime` for the selected implementation.
- `bridge` for the Microsoft 365 and HTTP boundary.
- `runtime` or runtime-specific names for OpenClaw and Hermes container code.

Keep OpenClaw names only where the code or configuration is genuinely OpenClaw-specific, such as the OpenClaw Gateway websocket protocol, device approval, OpenClaw runtime image, or OpenClaw bootstrap files.

## Consequences

- Documentation and operator flows describe OpenClaw and Hermes as peer runtimes.
- Common Terraform and bridge resources should not use OpenClaw-specific variable names unless they are runtime-specific.
- Existing OpenClaw deployments can be treated as legacy environments; new deployments should use the neutral naming.
- The rename is intentionally done before the Hermes implementation to avoid carrying mixed terminology through new code.
- Future runtimes can be added without another project-level rename.
