# Rollout

Run from the initialized source directory prepared by the `autopilots-deploy`
skill, with the same private inputs, state root and operator login. All
identifiers below are placeholders. Stop on the first nonzero exit.

```powershell
$config = 'D:\customer-work\state\stacks.toml'
$state = 'D:\customer-work\state'
$skill = 'D:\customer-work\.agents\skills\shared-chat-deploy'
$chatHost = 'agent-1'
```

## 1. Validate the inputs

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --offline plan
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state plan
```

The deployer rejects a missing `[stacks.agent365]`, a host that is not a stack,
demo authentication, and two stacks that share a blueprint or Agent Identity.

## 2. Create or update the infrastructure

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $chatHost infrastructure
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state infrastructure
uv run python $skill\scripts\shared_chat_state.py --config $config --state-root $state
```

Only the host stack applies the chat infrastructure. Send the printed identity
IDs to the admin for [handoff 2](entra-handoff.md) and wait until the federated
credentials exist.

For a stack that already runs in the default mode, this step changes its
Terraform from the simulator channel to Agent 365. Its running gateway keeps the
old settings until `deploy`. Review the Terraform output of the first stack
before continuing with the others.

## 3. Deploy the host stack

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $chatHost images
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $chatHost deploy
uv run python $skill\scripts\shared_chat_state.py --config $config --state-root $state
```

`identities` is not needed; it only prints that there is nothing to create. Send
`redirect_uri` to the admin for [handoff 3](entra-handoff.md). Then run the
[verification](verification.md) for the host stack's agent before going on.

## 4. Deploy a second stack, then the rest

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-2 images
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-2 deploy
```

The second stack is the first proof that the chat reaches a gateway in another
resource group and that the gateway reaches the chat. Verify it with a real
message. Only then deploy the remaining stacks:

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-3 --stack agent-4 images
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-3 --stack agent-4 deploy
```

Every `deploy` rewrites the chat's list of agents from
`<state>\simulator-<environment>-<host>\registrations.json`, so earlier stacks
stay registered. When several stacks are selected, the host is processed first.

## After a chat image change

A new chat image can replace the chat Sandbox and change its URL.

1. Deploy the host stack.
2. Run the state script. Stacks with `"stale_chat_url": true` still point at the
   old URL.
3. Repeat [handoff 3](entra-handoff.md) with the new `redirect_uri`.
4. Deploy every stale stack.

## Failure handling

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `Deploy the chat host stack ... before ...` | Host has no chat URL yet | Deploy the host stack first |
| `deployment.lock` exists in the host state | Another deployment is writing the chat settings | Find its owner; remove the lock only when it is proven stale |
| Chat reports `Channel token acquisition failed` with `AADSTS500011` | `agent_identity_id` is not an Agent Identity in this tenant | Correct the input, redeploy the stack |
| `AADSTS70021` or `AADSTS700213` in chat or gateway logs | Federated credential missing or its subject differs from the printed identity ID | Repeat [handoff 2](entra-handoff.md) |
| Gateway answers 401 `Invalid simulator channel` | `channel_client_id` differs from the chat's channel application | Correct `[shared_chat]`, redeploy host and stack |
| Agent replies but tools fail with `AADSTS65001` | Delegated grant for the Agent User is missing | Admin adds the grant; no redeploy |
| No reply and no error in the chat | Private routing between chat and gateway | Check routes, DNS and firewall with the network team; do not switch to public access |

Keep the same state and ownership after a failure. Do not delete state, do not
edit the chat's registrations by hand, and do not add secrets.

## Teardown

Only after explicit approval. The deployer refuses to destroy the host while
other stacks are still registered.

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-2 destroy
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $chatHost destroy
```

A destroyed stack leaves the chat's running list unchanged until the next
`deploy` of any remaining stack. The Entra objects belong to the admin and are
not removed by the deployer.
