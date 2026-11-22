# Role packages and role-owned MCP servers

This runbook is written for a coding agent that deploys one Worker with its own role (soul and skills) and its own MCP (Model Context Protocol) server. Follow the steps in order. Commands run from `autopilots-on-azure`.

Read [DEPLOYMENT.md](../../DEPLOYMENT.md) first for prerequisites, logins, and the Worker state layout. Use [identity-mcp.md](identity-mcp.md) when a token or consent step fails.

## What gets deployed

| Part | Source | Deployed by |
| --- | --- | --- |
| Role: `SOUL.md`, `skills/role`, `distribution.yaml`, `config.yaml` | a role folder under `roles/` | `scripts.setup_app_tfvars`, then `scripts.deploy_apps_runtime` |
| Role-owned MCP server | a Python project under `roles/` with `pyproject.toml` and `uv.lock` | `scripts.setup_role_mcp`, then `scripts.deploy_apps_runtime` |
| Power BI (read-only, optional) | Microsoft Fabric IQ MCP | `powerbi_mcp_url` in the Worker settings |

Nothing is built as a container image. `scripts/application_releases.py` zips the server's Python package, its data directories, and a `requirements.txt` exported from `uv.lock`. `scripts/sandbox_services.py` installs that archive in a second Sandbox of the Worker's `private-mcp` Sandbox Group and starts `python -m <package>.server` on port 8765.

The runtime never calls the server directly. It calls the local identity proxy (`autopilots_identity/mcp_proxy.py`), which adds the Worker's Agent Identity token. The server admits only that Agent Identity with the required application role, so one Worker cannot use another Worker's tools.

## Contract for a role-owned MCP server

The server must:

1. be a `uv` project with a committed `uv.lock`;
2. start with `python -m <package>.server` and listen on `HOST`/`PORT`;
3. serve MCP on `/mcp`;
4. validate the bearer token from `MCP_JWKS_URL`, `MCP_JWT_ISSUER`, `MCP_JWT_AUDIENCE`, `MCP_REQUIRED_ROLES`, `MCP_ALLOWED_CLIENT_IDS`, and `MCP_ALLOWED_OBJECT_IDS`. `private-incidents-mcp` is the reference implementation.

## Steps

### 1. Place the role package

Put the role package inside the deployment source, under `roles/` (ignored by git):

```powershell
git clone <role-package-repository> roles
```

Symbolic links are refused. The folder must be a real copy.

### 2. Select the Worker

```powershell
$worker = "<worker state name>"
$workspace = "<terraform workspace>"
```

A new Worker is created with `scripts.worker_lifecycle bootstrap` as described in DEPLOYMENT.md, "Fresh Worker bootstrap". Terraform provisions the `private-mcp` Sandbox Group whenever `demo_mcp_enabled` is true (the default for the `agent365` channel) or `role_mcp_servers` is non-empty, so `demo_mcp_enabled` may be set to `false` when the demo incidents and shipments servers are not needed. The simulator channel has no Agent Identity and cannot use role-owned MCP servers.

### 3. Point the Worker at its role

```powershell
uv run python -m scripts.setup_app_tfvars `
  --state-name $worker --autopilot-name $worker `
  --role-blueprint <role_blueprint from distribution.yaml> `
  --role-blueprint-source local --role-blueprint-path roles/<role folder> `
  --role-release <role_release from distribution.yaml> `
  --role-release-commit (git -C roles rev-parse HEAD)
```

The role folder must hold only role files: every file in it is bundled into the runtime, and symbolic links (for example a `.venv`) are refused. Keep the MCP server in a sibling folder.

With `--role-blueprint-source local` the script records the role bundle digest. Rerun this command after every change to the role files; a stale digest stops the runtime.

### 4. Register the MCP server

Write one TOML file per Worker from [role-mcp.example.toml](../role-mcp.example.toml), then:

```powershell
uv run python -m scripts.setup_role_mcp --state-name $worker --config <file>
```

The script creates one Entra application per server, grants `required_role` to the Worker's Agent Identity and `gateway_roles` to the gateway identity, and writes `role_mcp_servers` into the Worker settings. It needs a login that may register applications and assign application roles. Without that right, an administrator creates the application and assignments by hand and the `api_audience` is added to `role_mcp_servers` in `.local\<worker>\apps\generated.app.auto.tfvars.json`.

### 5. Optional: Power BI

Add to `.local\<worker>\apps\generated.app.auto.tfvars.json`:

```json
"powerbi_mcp_url": "https://fabriciq.svc.cloud.microsoft/v1/mcp/fabriciq"
```

The Worker's Agent User must be allowed to read the reports in Power BI. The runtime skill `powerbi-reports` tells the agent how to use it.

### 6. Deploy

```powershell
uv run python -m scripts.deploy_apps_runtime `
  --state-name $worker --workspace $workspace --apply --auto-approve --capture
```

### 7. Verify

```powershell
uv run python -m scripts.demo_ops status --state-name $worker
```

Then check, in this order:

1. `.local\<worker>\apps\terraform-outputs.json` has `role_mcp_<name>_url` for every server.
2. `sandbox_services` in the same file lists every server name.
3. Ask the Worker in chat to list the tools of the server. A tool list proves the proxy, token, and role assignment.

## Failures

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Role MCP servers require the private-mcp Sandbox Group` | Terraform `apps` has not been applied since adding `role_mcp_servers` | run step 6 with `--apply` |
| `The <name> release is missing: ...` | wrong `source_directory`, `package`, or `data_directories` | correct the TOML, rerun step 4 |
| `Local role bundle_sha256 is missing or differs` | role files changed after step 3 | rerun step 3 |
| Tools missing, server healthy | Agent Identity lacks the application role | rerun step 4 with a login that may assign roles |
| Tool needs another system and gets 401 | Agent User has no access there, or the header scope is wrong | fix access or `agent_user_token_headers` |
