# Step 10 deployment evidence

Captured 2026-07-16. Identifiers are operational resource IDs, not credentials.
Tokens, tenant user details, and request authorization headers are omitted.

## Deployed identities and resources

| Item | Value |
| --- | --- |
| Resource group | `rg-step10-toolbox-673af34` |
| MCP Container App | `ca-step10-policy-673af34-eastus2` |
| MCP endpoint | `https://ca-step10-policy-673af34-eastus2.blueglacier-bfa16bf5.eastus2.azurecontainerapps.io/mcp` |
| MCP API client ID | `7aca035b-d2bc-42b8-912c-afa1b6eb9d00` |
| RemoteTool connection | `step-10-support-policy-mcp` / `AgenticIdentityToken` |
| Hosted Agent | `step-10-foundry-toolbox` version `13`, status `active` |
| Hosted identity principal | `cd6dfe0f-8f90-4bff-a1b3-55934d8e6e7d` |
| Hosted blueprint client | `384b4cc3-e7d6-4164-a361-6544826588f0` |
| Toolbox | `step-10-contoso-support` |
| Toolbox v1 | version `1`, skill `step-10-escalation-policy@1` |
| Toolbox v2 | version `2`, skill `step-10-escalation-policy@2` |
| Active toolbox version | `1` |

## Passing evidence

- MCP `/health`: `status=ok`, one business tool
  `lookup_support_policy`.
- Anonymous MCP initialize: HTTP `401`.
- Easy Auth: enabled, `Return401`, tenant v2 issuer, exact observed token
  audience `7aca035b-d2bc-42b8-912c-afa1b6eb9d00`, `/health` excluded.
- Easy Auth application allowlist contains only the Hosted Agent instance and
  blueprint client IDs.
- API service principal: `appRoleAssignmentRequired=true`.
- API role: `Mcp.Tools.ReadWrite.All`,
  `471715d9-3e5b-4756-89d2-2e2bf58aed7e`.
- Hosted instance and blueprint app-role assignments target resource service
  principal `fca627e3-e327-4dd2-b4d6-765ab59358dd`.
- Direct Azure RBAC assignment is unsupported for the Agent ID principals:
  the instance ServiceIdentity returns HTTP 500 and the blueprint returns
  `PrincipalTypeNotSupported`. No success-shaped RBAC fallback is used.
- RemoteTool connection contains target and `AgenticIdentityToken`; no secret or
  key is returned.
- Toolbox v1 and v2 are immutable and v1 remains `default_version`.
- V2 metadata records the meaningful `prolonged-delivery escalation` change.
- Hosted Agent environment uses the unversioned consumer endpoint.
- Superseded Hosted Agent diagnostic versions 1-12 were deleted after v13 became active.

## Blocking acceptance evidence

The installed `azure.ai.connections` 1.0.0-beta.2 CLI is hard-coded to ARM
`2025-04-01-preview` and exposes no explicit instance/blueprint fields.
The latest provider API accepts those names only as free-form metadata, and
Toolbox ignores them for direct caller context.

The Hosted Agent now signs the initial Toolbox MCP handshake with an
authenticated `httpx.AsyncClient`. Toolbox returns HTTP 200, resolves the
AgenticIdentity connection, and attempts upstream `tools/list`.

The Hosted Agent console proves the token minted for the MCP API:

```text
aud   = 7aca035b-d2bc-42b8-912c-afa1b6eb9d00
azp   = cd6dfe0f-8f90-4bff-a1b3-55934d8e6e7d
iss   = https://login.microsoftonline.com/6ce4f237-667f-43f5-aafd-cbef954adf97/v2.0
ver   = 2.0
roles = [Mcp.Tools.ReadWrite.All]
```

ACA Easy Auth rejects that same token on a direct initialize request and rejects
the brokered Toolbox request:

```text
HTTP 401 Unauthorized
WWW-Authenticate: Bearer realm="<container-app-host>"
```

Controlled authorization isolation produced the same 401 when:

1. `defaultAuthorizationPolicy.allowedApplications` was removed;
2. `allowedAudiences` contained both the observed GUID and `api://<GUID>`;
3. `openIdIssuer` exactly matched the issuer in live tenant OIDC metadata;
4. the same token called `/.auth/me`, which also returned 401.

The strict instance/blueprint client allowlist was restored after isolation.

Because candidate acceptance failed, the promotion gate left v1 active. V2 was
not published, deployed behavior was not claimed, and model-judged evaluations
were not run against a success-shaped fallback.

## Evidence commands

```powershell
Invoke-RestMethod https://<mcp-app>/health
az containerapp auth show -g rg-step10-toolbox-673af34 -n ca-step10-policy-673af34-eastus2
azd ai connection show step-10-support-policy-mcp --show-credentials
azd ai toolbox show step-10-contoso-support --version 1 --output json
azd ai toolbox show step-10-contoso-support --version 2 --output json
azd ai agent show step-10-foundry-toolbox --output json
azd ai agent monitor step-10-foundry-toolbox --tail 100 --utc
uv run python scripts\toolbox_ops.py inspect --endpoint <version-2-endpoint>
```
