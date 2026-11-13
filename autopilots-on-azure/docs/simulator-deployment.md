# Agent Chat Simulator deployment

This flow deploys six fully independent stacks (`agent-1` through `agent-6`),
each with its own resource group, Autopilots platform, persistent Agent Chat
Simulator, gateway, and Hermes Worker. No Foundry account, model deployment,
network, registry, monitoring, simulator, or channel audience is shared.
The channel is not Microsoft Teams: no Agent 365 blueprint, Agent Identity,
Agent User, `a365`, publishing, Work IQ, or Microsoft 365 licensing is involved.
Azure DevOps and Clarity MCP integration and Entra Agent ID are phase 2.
Phase 1 uses only standard secretless Entra application registrations.

## Architecture and isolation

In each stack, the browser signs in to its simulator SPA/API. A separate channel application
authenticates activities to each Worker's gateway; the gateway uses its own
standard Entra application and federates through its UAMI for replies. Each
Worker also has an isolated Hermes runtime, managed identity, durable home
backups, owner, and generated-app group. Runtime inference uses its UAMI and
Foundry RBAC directly, not the channel's identity.

Everything runs on ACA Sandboxes. The simulator mounts one single-writer 5 GiB
Data Disk at `/mnt/tsim`, retaining SQLite and files across image replacement.
It stays running: automatic suspend and delete are disabled. The Data Disk
requires Disk mode; this is not a claim of Memory suspend support with a Data
Disk. Worker runtime and gateway retain their existing Memory-suspend policy.
The platform's private-ingress environment uses ACA Express, never classic ACA.
Demo incidents/shipments services are not deployed for simulator Workers.
Native scheduling connector namespaces are also omitted; interactive chat and
its durable conversation journal do not require a connector or a queue receiver.

The entry point copies only tracked Terraform configuration and lockfiles into
`.local\simulator-<environment>-agent-N\{platform,simulator,agent-N}`. Each root selects a
non-default workspace named `<environment>-agent-N`. Every Worker references the
absolute path to its own stack's platform state. Neither the original
`terraform\platform` default state nor `.local\hermes`, `.local\hermes2`, their
active tfvars, resources, or Agent 365 objects is used.

Keep each complete `.local\simulator-<environment>-agent-N` folder in a secure operator
location. It contains Terraform state, DEMO keys, API keys, and signing keys.
Do not commit it or send it to an LLM. Back it up before changing operator
machines; losing local state is not a supported fresh-deployment procedure.
Only one operator may deploy a given environment at a time.

## Customer prerequisites

Install Azure CLI, Terraform >=1.7, and `uv`. Use the committed `uv.lock` and
approved feed; no dependency upgrade is needed. The simulator source must be
present at `chat-simulator\`, including its Dockerfile.

Provide:

| Input | Meaning |
| --- | --- |
| `environment` | New 3-14 character lowercase prefix, not an existing environment |
| `subscription_id`, `tenant_id` | Customer Azure subscription and Entra tenant |
| `owner_id` | Worker owner's Entra user object ID |
| `stack_count` | Defaults to six; each stack has exactly one simulator and Worker |
| `assignments` | User/group Entra object IDs and `User` or `Admin` role |
| `role` | Public/reachable Role Blueprint repository, path, release, full immutable commit |
| `model_capacity` | Capacity per stack (default 15); explicit `stacks` can override each |
| `platform` | Optional regions/model deployment settings and adequate total model quota |
| `auth_mode` | `entra` by default; `demo` only for temporary test data |

Azure permissions: create resources in the new resource group, assign Azure
roles, create custom role definitions, build/import ACR images, and use
SandboxGroup data-plane operations. Subscription Owner suffices; alternatively
combine Contributor, Role Based Access Control Administrator (including the
custom-role operations), and SandboxGroup Data Owner at the appropriate scopes.
Register the providers used by the existing platform, including Microsoft.App,
Microsoft.Web, Microsoft.CognitiveServices, Microsoft.ContainerRegistry,
Microsoft.ManagedIdentity, Microsoft.Network, Microsoft.ServiceBus, and
Microsoft.Insights. Regional Sandbox availability and Foundry model quota are
prerequisites. `model_capacity` is a deployment capacity unit, not a concurrent
Worker count or guaranteed token rate.

Entra permissions: an Application Administrator or Cloud Application
Administrator can create/update the standard applications, service principals,
federated credentials, delegated consent, and role assignments. Tenant consent
policies may require a higher-privilege admin for consent. User/group assignment
requires permission to read the selected principals and assign enterprise-app
roles. Azure Owner alone does **not** grant these directory permissions.

The identity command emits an admin request manifest on a Graph authorization
failure instead of inventing an identity or using a client secret. Give the
manifest and the same scripts to the customer's Entra admin; rerun the identity
stage with that admin's device-code login, then restore the deployment operator
login. The original deployment principal is pinned in `environment.json`.

## Inputs and deployment

From `autopilots-on-azure`, create a local TOML file (do not commit customer
identifiers). Replace every placeholder:

```toml
environment = "customer-demo"
subscription_id = "<subscription UUID>"
tenant_id = "<tenant UUID>"
owner_id = "<owner user object UUID>"
auth_mode = "entra"
stack_count = 6
model_capacity = 15

