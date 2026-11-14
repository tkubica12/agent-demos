# Foundry isolation and quota

Independent stacks isolate simulator, gateway, runtime, identities, storage,
messaging and monitoring. Foundry can be per stack or explicitly shared.
Prefer a shared account/model deployment for a training cohort when regional
quota cannot support N independent reservations. Every runtime must have its
own role assignment on the shared account; do not share credentials.

Per-stack mode gives independent allocation and resource lifecycle, at the
cost of N capacity reservations. Shared mode pools capacity and reduces
fragmentation but shares throttling, model configuration and service failure
boundaries. Size the pool from measured concurrent demand and available
regional quota, not from a presumed tokens-per-second conversion.

Shared deployment configuration and lifecycle must be validated against the
selected source release. Never enable a configuration key unsupported by that
release, or remove shared resources while other stacks depend on them.

## Configuration

[The seven-stack template](../assets/stacks-seven-shared.example.toml) provides
six independent training stacks and an IT `agent-dev` canary. Copy it to
private `stacks.toml` instead of the initializer's two-stack per-stack example,
then replace all resource IDs. Add/remove explicit `[[stacks]]` for N stacks;
names are `agent-` followed by 1-3 lowercase alphanumeric characters. Always
select `--stack agent-dev` first for this template.

The deployment-wide key is `foundry_mode = "shared"` or `"per_stack"`
(default). It cannot be overridden per stack. In shared create mode:

```toml
foundry_mode = "shared"

[shared_foundry]
mode = "create"
existing_resource_group_name = "rg-training-foundry"
location = "swedencentral"
model_name = "gpt-6.1-sol"
model_version = "2026-09-29"
model_sku_name = "GlobalStandard"
model_capacity = 15
model_deployment_name = "gpt-6-1-sol"
```

The resource group must already exist and differ from every stack group.
`account_name` is optional; Private Endpoint subnet and Foundry DNS zone IDs
inherit from platform settings. `model_capacity` is one pool (default 15),
not 15 per stack. Projects remain per stack within the shared account.

For a customer-owned account use exactly:

```toml
foundry_mode = "shared"

[shared_foundry]
mode = "existing"
account_resource_id = "/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/rg-training-foundry/providers/Microsoft.CognitiveServices/accounts/training-models"
model_deployment_name = "gpt-6-1-sol"
```

The existing account must be in the configured subscription. Do not include
create-mode keys in existing mode. The deployer references the existing
account; it must not delete or relabel customer-owned Foundry resources.

In per-stack mode remove `[shared_foundry]` and size `model_capacity` per
stack. Review N times the capacity against available regional quota.
Foundry ownership mode is immutable within a captured environment/state
boundary, including after teardown. Do not change `foundry_mode` in that
environment's TOML and reuse its state.

## Per-stack demo canary before a shared deployment

A per-stack demo canary can prove its own deployment before shared/Entra
prerequisites are ready; it does not validate the shared path. After its
acceptance, tear down only that canary using the **original per-stack TOML**
and authoritative state:

```powershell
uv run python -m scripts.deploy_simulator_environment --config 'D:\customer-work\state\per-stack-demo.toml' --state-root 'D:\customer-work\state' --stack agent-dev destroy
```

This removes the canary's owned per-stack Foundry and other owned resources,
not its referenced existing resource group, DNS zones or subnet. Preserve
that state and teardown receipt.

Create separate shared inputs with a **new `environment` prefix**, for example
`training-pool` instead of `training`. The same top-level `--state-root` may
be used, but the generated stack directories must be disjoint:
`simulator-training-pool-agent-dev` instead of
`simulator-training-agent-dev`, plus `simulator-shared-training-pool`.
Do not copy, import or rewrite the old captured state into those directories.
Follow the shared stage order below with the new shared TOML. Actual
shared/Entra behavior and URL stability still require live acceptance.

## Stage order and permissions

From the deployment source, using private seven-stack inputs:

```powershell
$config = 'D:\customer-work\state\stacks.toml'
$state = 'D:\customer-work\state'
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --offline plan
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state plan
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state shared
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-dev all
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-dev verify
```

`--offline plan` performs no Azure reads and cannot prove prerequisites.
The normal plan performs read-only preflight. `shared` is deployment-wide:
**never pass `--stack`**. It provisions or references central Foundry before
stack `infrastructure`. In shared mode `all` also reconciles the central stage,
including when only a canary is selected; this is not a per-canary pool.
The shared state is `<state>\simulator-shared-<environment>`, with a separate
Terraform workspace and lock. Preserve it alongside all stack state.

The operator needs project creation and role-assignment write/delete rights
on the shared account, not only on stack resource groups. Equivalent custom
roles, or Contributor plus Role Based Access Control Administrator at the
account, can grant them. Missing/unreadable permissions produce
`azure-admin-request.json` in shared state and stop stack infrastructure.
Directory app permissions remain a separate [Entra requirement](authentication.md).

Shared teardown has two explicit steps. After authorization, destroy **each
actually provisioned stack** using its original shared inputs/state. For a
deployment with only the shared canary provisioned:

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-dev destroy
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state shared-destroy
```

Repeat stack `destroy` for any other provisioned stacks before
`shared-destroy`. Stack `destroy` never removes the shared pool.
`shared-destroy` accepts no stack selector and refuses when captured stack state
still has managed resources or unfinished identity teardown. In existing mode
it removes deployment references, never the customer-owned account/model.
In create mode it removes only its owned central resources, not the supplied
resource group or DNS zones. Do not delete state to bypass these guards.
