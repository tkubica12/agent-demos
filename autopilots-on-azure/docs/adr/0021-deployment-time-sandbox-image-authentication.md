# ADR 0021: Deployment-only ACR token for Sandbox image conversion

## Decision

Use an explicitly approved short-lived Entra-derived ACR token for deployment-time OCI-to-disk conversion while SDK 0.1.0b4 managed-identity conversion returns `401 RegistryAuthFailed`. The related upstream issue is [microsoft/azure-container-apps#1768](https://github.com/microsoft/azure-container-apps/issues/1768).

The deployer obtains the credential through `az acr login --expose-token`. The conversion SDK receives it in `RegistryCredentials`; this is an explicit bearer credential, not native managed-identity conversion.

Before creating workloads, prepare **all four** ready disk images: runtime, gateway, private MCP, public MCP. Pass only their IDs onward; gateway runtime startup uses `AGENT_RUNTIME_DISK_IMAGE_ID`. Direct runtime launch requires a prepared `--disk-image-id`.

## Alternatives and rationale

| Alternative | Decision |
| --- | --- |
| Native managed-identity conversion | Preferred when the real conversion path succeeds; currently blocked. |
| Deployer's short-lived registry token | Selected: bounded credential lifetime and no workload credential dependency. |
| Registry admin credentials or token broker/cache | Rejected: standing credentials or another privileged service. |

ACR admin remains disabled. Runtime/gateway neither acquire credentials nor convert images, and workload identities receive no registry roles. Keep token values out of logs, command arguments, files, Terraform state, and workload settings. Reuse matching ready disk images; new image content requires another deployment-time conversion, not token renewal in a running service.

Reconsider after a native managed-identity conversion succeeds for the actual private images. Until then, do not silently fall back or describe this path as credential-free.
