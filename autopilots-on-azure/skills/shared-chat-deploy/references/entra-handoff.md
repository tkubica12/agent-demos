# Entra handoff

The deployer creates nothing in Entra in this mode. The admin needs the roles
Agent ID Administrator, Cloud Application Administrator and User Administrator.
The work comes in three handoffs because some values only exist after a
deployment stage.

## Handoff 1: before any deployment

Ask the admin to create these objects and send the IDs back.

Once for the whole deployment:

| Object | Settings | Value to send back | Goes to |
| --- | --- | --- | --- |
| Sign-in application | Single tenant. Application ID URI `api://<its application ID>`. Scope `access_as_user`, pre-authorized for itself. App roles `User` and `Admin` for users and groups. Access token version 2. Assignment required, with the chat users and admins assigned. | Application ID | `[shared_chat] client_id` |
| Channel application | Single tenant. Access token version 2. No secret, no certificate. | Application ID | `[shared_chat] channel_client_id` |

Once per stack:

| Object | Settings | Value to send back | Goes to |
| --- | --- | --- | --- |
| Agent blueprint | Owner and sponsor set. No secret, no certificate. | Blueprint application ID | `[stacks.agent365] blueprint_client_id` |
| Agent Identity | Created from that blueprint. | Object ID | `[stacks.agent365] agent_identity_id` |
| Agent User | Created with the Agent Identity as parent. Licences and delegated grants for the tools the role needs. | Object ID and UPN (user principal name) | `[stacks.agent365] agent_user_id`, `agent_user_principal_name` |

The Agent Identity ID is used both as the client ID and as the object ID of the
Worker. The same ID registers the agent in the chat.

## Handoff 2: after `infrastructure`

`infrastructure` creates the managed identities. Print their IDs:

```powershell
uv run python <skill folder>\scripts\shared_chat_state.py --config $config --state-root $state
```

Ask the admin to add federated credentials. Each one uses issuer
`https://login.microsoftonline.com/<tenant ID>/v2.0` and audience
`api://AzureADTokenExchange`.

| On | Subject | Count |
| --- | --- | --- |
| Channel application | `channel_identity_principal_id` of the host stack | 1 |
| Each blueprint | `gateway_identity_principal_id` of its stack | 1 per stack |
| Each blueprint | `runtime_identity_principal_id` of its stack | 1 per stack |

A stack whose gateway or runtime identity was replaced needs its credentials
updated. Compare the printed IDs with the admin's record after every
`infrastructure` run.

## Handoff 3: after the host stack is deployed

The chat URL contains a Sandbox ID and exists only after `deploy` of the host
stack. The deployer prints it and the state script repeats it as `redirect_uri`.
Ask the admin to add it to the sign-in application as a `Single-page
application` redirect URI, including the trailing `/`. Nobody can sign in to the
chat before this is done. Repeat the handoff whenever the chat URL changes.

## What the admin does not do

- No application per stack for the chat: the Agent Identity plays that role.
- No client secrets anywhere.
- No change to the customer's network, DNS or resource groups.
