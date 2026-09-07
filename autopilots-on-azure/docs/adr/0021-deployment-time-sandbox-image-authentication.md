# ADR 0021: Deployment-time image conversion with short-lived Entra credentials

- Status: Accepted; deployment-time token conversion and both existing Workers live-verified
- Date: 2026-09-06

## Context

Two different image-authentication paths existed before the modernization:

| Operation | Historical credential mechanism | What earlier success proves |
| --- | --- | --- |
| Classic bridge/private-MCP/public-MCP Container App image pull | Native managed-identity integration with ACR | Those classic ACA workloads could pull their images using MI. |
| Sandbox runtime image conversion | ACR admin username/password supplied through `RegistryCredentials` | Explicit registry credentials worked for that conversion; this was not an MI-only conversion test. |

The previous `scripts/sandbox_runtime.py` at commit `8868a5e` confirms the distinction:
`ensure_agent_sandbox` obtains the username and `passwords[0].value` with
`az acr credential show` when credentials are not already supplied, then constructs
`RegistryCredentials`. Logging in to Azure CLI to retrieve that password does not
make the subsequent registry authentication a managed-identity or short-lived-token
flow. The credential presented to the conversion service was the ACR admin password.

The all-Sandbox architecture requires disk-image conversion for gateway, runtime,
and both MCP services. Direct managed-identity conversion returned HTTP 401
`RegistryAuthFailed` with the attached user-assigned identity and its `AcrPull`
assignment. Both SDK 0.1.0b3 and 0.1.0b4 were attempted. The public issue
[microsoft/azure-container-apps#1768](https://github.com/microsoft/azure-container-apps/issues/1768)
describes a known SDK problem; the reviewed public status had no fix as of
September 4. SDK 0.1.0b3 with `managedIdentityResourceId`, SDK 0.1.0b4, and direct
REST with `managedIdentityClientId` failed in the current investigation.
This does not establish whether every remaining failure is SDK-only.

## Options considered

1. Direct managed-identity image conversion: preferred native contract, but
   blocked by the reproduced failure. Do not silently retry with another identity.
2. Restore the historical ACR admin password: technically the same credential class
   as before, but it restores a shared, non-expiring-until-rotated registry credential
   with pull and push access. The user initially authorized this option, then
   selected the short-lived-token alternative below. Admin-password restoration
   is therefore not the accepted final implementation.
3. Short-lived Entra-derived registry token during deployment: the user-approved
   alternative, not the historical credential mechanism.
4. Return the services to classic ACA: outside the accepted all-Sandbox direction;
   it would abandon the agreed
   all-Sandbox compute direction to work around one provisioning operation.

## Decision

Following the explanation of the historical credentials, the user approved
short-lived Entra ACR tokens on September 6, 2026, strictly for deployment-time
conversion. The deployment helper prepares disk images before changing workloads.
Runtime startup requires `AGENT_RUNTIME_DISK_IMAGE_ID`; runtime and gateway never
acquire ACR credentials or perform image conversion. Do not restore the ACR admin
account, add a token renewal daemon, or fall back to another authentication path.

The first live private-MCP conversion on September 6, 2026, reached `Ready`
with SDK 0.1.0b4 and a short-lived Entra token. The completed helper subsequently
prepared gateway, runtime, private-MCP, and public-MCP disk images for both existing
Workers, `hermes` and `hermes2`. Both gateways, runtimes, and all four MCP services
were deployed. Real native Entra-authenticated model inference and private/public
MCP calls succeeded; these are separate application-level observations, not
inferences from a successful image conversion.

Repeated MCP deployment reused the same Sandbox IDs without requesting a registry
login. A stopped runtime also resumed on the same ID with its DataDisk retained.
The ACR admin account remains disabled, and obsolete Worker `AcrPull` assignments
were removed. No native MI-only conversion fix, token renewal/cache service,
stored workload registry credential, or fresh-identity Worker bootstrap is claimed.

## Deployment-time flow and credential boundary

1. The deployer uses an existing Azure CLI Entra login. It does not read
   either ACR admin password.
2. `az acr login --expose-token` returns an Entra-derived registry credential
   without requiring a Docker daemon. Microsoft documents registry login tokens
   as valid for three hours.
3. The conversion request still contains explicit `RegistryCredentials`:
   the documented all-zero username and the token in the password field.
   That is not a native MI-only conversion request and is not credential-free.
4. The token represents the deployer's authorized registry access, not
   automatically the Sandbox Group UAMI. Short lifetime does not narrow a
   broadly privileged deployer's permissions.
5. The conversion service receives the bearer credential over HTTPS to fetch
   the image. Validation must verify that no token is written to
   application logs, command arguments, Terraform variables/state, generated files,
   or gateway/runtime settings. Azure CLI keeps its normal login cache separately.
6. A completed conversion produces a prepared Sandbox disk-image ID.
   Service creation/resume uses that ID without another ACR login; a new source
   image requires fresh deployment-time conversion. Expiry during an unfinished
   conversion remains an explicit failure, not an admin-password fallback.

Using the same `RegistryCredentials` field does not make the old and selected
credentials equivalent. The old field held a registry-wide admin password;
the selected flow supplies a time-limited, identity-authorized bearer token.
The latter still exposes a usable credential to the conversion service and still
requires verified identity, scope, storage, and lifecycle boundaries.

## Validation gates

- Confirm the intended deployer and least required ACR read / Sandbox conversion permissions.
- Keep the ACR admin account disabled and avoid any token cache, renewal daemon,
  credential broker, or automatic authentication fallback.
- Bind the source tag to the intended digest and require a `Ready` disk-image result.
- Verify that token values are absent from persisted deployment and workload state.
- Convert before changing workloads so a failed conversion does not replace
  existing services with an unusable configuration.
- Prove runtime/gateway launch and resume use only prepared disk-image IDs, without
  registry authentication or image conversion in their execution path.
- Separately validate service startup, ingress, private MCP access, native model
  inference, and identity. A successful conversion would prove none of those alone.

## Return to the native contract

Follow the upstream issue and weekly SDK dependency updates. A supported
SDK/service version must actually convert the private image using the intended
managed identity with no explicit registry credentials before the native path is
called verified. Record any actual live results for the accepted interim flow
separately from the historical admin-password mechanism.

## Sources

- [Sandbox managed-identity conversion issue](https://github.com/microsoft/azure-container-apps/issues/1768)
- [ACR Entra authentication and exposed tokens](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-authentication#use-az-acr-login-without-docker-daemon)
- [Previous runtime conversion code](https://github.com/tkubica12/agent-demos/blob/8868a5e8e4b181744f34e5039d301f8bd493e5fa/autopilots-on-azure/scripts/sandbox_runtime.py)
