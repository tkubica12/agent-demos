# Step 10 deployment evidence

Captured 2026-07-16. Identifiers are operational resource IDs, not credentials.
Tokens, tenant user details, and request authorization headers are omitted.

## Deployed identities and resources

| Item | Value |
| --- | --- |
| Resource group | `rg-step10-toolbox-673af34` |
| MCP Container App | `ca-step10-policy-673af34-eastus2` |
| MCP endpoint | `https://ca-step10-policy-673af34-eastus2.blueglacier-bfa16bf5.eastus2.azurecontainerapps.io/mcp` |
| MCP API client ID | `df66acde-3339-4659-af7e-3480d114f9ab` |
| RemoteTool connection | `step-10-support-policy-mcp` / `AgenticIdentityToken` |
| Hosted Agent | `step-10-foundry-toolbox` version `4`, status `active` |
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
- Easy Auth: enabled, `Return401`, tenant v2 issuer, audience
  `api://df66acde-3339-4659-af7e-3480d114f9ab`, `/health` excluded.
- API service principal: `appRoleAssignmentRequired=true`.
- API role: `Mcp.Tools.ReadWrite.All`,
  `dcc3431c-3ac1-487f-9bc3-6eba3ea86930`.
- Hosted identity app-role assignment:
  principal `cd6dfe0f-8f90-4bff-a1b3-55934d8e6e7d` to resource service
  principal `db0cea7d-93e2-4e48-8aae-093a10fb49f3`.
- RemoteTool connection contains target and `AgenticIdentityToken`; no secret or
  key is returned.
- Toolbox v1 and v2 are immutable and v1 remains `default_version`.
- V2 metadata records the meaningful `prolonged-delivery escalation` change.
- Hosted Agent environment uses the unversioned consumer endpoint.

## Blocking acceptance evidence

Authenticated MCP `initialize` reaches the versioned Toolbox v2 endpoint.
`tools/list` then fails before publication:

```text
Connection resolution failed for 'step-10-support-policy-mcp'.
HTTP status: BadRequest.
Reason: AgenticIdentityToken auth type for connection
'step-10-support-policy-mcp' requires AgentInstanceClientId and
AgentBlueprintClientId, or an ApplicationName.
```

The same failure surfaces from Hosted Agent Responses as:

```text
response.failed
server_error: MCP server failed to initialize
```

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
uv run python scripts\toolbox_ops.py inspect --endpoint <version-2-endpoint>
```
