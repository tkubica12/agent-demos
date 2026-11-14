# Authentication and switching

## Entra prerequisites

Supply `tenant_id`, `owner_id` and authoritative `[[assignments]]` entries
(`principal_id`, `principal_type = "User"` or `"Group"`, `role = "User"` or
`"Admin"`). Omitting assignments grants the owner Admin; `assignments = []`
denies everyone. Azure resource rights do not grant Microsoft Graph rights.

The identities stage creates and reconciles three ordinary single-tenant
applications/service principals per one-agent stack: simulator SPA/API,
channel and agent. It configures `api://<SPA client ID>/access_as_user`,
`requestedAccessTokenVersion = 2` on SPA, channel and agent applications,
User/Admin app roles, enterprise application
assignment required and the actual simulator HTTPS URL **with trailing `/`**
as SPA redirect URI. It uses managed-identity federated credentials for
channel/gateway/runtime trust instead of secrets. It does not implement
Entra Agent ID or request Agent 365 permissions.

App-only channel tokens must also be v2: the gateway rejects v1 issuer/version
claims. Use a release whose identity reconciler sets this on non-SPA resource
apps too; rerunning `identities` repairs existing owned registrations. A v2
token endpoint alone does not establish the resource application's token version.

Application Administrator or Cloud Application Administrator may be needed;
tenant policy and delegated Graph consent are also relevant. The manifest
requests only its listed operations: Application.ReadWrite.All,
AppRoleAssignment.ReadWrite.All for assignments and User.Read.All/Group.Read.All
for principal lookups. Do not assume the Azure CLI token has those permissions.
The simulator API requests its own delegated scope; do not copy unrelated
Microsoft Graph User.Read/openid/profile grants from older instructions.

On Graph HTTP 403 the stage stops and writes
`<state>\simulator-<environment>-<stack>\identities.admin-request.json`.
Give the reviewed manifest and exact package/config/state boundary to an Entra
admin. The admin signs in with device code and reruns `identities`. It is a
conditional plan with reference-linked Graph calls, not a script to execute
blindly. Keep the captured original deployer principal unchanged.

`deploy` also reconciles identities using the real hosting URL. Therefore
directory authority is needed **during Entra deploy too**, particularly for a
first deployment whose URL was not known at the identities stage. An admin may
need to perform that stage with the same preserved state, then restore the
operator login. Never solve this by deleting state or changing RBAC identity.

## Demo prerequisites

Set `auth_mode = "demo"`, `demo_acknowledge = "test-data-only"` and
`demo_expires_at` to a future UTC timestamp at most 30 days away. No app
registration is required. `prepare` generates per-stack access codes and
per-agent channel keys in private `secrets.json`. Restrict filesystem access
to state; never put access codes or keys in the customer TOML, ZIP or Git.
Anyone holding a code may choose a name; this is not a real user identity.
Do not use real customer data, and do not expose this mode publicly.

## Switch a deployed canary

Use the same source, digest release, configuration path, `environment`,
stack names, state root and operator identity. Stop new traffic and preserve
the existing verification receipts as historical evidence outside the current
acceptance path. Do not reuse Entra browser evidence from before the switch.
Run the following from the initialized `autopilots-on-azure` source directory:

```powershell
$config = 'D:\customer-work\state\stacks.toml'
$state = 'D:\customer-work\state'
$stack = 'agent-1'
```

**Demo to Entra:** edit TOML to `auth_mode = "entra"`, remove
`demo_owner_display_name` if present and configure assignments. Expiry and
acknowledgement settings are no longer used.

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack plan
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack identities
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack deploy
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack verify
```

The last command fails until fresh assigned-user browser acceptance exists.
First check actual URL/redirect, login, logout, denied unassigned access and
the full [browser checks](verification.md).

**Entra to demo:** make the three demo TOML settings above explicit, then:

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack plan
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack deploy
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $stack verify
```

Entra app registrations remain owned and stored for a later switch or teardown;
demo does not delete them. Remove old browser sessions and confirm actual
runtime mode, demo expiry and access-code behavior.

This is **config plus stage execution**, not manually changing environment
variables in the portal. `deploy` reapplies worker Terraform, activates the
gateway/runtime service settings and atomically writes the simulator service
config. With unchanged image, it reuses the owned simulator Sandbox and URL.
With changed image, it can delete/recreate the Sandbox: check the new URL,
redirect, private DNS and channel service URL; do not promise a stable URL.
No `infrastructure` or `images` stage is required for an auth-only switch
when their existing state is intact. These are source-verified commands;
live acceptance is a separate gate.
