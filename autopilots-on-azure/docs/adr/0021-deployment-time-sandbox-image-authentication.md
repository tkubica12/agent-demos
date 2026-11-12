# ADR 0021: Managed-identity Sandbox image conversion

## Decision

Convert the private runtime dependency image from ACR to a Sandbox disk image at deployment time with **native-v2 registry conversion**, authenticated by the runtime Sandbox Group's user-assigned managed identity. Gateway and MCP services convert a pinned public Python image directly. No registry token, password, or admin credential exists.

The deployer submits `PUT {group}/diskimages/v2/async` with `source.kind = registry`, the digest-verified ACR tag in `imageUrl`, and the group identity's client ID in `managedIdentityClientId`, then polls `GET {group}/diskimages/operations/{operationId}`. The client uses the `https://management.azuredevcompute.io/.default` audience. SDK 0.1.0b4 does not expose this endpoint, so `scripts/sandbox_services.py` sends the request through the SDK client pipeline; its older typed `begin_create_disk_image` managed-identity path returned `401 RegistryAuthFailed` ([microsoft/azure-container-apps#1768](https://github.com/microsoft/azure-container-apps/issues/1768)).

Terraform grants `AcrPull` only to the runtime identity. Before creating workloads, prepare all four group-scoped disk images and pass their IDs onward; runtime startup uses `AGENT_RUNTIME_DISK_IMAGE_ID`. Gateway and MCP identities need no registry permission.

Application source is a separate content-addressed release. Thin-service dependencies are prepared from locked approved-feed wheels and reused by requirement/base-image digest. Runtime source reuses the installed dependency image and verifies its dependency lock. Routine Python changes therefore need neither an ACR build nor another image conversion.

## Alternatives and rationale

| Alternative | Decision |
| --- | --- |
| Native-v2 conversion with the group managed identity | Selected: real managed-identity conversion succeeded for the private images; no credential leaves Entra. |
| Public pinned image plus verified source release | Selected for gateway and MCP services; avoids three custom image builds and their registry grants. |
| Deployer's short-lived token from `az acr login --expose-token` | Rejected: an explicit bearer-credential exception that is no longer needed. |
| Registry admin credentials or token broker/cache | Rejected: standing credentials or another privileged service. |

The runtime identity can pull from the demo registry; pull is read-only and ACR admin stays disabled. ACR remains justified by LibreOffice, compiled .NET helpers, Node packages, and sealed Hermes dependencies. Moving that preparation into every startup would not simplify deployment.

Failures remain explicit: a failed conversion or unpropagated role assignment stops deployment without a credential fallback. Reuse matching ready disk images. A changed dependency image requires conversion; a changed application release does not.
