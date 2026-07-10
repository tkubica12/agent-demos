# A7 identity and MCP runbook

This runbook captures the repeatable first-time and recovery procedures for Agent Identity, Agent User, Work IQ Mail, private MCP, public MCP, and Agent 365 BYO registration.

## Authentication events to expect

| Event | Frequency | Identity |
| --- | --- | --- |
| Azure CLI login | Per workstation/session as needed | Azure operator; prefer `az login --use-device-code`. |
| Agent 365 Work IQ admin consent | Once per blueprint/tool permission set | Global Administrator in the target tenant. |
| BYO MCP admin approval | Once per external server registration | AI Administrator or Global Administrator in the target tenant. |
| Supported-client BYO connection | Per user/client consent lifecycle | Human user through the generated public client. |
| Runtime MCP calls | No interactive login | Sandbox managed identity federated into Agent Identity or Agent User. |

Normal reruns of the setup scripts should not request consent again.

## First-time sequence

1. Ensure both Agent 365 blueprints and instances exist.
2. Configure A7 for both runtimes:

   ```powershell
   uv run python -m scripts.setup_a7_identity --runtime openclaw
   uv run python -m scripts.setup_a7_identity --runtime hermes
   ```

3. Build and deploy each runtime:

   ```powershell
   uv run python -m scripts.build_images --runtime openclaw
   uv run python -m scripts.deploy_apps_runtime --runtime openclaw --apply --auto-approve

   uv run python -m scripts.build_images --runtime hermes
   uv run python -m scripts.deploy_apps_runtime --runtime hermes --apply --auto-approve
   ```

4. Recreate stale Sandboxes so new images and environment variables take effect:

   ```powershell
   uv run python -m scripts.demo_ops reset-sandbox --runtime openclaw --execute
   uv run python -m scripts.demo_ops reset-sandbox --runtime hermes --execute
   ```

5. Validate private MCP:

   ```powershell
   uv run python -m scripts.demo_ops smoke --runtime openclaw
   uv run python -m scripts.demo_ops smoke --runtime hermes --message "Use private-incidents MCP list_services."
   ```

6. Register the public BYO server from the OpenClaw workspace:

   ```powershell
   terraform -chdir=terraform\apps workspace select autopilot-openclaw
   uv run python -m scripts.register_a7_byo_mcp --runtime openclaw --dry-run
   uv run python -m scripts.register_a7_byo_mcp --runtime openclaw
   ```

7. Approve `ext_Shipments` in Microsoft 365 admin center:

   ```text
   Agents -> Tools -> Requests
   ```

8. Persist approval state:

   ```powershell
   uv run python -m scripts.register_a7_byo_mcp --runtime openclaw --mark-approved
   ```

## Idempotent reruns

`scripts.setup_a7_identity`:

- recovers the private/public MCP Entra applications by display name when `.local` state is absent;
- reuses service principals;
- reuses federated identity credentials and app-role assignments;
- detects Work IQ consent in `a365.generated.config.json` and skips interactive admin consent;
- re-ensures the Agent User delegated grant.

Use `--force-workiq-permissions` only when the Tooling manifest or blueprint permissions intentionally changed.

`scripts.register_a7_byo_mcp`:

- returns the recorded registration when local state exists;
- recovers an existing registration from Entra backing applications and the Agent 365 catalog when local state is absent;
- refuses `--force` when backing applications already exist, preventing orphan app registrations;
- automatically repairs required service principals, delegated grants, user assignments, and public-client configuration.

## Recovery commands

### Work IQ permission changed

```powershell
uv run python -m scripts.setup_a7_identity `
  --runtime openclaw `
  --force-workiq-permissions
```

### BYO consent fails

```powershell
uv run python -m scripts.register_a7_byo_mcp `
  --runtime openclaw `
  --repair-consent
```

Refresh the admin portal and retry approval after the repair.

### Wrong tenant account appears

Windows account broker can automatically choose an unrelated Microsoft account. Use an isolated/InPrivate browser profile and explicitly sign in with the target tenant administrator.

### BYO CLI silently cancels

The preview CLI asks `Proceed with registration?` and can exit with code 0 after receiving EOF. The repository script supplies confirmation and rejects output containing `Registration cancelled`.

### Work IQ CLI asks to provision a service principal

The repository script provisions missing Tooling resource service principals before running `a365 setup permissions mcp`, avoiding the interactive prompt.

### Custom MCP returns `invalid_token`

Entra v2 access tokens use the application client ID GUID in `aud`, even when the requested scope uses an `api://` URI. The MCP JWT verifier must validate the GUID audience.

### Sandbox token exchange fails TLS validation

Keep the Sandbox egress proxy enabled and set:

```text
SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
```

### BYO raw gateway tools are empty

The approved gateway accepts the generated public-client token and initializes, but a raw MCP client does not perform the supported-client connection/OAuth handshake. Use Copilot Studio, Visual Studio Code, Claude Code, or GitHub Copilot CLI for BYO tool invocation.

## Live smoke prompts

```text
List services from private incidents MCP.
Use public-shipments get_shipment_status for SHIP-1003.
Use Work IQ Mail to send a validation message from your Agent User mailbox.
```

## Evidence capture

After major identity changes:

```powershell
uv run python -m scripts.snapshot_system
```

The A7 implementation snapshot is under `.local\snapshots\20260710-143241Z`.
