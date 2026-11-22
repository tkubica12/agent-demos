---
name: shared-chat-deploy
description: Deploy one shared Agent Chat Simulator for many independent stacks that run as Agent 365 Workers with Entra objects created by the customer's admin. Use when stacks deployed with autopilots-deploy must share a single chat and keep their Agent User tools such as mail, SharePoint, Azure DevOps or Power BI.
---

# Shared chat deployment

Use this skill when several stacks (one resource group, gateway and runtime
each) must talk to users through one chat and act under their own Agent User.
It builds on the `autopilots-deploy` skill: that skill prepares the source,
image release, private inputs and state; this one changes how the chat and the
identities are wired. Install the complete folder at
`.agents/skills/shared-chat-deploy/` next to `autopilots-deploy`.

## Decide whether this mode fits

| | Default stacks (`autopilots-deploy`) | Shared chat (this skill) |
| --- | --- | --- |
| Chats | One per stack | One, in the host stack |
| Entra applications for chat | Three per stack, created by the deployer | Two in total, created by the admin |
| Agent identity | Ordinary app registration | Blueprint, Agent Identity and Agent User |
| Tools under the agent's own account | No | Yes |
| Scheduling, document retry, generated chat files | Off | Off |
| Failure of the chat | One agent | All agents |

Do not use it for demo authentication, for a single stack, or when the stacks
must stay fully isolated from each other.

## Establish the contract

Before any Azure write, confirm with the customer:

1. The host stack: the one stack whose resource group runs the chat.
2. The Entra admin who creates the objects, and that they accept the
   [handoff](references/entra-handoff.md). The deployer creates no Entra
   application in this mode.
3. Private network routing: the chat must reach every gateway and every
   gateway must reach the chat. The deployer does not verify this.
4. The source release supports the mode. From the initialized source:

   ```powershell
   uv run python -c "from scripts.deploy_simulator_environment import validate_shared_chat"
   ```

   An import error means an older release; stop and select a newer one.

## Deploy

1. Give the admin the first [handoff](references/entra-handoff.md): the two chat
   applications and, per stack, a blueprint, Agent Identity and Agent User.
2. Fill `[shared_chat]` and one `[stacks.agent365]` per stack in the private
   inputs, starting from the [example](assets/stacks-shared-chat.example.toml).
3. Run `plan`, then `infrastructure` for every stack. This creates the managed
   identities; it changes each gateway to Agent 365 mode in Terraform only.
4. Print the identity IDs with the [state script](scripts/shared_chat_state.py)
   and give the admin the second handoff: federated credentials.
5. Deploy the host stack first, send the admin the printed redirect URI, then
   verify one more stack before the rest. Commands and order are in
   [rollout](references/rollout.md).
6. Prove each agent with a real message; see
   [verification](references/verification.md). Health checks alone prove nothing
   about identity.

## Stopping rules

- Never deploy a stack before the host has a chat URL; the deployer refuses it.
- Never put a client secret on a blueprint or a chat application to get past a
  missing federated credential. Fix the credential.
- Do not edit `registrations.json` or the chat's service configuration by hand.
  Redeploy the stack whose entry is wrong.
- A changed chat URL makes every other gateway stale. Redeploy all stacks before
  telling users the chat works again.
- Destroy the host stack last. Ask for explicit approval before any teardown.
- Request approval for Azure writes and for changes to customer identities
  unless already delegated. Never print secrets or export access tokens.

## Known limitations

Verified by unit tests only: no deployment of this mode against Azure has been
recorded. Private routing between the chat and gateways in other resource groups
is unproven. Scheduling, Dreaming, document retry and generated chat files stay
off, because the Worker runs without the native scheduling transport. A stack
that ran `infrastructure` before joining keeps an unused chat Sandbox Group and
channel identity until its teardown.
