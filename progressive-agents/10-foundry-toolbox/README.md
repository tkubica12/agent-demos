# Step 10: Foundry Toolbox

Step 10 replaces direct policy wiring with a versioned Foundry Toolbox. The
toolbox uses an `AgenticIdentityToken` RemoteTool connection to one HTTPS
FastMCP service and pins an immutable Foundry skill version.

## What this step proves

- Toolbox v1 and v2 are immutable; creating v2 does not change the default.
- The deployed agent uses only the unversioned consumer endpoint.
- V2 is accepted through its versioned endpoint before deliberate publication.
- `tool_search` discovers an opaque tool name and `call_tool` invokes that exact
  name; the agent does not construct separator conventions.
- `resources/list` and `resources/read` identify the pinned skill version.
- Publication changes both tool and skill behavior without an agent redeploy.
- Rollback to v1 preserves `ESC-CANARY-7742`; republishing v2 restores
  `ESC-CANARY-9921`.
- The sole business capability, `lookup_support_policy`, is read-only.
- Hosted Agent instance identity, not project-wide identity, receives the MCP
  API application role.
- Responses, cross-session memory, local skills, unrelated-skill exclusion,
  and 15 model-judged cases remain healthy.

## Architecture

```text
Foundry Hosted Agent v24
  |
  | authenticated MCPStreamableHTTPTool
  v
unversioned Toolbox MCP endpoint
  |-- tool_search / call_tool
  |-- pinned Foundry skill metadata
  `-- AgenticIdentityToken RemoteTool connection
        | audience api://<MCP API client ID>
        v
HTTPS ACA ingress
  `-- FastMCP Entra JWT verifier
        |-- tenant JWKS cache and key rotation
        |-- RS256 only
        |-- exact issuer and audience
        |-- exp / nbf
        |-- Mcp.Tools.ReadWrite.All
        |-- exact hosted-agent instance azp / oid
        `-- lookup_support_policy
```

`/health` is the only unauthenticated path. Anonymous or invalid MCP tokens
receive 401. Valid tokens with the wrong role or caller receive 403.

## Why Easy Auth is disabled

ACA Easy Auth was tested with the valid Entra Agent ID ServiceIdentity v2 token:

- exact GUID and `api://GUID` audiences;
- tenant v2 issuer matching live OIDC metadata;
- `allowedApplications` present and removed;
- direct `/mcp` and `/.auth/me` calls.

Every combination returned HTTP 401 before the request reached FastMCP. The
application therefore performs standards-based Entra JWT validation itself.
No token is decoded without signature verification, no principal headers are
trusted, and no key or client secret is stored.

## Skill compatibility boundary

The Toolbox preview exposes the pinned skill index to the Hosted Agent, but
remote `SKILL.md` reads fail for Agent ID ServiceIdentity principals because
those principals cannot receive Foundry Azure RBAC. The agent reads the signed
Toolbox index metadata and selects the exactly matching checked-in immutable
skill package. Both v1 and v2 packages ship in the agent image, so changing the
Toolbox default changes the selected package on the next session without a
redeploy.

## Important files

| Path | Purpose |
| --- | --- |
| `support_policy_mcp.py` | Policy tool plus strict Entra JWT authentication and authorization. |
| `toolbox/skills/v1`, `v2` | Self-contained immutable skill releases. |
| `scripts/toolbox_ops.py` | Non-destructive version creation and MCP evidence helpers. |
| `scripts/Provision-Step10.ps1` | Primary bootstrap, deployment, candidate, publish, rollback, smoke, and eval path. |
| `scripts/Promote-Toolbox-Step10.ps1` | Manual publication with rollback on deployed acceptance failure. |
| `infra/platform` | Terraform/azapi ACR, identities, roles, and ACA environment. |
| `infra/app` | Terraform/azapi MCP app, JWT configuration, HTTPS ingress, and disabled Easy Auth resource. |
| `eval.yaml` | Fifteen-case model-judged Hosted Agent evaluation. |
| `DEPLOYMENT_EVIDENCE.md` | Sanitized deployed resource and acceptance evidence. |

## Prerequisites

- Python 3.11-3.13 and `uv`.
- Terraform 1.7 or later.
- Azure CLI and Azure Developer CLI with authenticated sessions.
- `azure.ai.agents` 1.0.0-beta.4 or later.
- Microsoft Graph permission to create an application, service principal,
  application role, and role assignment.
- A Foundry project with `gpt-5.4-mini` and `text-embedding-3-large`.

## Local validation

```powershell
cd progressive-agents\10-foundry-toolbox
uv sync --frozen
uv run python smoke_mcp.py
uv run python smoke_skills.py
uv run python smoke_memory.py
```

`smoke_mcp.py` generates an RSA key and validates signature, algorithm,
issuer, audience, expiry, role, caller, and negative authorization cases.

## Primary classroom path

```powershell
azd env new 10-foundry-toolbox
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
.\scripts\Provision-Step10.ps1
```

The script:

1. validates local behavior;
2. provisions Terraform platform resources;
3. builds the MCP image from committed `uv.lock`;
4. creates the v2-token API app and app role;
5. bootstraps stable Hosted Agent identities;
6. deploys the JWT-protected MCP service with only the instance identity
   authorized;
7. creates the AgenticIdentity RemoteTool connection and skill v1/v2;
8. creates immutable Toolbox v1/v2 and explicitly keeps v1 default;
9. deploys and accepts a version-pinned v2 candidate;
10. deploys the final unversioned consumer agent;
11. publishes v2, runs deployed smoke and model-judged evaluation;
12. proves rollback to v1, verifies its canary, and republishes v2.

Any failed publication acceptance republishes v1.

## Manual publication

```powershell
.\scripts\Promote-Toolbox-Step10.ps1 `
  -Version 2 `
  -ExpectedCanary ESC-CANARY-9921
```

## Deployed result

- Toolbox default: v2.
- Hosted Agent: v24, unversioned consumer endpoint.
- Model-judged evaluation: 15/15 passed.
- MCP business tools: one read-only policy lookup; no write tools.
- Rollback proof: v2 -> v1 -> v2, with both pinned canaries verified.

## Preview limitations

- Toolbox, Toolbox Search, skills, and AgenticIdentity connections are preview.
- Toolbox does not enforce `require_approval`; Agent Framework owns approval.
  `never_require` is safe here because the only business tool is read-only.
- Toolbox MCP is streaming HTTP; ping and prompts/list are not acceptance gates.
- Agent Framework `header_provider` does not sign the initial MCP handshake in
  the installed build, so the agent uses an authenticated `httpx.AsyncClient`.
- Sweden Central lacked ACA capacity during deployment; the MCP app runs in East
  US 2 while Foundry and ACR remain in Sweden Central.

## Next capability

Step 11 publishes the existing Hosted Agent through Agent 365 and Teams.
