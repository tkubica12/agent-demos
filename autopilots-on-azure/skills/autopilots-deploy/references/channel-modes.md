# Gateway channel boundary

The simulator speaks Bot Framework Activity protocol. There is no simulator
fork of the agent; authentication and tool capability are configuration-driven.
The following reflects `bridge/channel_auth.py` and `scripts/simulator_channel.py`.

| Environment selection | Gateway mode | Trust and tools |
| --- | --- | --- |
| `USE_AGENTIC_AUTH=true` (also `1`/`yes`), no demo settings | `agent365` | Agent 365 authentication; agentic capabilities require their own configured identities/services |
| Agentic off; `CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID`, tenant, single `<channelAppId>/.default` scope and `CONNECTIONSMAP` | `standard` | Validated Entra application tokens; issuer, tenant, caller and mapped serviceUrl enforced; no agentic activities/M365 collaboration tools |
| Agentic off; no service CLIENTID; `DEMO_CHANNEL_KEY` (at least 32 URL-safe ASCII chars), `DEMO_SERVICE_URL=https://<simulator>/demo/<key>` | `demo` | Constant-time `X-Demo-Channel-Key` check and exact serviceUrl; fake users; no Entra/agentic tools |
| No agentic flag, client ID or demo settings | `anonymous` | Not an acceptable customer deployment; stop and inspect configuration |

Conflicting demo and agentic/service-client settings fail startup; malformed
standard settings fail instead of becoming anonymous.

For simulator stacks the deployer sets `USE_AGENTIC_AUTH=false`, removes
Agent365/WorkIQ/service-connection settings from the previous environment,
sets `ATTACHMENT_DOWNLOAD_ORIGINS` to the simulator origin and disables
document retry, demo MCP and scheduling. Standard mode adds
`AUTHTYPE=FederatedCredentials`, agent CLIENTID, TENANTID,
`FEDERATEDCLIENTID` (gateway managed identity), `SCOPES__0` and an origin-only
serviceUrl mapping. Demo sets only its per-agent channel key/service URL.
The simulator receives `TSIM_AUTH_MODE` and either tenant/SPA/channel identity
settings or acknowledgement/access code/expiry settings.

## Verify the running mode, not just the desired TOML

`/health` is not proof of channel mode. Inspect the selected gateway's active
service configuration via authenticated Sandbox management and classify the
above non-secret fields; never print DEMO_CHANNEL_KEY, serviceUrl containing
the key, tokens or whole configuration files. Compare deployment source/image
receipts with the actual running release. Demo logs a test-data-only warning.
Use the simulator's real authenticated chat path to prove delivery; a missing
or invalid demo header/application token must be rejected. Standard mode also
rejects agentic activities. A passing endpoint health check alone proves none
of these authentication checks.

For a selected stack, run this read-only inspection from the deployment source.
It prints only non-secret selectors, not the full configuration:

```powershell
@'
import json
import sys
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.containerapps.sandbox import SandboxGroupClient
from scripts.sandbox_services import DISK_IMAGE_AUDIENCE, SERVICE_CONFIG_PATH

root = Path(sys.argv[1])
host = json.loads((root / "hosting.json").read_text())
worker = json.loads((root / sys.argv[2] / "outputs.json").read_text())
binding = worker["sandbox_groups"]["gateway"]
with AzureCliCredential(subscription=host["subscription_id"]) as credential:
    with SandboxGroupClient(
        host["management_endpoint"], credential,
        subscription_id=host["subscription_id"],
        resource_group=worker["resource_group_name"], sandbox_group=binding["name"],
        audience=DISK_IMAGE_AUDIENCE,
    ) as group:
        active = json.loads(group.get_sandbox_client(worker["bridge_sandbox_id"]).read_file(SERVICE_CONFIG_PATH))
env = active["environment"]
prefix = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__"
print(json.dumps({
    "USE_AGENTIC_AUTH": env.get("USE_AGENTIC_AUTH", ""),
    "service_client_id": env.get(prefix + "CLIENTID", ""),
    "tenant_id": env.get(prefix + "TENANTID", ""),
    "scope": env.get(prefix + "SCOPES__0", ""),
    "connection_mapping_present": bool(env.get("CONNECTIONSMAP__0__CONNECTION")),
    "demo_key_present": bool(env.get("DEMO_CHANNEL_KEY")),
    "demo_key_length": len(env.get("DEMO_CHANNEL_KEY", "")),
    "demo_service_url_present": bool(env.get("DEMO_SERVICE_URL")),
}, indent=2))
'@ | uv run python - 'D:\customer-work\state\simulator-training-agent-1' 'agent-1'
```

This inspects the active service config file, not process memory. Confirm its
health/restart activation and real authenticated behavior before calling the
running channel verified.

## Shared chat with Agent 365 Workers

By default every stack owns its chat and three Entra applications. Add
`[shared_chat]` when one chat should serve every stack and the Workers need
their Agent 365 identity (Agent User, Work IQ tools). The stacks keep their own
resource group, subnet, gateway and runtime. The `shared-chat-deploy` skill has
the full procedure: admin handoffs, rollout order and verification.

```toml
[shared_chat]
host = "agent-1"                                           # stack whose resource group runs the one chat
client_id = "<sign-in application ID>"                     # created by the Entra admin
channel_client_id = "<channel application ID>"             # created by the Entra admin

[[stacks]]
name = "agent-1"
[stacks.agent365]
blueprint_client_id = "<blueprint application ID>"
agent_identity_id = "<Agent Identity object ID>"
agent_user_id = "<Agent User object ID>"
agent_user_principal_name = "<Agent User UPN>"
```

Every stack then needs `[stacks.agent365]` and `auth_mode = "entra"`. The deployer:

- creates no Entra application; `identities` is a no-op and the admin creates the
  sign-in application, the channel application, and each blueprint, Agent Identity
  and Agent User beforehand;
- applies the chat infrastructure and Sandbox only in the host stack;
- deploys every gateway with `USE_AGENTIC_AUTH=true`, the blueprint as service
  connection, and the shared chat as `SIMULATOR_CHANNEL_CLIENT_ID` /
  `SIMULATOR_SERVICE_URL`;
- keeps one entry per stack in `<host state>/registrations.json` and writes the
  complete list to the chat on every stack deployment, so stacks never drop each
  other's agents. The registration `clientId` is the Agent Identity ID.

Order and limits:

- Deploy the host stack first; other stacks stop with an explicit error until the
  host has a chat URL. `all` over several stacks orders the host first.
- The channel application's federated credential subject is the host stack's
  `bindings.json` `channel.identity_principal_id`. Each blueprint needs federated
  credentials for its stack's `agents.<name>.identity_principal_id` (gateway) and
  `runtime_identity_principal_id`.
- After the host deployment, give the admin the printed redirect URI for the
  sign-in application.
- A changed chat image can change the chat URL. Redeploy every other stack
  afterwards; their gateways keep the old URL until then.
- Scheduling, Dreaming, document retry and generated chat files stay off. The
  Worker is applied with `native_transport_enabled = false`, so no Service Bus
  namespace or DNS zone is needed.
- The chat must reach every gateway and every gateway the chat over the private
  network; workload egress must also allow the Work IQ and Entra endpoints. The
  deployer does not verify cross-stack routing.
- A stack that ran `infrastructure` before joining keeps its unused chat Sandbox
  Group and channel identity until its teardown. Destroy the host stack last.
- One chat is one point of failure for all stacks.
