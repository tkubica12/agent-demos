# Step 10: Foundry Toolbox

Step 10 copies step 09 and replaces the direct policy function with one
versioned Foundry Toolbox. The toolbox references an Entra-protected FastMCP
service through an `AgenticIdentityToken` project connection and pins an
immutable Foundry skill version.

## Status

The infrastructure, MCP service, Entra application, hosted agent, connection,
skills, and toolbox versions are deployed. Toolbox v1 remains the explicit
default. V2 was deliberately not published because ACA Easy Auth rejects the
valid v2 Entra Agent ID token with HTTP 401.
See [DEPLOYMENT_EVIDENCE.md](DEPLOYMENT_EVIDENCE.md).

Do not update the progressive-agents roadmap to mark step 10 complete until the
documented platform failure is resolved and the full acceptance path passes.

## What this step proves

- The MCP container is built from the committed `uv.lock`.
- `/health` is separately usable, while unauthenticated `/mcp` returns 401.
- ACA Easy Auth is configured with the tenant v2 issuer, the observed GUID
  token audience, the two agent-specific client IDs, and `Return401`.
- The API service principal requires app-role assignment.
- The Hosted Agent identity, not the project-wide identity, has the
  `Mcp.Tools.ReadWrite.All` application role.
- A `RemoteTool` connection uses `agentic-identity` with the
  `api://<client-id>` token-request audience; it stores no key or secret.
- Toolbox v1 and v2 are immutable and pin skill versions 1 and 2.
- Creating v2 does not implicitly change `default_version`.
- Promotion validates the versioned candidate first and republishes the prior
  default on failure.
- The deployed agent points only at the unversioned consumer endpoint, so a
  successful future publication changes behavior without an agent redeploy.

## Architecture

```text
Foundry Hosted Agent v13
  |
  | Entra token for https://ai.azure.com/.default
  v
unversioned Toolbox MCP consumer endpoint
  |-- tool_search / call_tool
  |-- pinned Foundry skill resources
  `-- RemoteTool project connection
        | AgenticIdentityToken
        | audience api://<MCP API app client ID>
        v
ACA Easy Auth -> FastMCP /mcp -> lookup_support_policy (read-only)

GET /health bypasses Easy Auth for readiness only.
```

The agent treats discovered tool names as opaque. It invokes the exact name
returned by `tools/list` or `tool_search` and retries cold discovery with the
exact capability `lookup_support_policy` rather than constructing separator
conventions.

## Repository layout

| Path | Purpose |
| --- | --- |
| `support_policy_mcp.py` | One real read-only FastMCP policy capability. |
| `Dockerfile.mcp` | Locked MCP image build using `pyproject.toml` and `uv.lock`. |
| `toolbox/skills/v1`, `v2` | Meaningfully different escalation skill packages. |
| `scripts/toolbox_ops.py` | Immutable version creation, MCP acceptance, promotion, and rollback. |
| `scripts/Provision-Step10.ps1` | Primary setup, deploy, validation, and promotion path. |
| `scripts/Register-Step10Entra.ps1` | Repeatable v2 API app and required application role. |
| `infra/platform` | Terraform/azapi ACR, identities, roles, and ACA environment. |
| `infra/app` | Terraform/azapi MCP Container App and Easy Auth. |
| `infra/main.bicep` | Resource-free azd state adapter around Terraform outputs. |

## Prerequisites

- Python 3.11-3.13 and `uv`.
- Terraform 1.7 or later.
- Azure CLI and Azure Developer CLI with authenticated sessions.
- `azure.ai.agents` extension 1.0.0-beta.4 or later.
- Microsoft Graph permission to create an application, service principal, app
  role, and app-role assignment.
- An existing Foundry project with `gpt-5.4-mini` and
  `text-embedding-3-large`.

## Local validation

```powershell
cd progressive-agents\10-foundry-toolbox
uv sync --frozen
uv run python smoke_mcp.py
uv run python smoke_skills.py
uv run python smoke_memory.py
```

Run the local MCP service:

```powershell
uv run python support_policy_mcp.py
```

Local in-process validation verifies that only `lookup_support_policy` exists,
the policy canary is returned, no write-like business tool exists, and the two
skill releases differ meaningfully.

## Provision and deploy

```powershell
azd env new 10-foundry-toolbox
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
.\scripts\Provision-Step10.ps1
```

The script uses two explicit Terraform roots. It applies the platform root,
builds the locked MCP image in ACR, then applies the app root. Entra and Foundry
registrations remain explicit imperative steps because they are data-plane or
directory operations. The final acceptance gate stops before publication on
any failure.

To retry an already-created candidate manually:

```powershell
.\scripts\Promote-Toolbox-Step10.ps1 `
  -Version 2 `
  -ExpectedCanary ESC-CANARY-9921
```

That command validates the versioned endpoint before publishing. If candidate
or consumer acceptance fails after the update, it restores the previous
`default_version`.

## Required acceptance

The step is complete only when all of these pass:

1. unauthenticated upstream MCP returns 401;
2. authenticated initialize, `tools/list`, policy call, `resources/list`, and
   `resources/read` succeed through toolbox v2;
3. no write business tool is present;
4. v2 acceptance succeeds before publication;
5. the consumer endpoint resolves v2 after publication;
6. the pinned v1 endpoint still returns release-1 skill content;
7. local and deployed agent responses select the policy tool and exclude
   unrelated skills;
8. inherited Responses, memory, skills, and model-judged evaluations pass.

In the recorded deployment, item 2 fails during `tools/list`; all later items
remain intentionally unclaimed.

## Preview boundaries

- Toolbox, Toolbox Search, skills, and AgenticIdentity connections are preview
  surfaces and can change.
- The toolbox proxy currently does not enforce `require_approval`; Agent
  Framework owns approval policy. `never_require` is used only because the sole
  business capability is read-only.
- Toolbox MCP is streaming HTTP. The smoke path does not use ping or
  prompts/list as correctness gates.
- The installed connection CLI and public ARM schema expose only auth type,
  audience, and free-form metadata. Supplying the instance/blueprint IDs as
  metadata does not affect connection resolution.
- Agent Framework `header_provider` does not sign the initial MCP handshake in
  the installed version. This step uses an authenticated `httpx.AsyncClient`
  so every Toolbox request is signed.
- The hosted identity token is verified in console evidence as v2 with audience
  `7aca035b-d2bc-42b8-912c-afa1b6eb9d00`, agent instance `azp`, tenant v2
  issuer, and `Mcp.Tools.ReadWrite.All`.
- Azure RBAC cannot be assigned directly to these Agent ID principals in the
  validated tenant: the instance ServiceIdentity returns HTTP 500 and the
  blueprint returns `PrincipalTypeNotSupported`. Foundry runtime access remains
  platform-managed; downstream MCP authorization uses Entra app roles.
- Toolbox accepts that hosted identity token and resolves the RemoteTool
  connection. Both the brokered request and a direct request from the Hosted
  Agent then receive HTTP 401 from ACA Easy Auth. The same result occurs with
  `allowedApplications` removed, both audience forms configured, and an issuer
  exactly matching live OIDC metadata. This is the current narrow platform
  blocker.
- Sweden Central rejected new ACA environments due regional capacity, so the
  MCP Container App runs in East US 2 while Foundry and ACR remain in Sweden
  Central.

## Next capability

Step 11 is Agent 365 and Teams publication, but it must not start until this
step's Toolbox acceptance and deliberate v2 promotion complete.
