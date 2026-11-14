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
