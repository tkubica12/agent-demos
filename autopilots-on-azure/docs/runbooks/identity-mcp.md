# Identity and MCP operations

Use [DEPLOYMENT.md](../../DEPLOYMENT.md) for the staged existing-Worker update and isolated owner login. Commands below run from `autopilots-on-azure`; select an existing Worker:

```powershell
$worker = "hermes2"
```

## Authority

| Operation | Identity |
| --- | --- |
| Azure resources | Deployment operator |
| Blueprint consent | Authorized tenant administrator |
| Endpoint update | Existing direct blueprint owner, checked before mutation |
| BYO registration approval | AI Administrator or Global Administrator |
| Supported-client BYO connection | Client user's OAuth consent |
| Runtime custom MCP | Worker Agent Identity |
| Worker-owned Microsoft 365 | Fixed Agent User |

Autonomous work does not inherit the invoking human's access. Runtime/gateway federation credentials are separate; network reachability does not replace resource authorization.

## Reconcile an existing Worker

```powershell
uv run python -m scripts.setup_identity --state-name $worker
Push-Location ".local\$worker\agent365"
try { a365 query-entra inheritance } finally { Pop-Location }
```

The helper reuses discovered applications/service principals, federation, and role assignments; requests missing Tooling permissions; and ensures Agent User delegated grants. Every listed resource must show effective inheritance. Apply the resulting tfvars before service deployment.

For an intentional permission change requiring consent reconciliation:

```powershell
uv run python -m scripts.setup_identity `
  --state-name $worker --force-workiq-permissions
```

Use `--skip-workiq-permissions` only for a Worker without those Microsoft 365 scenarios. Registry UI summaries may lag; check inheritance and a real tool call.

## Public shipments BYO

Direct runtime access uses Agent Identity. The separate BYO path registers the public endpoint for supported clients:

```powershell
uv run python -m scripts.register_byo_mcp --state-name $worker --dry-run
uv run python -m scripts.register_byo_mcp --state-name $worker
```

Approve the returned server under **Microsoft 365 admin center → Agents → Tools → Requests**, then record that approval:

```powershell
uv run python -m scripts.register_byo_mcp --state-name $worker --mark-approved
```

The helper recovers existing registrations/backing applications rather than duplicating them and repairs service principals, delegated grants, user assignments, and client configuration. Do not force a new registration over existing backing applications.

## Troubleshooting

| Symptom | Check/action |
| --- | --- |
| Graph CAE or expired login | Complete a new `az login --use-device-code` in the intended tenant; restart an expired device-code attempt. |
| Wrong consent account | Use an isolated browser profile and confirm tenant/account before approval. |
| Endpoint update blocked | Read-only preflight requires direct ownership. Do not grant ownership or switch the Terraform login as a workaround. |
| BYO consent failure | Run `scripts.register_byo_mcp --state-name $worker --repair-consent`, then retry approval. |
| BYO initializes but exposes no tools | Use a supported client with its connection/OAuth handshake; raw MCP initialization is insufficient. |
| Custom MCP `invalid_token` | Entra v2 audience is the resource application client ID, even when the requested scope uses `api://`. Check issuer, tenant, audience, Worker binding, and role independently. |
| Private MCP unreachable | Check Group VNet attachment, linked Express environment, Private Endpoint, and DNS. Do not publish the endpoint to bypass routing. |
| Sandbox TLS trust failure | Keep egress inspection enabled; use the system CA bundle below. |
| Tool works for operator but not Worker | Check Agent Identity/Agent User grants and actual selected principal, not human browser access. |

Runtime TLS settings:

```text
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
```

## Live checks

```powershell
uv run python -m scripts.demo_ops smoke --state-name $worker `
  --message "Use private incidents to list services and public shipments to list demo shipments." `
  --timeout 600
uv run python -m scripts.document_smoke --state-name $worker --timeout 1200
```

Require actual tool results under Worker identity. Keep tokens, private tool payloads, generated identity files, and consent caches out of committed diagnostics. Remove the Word smoke artifact after validation.
