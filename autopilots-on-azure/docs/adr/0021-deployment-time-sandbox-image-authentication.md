# ADR 0021: Managed-identity Sandbox image conversion

## Decision

Convert private ACR images to Sandbox disk images at deployment time with the **native-v2 registry conversion**, authenticated by the target Sandbox Group's single user-assigned managed identity. No registry token, password, or admin credential exists anywhere in the flow.

The deployer submits `PUT {group}/diskimages/v2/async` with `source.kind = registry`, the digest-verified ACR tag in `imageUrl`, and the group identity's client ID in `managedIdentityClientId`, then polls `GET {group}/diskimages/operations/{operationId}`. The client uses the `https://management.azuredevcompute.io/.default` audience. SDK 0.1.0b4 does not expose this endpoint, so `scripts/sandbox_services.py` sends the request through the SDK client pipeline; its older typed `begin_create_disk_image` managed-identity path returned `401 RegistryAuthFailed` ([microsoft/azure-container-apps#1768](https://github.com/microsoft/azure-container-apps/issues/1768)).

Terraform grants `AcrPull` on the platform registry to the runtime, gateway, private-MCP, and public-MCP group identities. Before creating workloads, prepare **all four** ready disk images and pass only their IDs onward; gateway runtime startup uses `AGENT_RUNTIME_DISK_IMAGE_ID`. Direct runtime launch requires a prepared `--disk-image-id`.

## Alternatives and rationale

| Alternative | Decision |
| --- | --- |
| Native-v2 conversion with the group managed identity | Selected: real managed-identity conversion succeeded for the private images; no credential leaves Entra. |
| Deployer's short-lived token from `az acr login --expose-token` | Rejected: an explicit bearer-credential exception that is no longer needed. |
| Registry admin credentials or token broker/cache | Rejected: standing credentials or another privileged service. |

The tradeoff is that the four workload identities can now pull from the demo registry. That registry holds only this demo's images, and pull is read-only. ACR admin stays disabled. Failures stay explicit: a conversion that reports `Failed` (including a role assignment that has not propagated yet) stops the deployment with the service error and no credential fallback. Reuse matching ready disk images; new image content requires another deployment-time conversion.