[[assignments]]
principal_id = "<admin user object UUID>"
principal_type = "User"
role = "Admin"

[[assignments]]
principal_id = "<learner group object UUID>"
principal_type = "Group"
role = "User"

[role]
blueprint = "junior-project-manager"
source = "https://github.com/tkubica12/agent-demos.git"
path = "autopilots-on-azure/blueprints/junior-project-manager"
release = "4.0.0"
commit = "<full 40-character commit containing this release>"
```

Instead of `stack_count`, an explicit list can set display names, assignment,
owner, role assignments, and model capacity separately:

```toml
[[stacks]]
name = "agent-1"
display_name = "Agent 1"
assignment = "Delivery coordination"
model_capacity = 15

[[stacks]]
name = "agent-2"
display_name = "Agent 2"
model_capacity = 15
```

Use device-code login only:

```powershell
az login --use-device-code --tenant <tenant UUID>
az account set --subscription <subscription UUID>
uv sync --frozen
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml
```

The default action is `all` for every selected stack: isolated infrastructure and UAMIs, identities,
immutable image builds, simulator creation, redirect reconciliation, Workers,
registrations through `TSIM_AGENTS_JSON`, and health/anonymous-access checks.
In DEMO mode it also logs in, sends a unique challenge through the simulator,
and requires a real Hermes/model reply through the gateway and Connector API.
Each stack has its own three Entra applications (SPA/API, channel, agent).
The initial UAMI infrastructure must exist before Entra FICs can bind to its
actual object IDs. The SPA redirect is captured from the native Sandbox HTTPS
URL; no placeholder endpoint is published. The simulator listens on port 8000.

Stages are individually rerunnable:

```powershell
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml infrastructure
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml identities
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml build
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml deploy
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml verify
```

Prove two stacks first, then deploy the remaining four. Use `--stack` to select
any subset without touching the others:

```powershell
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml --stack agent-1 --stack agent-2
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml --stack agent-3
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml --stack agent-3 verify
```

Rerunning a stack reuses its identities, platform, state, DEMO keys, and runtime
dependency image. Selection never redirects one stack to another's state.
An update that would delete/replace a UAMI or Worker group is rejected before
apply. Source-only Worker changes use the existing source-release mechanism.

## Temporary hosted DEMO

DEMO mode is opt-in and accepts test data only. It is not a substitute for
customer Entra authorization. Add the following to the top level of the TOML:

```toml
auth_mode = "demo"
demo_acknowledge = "test-data-only"
demo_expires_at = "2026-11-15T23:59:00Z"
```

Choose a future UTC expiry within 30 days for the actual exercise. Deployment
generates a private simulator access code and a different strong agent key per
stack in that stack's ignored `secrets.json`. Share the access code through an
approved secure channel, never a deployment log or public issue. Expiry is
enforced by the application, not by an Azure budget. DEMO deployment does not
require Entra app creation. To transition, reconcile identities, change
`auth_mode` to `entra`, and rerun `deploy`; do not recreate the platform.

## Validation and boundaries

```powershell
uv run python -m unittest tests.test_simulator_deployment tests.test_simulator_identity tests.test_sandbox_services tests.test_sandbox_launcher -q
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml verify
```

The `verify` action includes a real personal-chat challenge/reply in DEMO mode
and saves `chat-verification.json` in the stack's local state directory.
In Entra mode, supply `TSIM_SMOKE_ACCESS_TOKEN` from an assigned user's SPA
session to run that same check; without it, verification explicitly reports
that only health and anonymous-access rejection were checked. Tokens are never
saved or printed by this command.

Health and unauthenticated-access checks are **not** complete chat acceptance.
With a real assigned Entra user (or the explicit DEMO access code), verify login,
message → gateway → Hermes → reply, typing, reactions, cards, group chats,
uploads/downloads, and exactly one agent registration per simulator. Repeat on
each selected stack. Check a denied/unassigned user,
expired DEMO session, and a restart retaining chat and uploaded files. Inspect
Foundry/App Insights traces for real inference, not a canned response.

User scheduling, Dreaming, and Microsoft 365 document retries remain disabled
in this simulator deployment until their channel-aware identity and delivery
contracts have been implemented. The original Agent 365 channel keeps its
default behavior. No DevOps/Clarity credentials or mock MCP endpoints are
provisioned. A later phase can add explicit scoped MCP registrations without
changing the simulator's channel identity.

## Cost and teardown

Billable per-stack resources include ACR Basic/builds, Service Bus Standard,
monitoring, private networking, Sandbox compute and volumes, and Foundry token
usage. Stopped Worker compute does not make the environment free. Each simulator
is provisioned at 1 vCPU/2 GiB continuously; its active Worker adds 2 vCPU/2 GiB
runtime and 0.5 vCPU/1 GiB gateway. Six simultaneously active stacks therefore
allocate 21 vCPU/30 GiB including their six simulators; generated apps add usage.
Use current regional retail rates and expected active hours for a budget.
Model capacity is a quota allocation, not an included token allowance.

On 2026-11-13, the Sweden Central retail API returned USD 0.000024/vCPU-second
and USD 0.000003/GiB-second. At those active rates, simulator-only compute is
about **USD 0.108/hour per simulator**; all six complete stacks are **USD 2.1384/hour**.
For 31 days, continuous compute is about USD 1,591. With six Workers active
8 hours on 22 teaching days and all six simulators continuous, compute is about
USD 744. Both estimates exclude model tokens, networking, registry/builds,
storage, monitoring, generated apps, tax, and discounts; they are not spending
limits. The live validation subscription initially had only 95k TPM free;
six separate 15k TPM deployments need 90k TPM total. Customer quotas must cover
the sum of per-stack capacities. A 15k TPM allocation is a constrained classroom
starting point, not proof of an unrestricted concurrency SLA.

Pricing evidence (public sources, retrieved with `web_fetch` on 2026-11-13;
both retrieved successfully and used): [Sandbox cost contract](https://sandboxes.azure.com/docs/sandboxes/cost)
(updated 2026-11-09) and the [Azure Retail Prices API](https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview&currencyCode=USD&$filter=serviceName%20eq%20%27Azure%20Container%20Apps%27%20and%20armRegionName%20eq%20%27swedencentral%27%20and%20priceType%20eq%20%27Consumption%27)
(16 regional consumption meters, including the two active-compute meters).
No internal or community source was needed for this bounded price lookup.

Teardown is destructive: it removes the selected stacks' Workers and backups,
simulator chat/files, owned Entra apps, and each stack's platform. First export
anything the exercise must retain. Use the same full inputs and operator state:

```powershell
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml destroy
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml --stack agent-3 destroy
```

Directory deletion requires Entra permissions as well as Azure permissions.
On failure, retain the local folder, resolve the explicit error, and rerun;
never delete the local state to make teardown appear complete. Other
environments and the original Agent 365 Workers are outside this command's
state boundary.
