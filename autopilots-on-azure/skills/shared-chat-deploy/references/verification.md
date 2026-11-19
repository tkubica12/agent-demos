# Verification

Keep evidence private and tied to the stack, UTC time, source commit and chat
URL. A check proves only what its row says.

| Check | Proves | Does not prove |
| --- | --- | --- |
| State script | Every stack has identities, a registration and the current chat URL on disk | That Entra or the running services match |
| Gateway settings inspection | The running gateway is in Agent 365 mode and points at the shared chat | That tokens are issued or messages arrive |
| Real message in the chat | Sign-in, delivery both ways, the agent's identity and its tools | Other stacks; each one needs its own message |

## 1. State on disk

```powershell
uv run python $skill\scripts\shared_chat_state.py --config $config --state-root $state
```

Expected for every deployed stack: `"registered": true` and
`"stale_chat_url": false`. `redirect_uri` must equal the redirect URI the admin
set on the sign-in application.

## 2. Running gateway settings

Read-only. It prints selected non-secret settings of one stack's gateway:

```powershell
@'
import json
import sys
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.containerapps.sandbox import SandboxGroupClient
from scripts.sandbox_services import DISK_IMAGE_AUDIENCE, SERVICE_CONFIG_PATH

host_root, stack_root, stack = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
host = json.loads((host_root / "hosting.json").read_text())
worker = json.loads((stack_root / stack / "outputs.json").read_text())
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
print(json.dumps({
    "USE_AGENTIC_AUTH": env.get("USE_AGENTIC_AUTH", ""),
    "NATIVE_TRANSPORT_ENABLED": env.get("NATIVE_TRANSPORT_ENABLED", ""),
    "blueprint": env.get("AGENT365_BLUEPRINT_CLIENT_ID", ""),
    "agent_identity": env.get("AGENT365_AGENT_IDENTITY_CLIENT_ID", ""),
    "agent_user": env.get("AGENT365_AGENT_USER_PRINCIPAL_NAME", ""),
    "simulator_channel": env.get("SIMULATOR_CHANNEL_CLIENT_ID", ""),
    "simulator_url_matches_chat": env.get("SIMULATOR_SERVICE_URL", "") == host["url"],
    "demo_key_present": bool(env.get("DEMO_CHANNEL_KEY")),
}, indent=2))
'@ | uv run python - 'D:\customer-work\state\simulator-training-agent-1' 'D:\customer-work\state\simulator-training-agent-2' 'agent-2'
```

Expected: `USE_AGENTIC_AUTH` is `true`, `NATIVE_TRANSPORT_ENABLED` is `false`,
the three identity values match `[stacks.agent365]`, `simulator_channel` matches
`[shared_chat] channel_client_id`, `simulator_url_matches_chat` is `true` and
`demo_key_present` is `false`. The first path is the host stack's state folder,
the second the inspected stack's.

## 3. Real message per agent

An assigned user signs in to the chat from the private network with normal TLS
(Transport Layer Security) validation, opens a conversation with the agent and
asks which identity and tools it has.

Pass:

- the reply arrives in the chat;
- the agent names its own Agent User UPN (user principal name);
- the agent lists its Work IQ tools and one read-only tool call succeeds, for
  example listing SharePoint sites.

Repeat for every stack. One working agent says nothing about the others: each
has its own blueprint, federated credentials, grants and network path.

Also check once: a user without an assigned role cannot sign in, and a second
stack's deployment did not remove the first stack's agent from the chat.

## The deployer's `verify` action

`verify` still expects a manual browser receipt per stack at
`<state>\simulator-<environment>-<stack>\browser-acceptance.json`, with the
shared chat URL as `url`. Write it only after the checks above were observed for
that stack. Never export tokens to produce evidence.

## Rollout holds

Missing reply, a reply under the wrong identity, a tool error, or a stack that
disappears from the chat are holds for the whole rollout, not warnings. Capture
the stack, time and redacted logs, and stop.
