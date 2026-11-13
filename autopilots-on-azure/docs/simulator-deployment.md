# Agent Chat Simulator deployment

This flow deploys six fully independent stacks (`agent-1` through `agent-6`),
each with its own resource group, Autopilots platform, persistent Agent Chat
Simulator, gateway, and Hermes Worker. No Foundry account, model deployment,
registry, simulator, or channel audience is shared. Customer landing-zone
networking, DNS and AMPLS may be shared explicitly.
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
The Sandbox image variant performs only volume initialization as root: it sets
`/mnt/tsim` ownership to UID/GID 10001 and mode 0700, then drops supplementary
groups and UID/GID before supervising the app. Both supervisor and app run as
10001; settings stay mode 0600. The generic image still defaults to 10001.
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

## Private landing-zone profile

The simulator entry point defaults to `network_mode = "private"` and
`resource_group_mode = "existing"`. Existing mode never creates/deletes customer
RGs, VNets, subnets, peerings, routes, NSGs, central DNS zones or their VNet links.
Supply one existing RG and dedicated delegated Sandbox subnet per stack.
A nondelegated PE subnet and centrally managed DNS/AMPLS may be shared as
landing-zone services. Private stack resources never fall back to public access.

Run deployment and acceptance from a workstation/runner inside the VNet with
private DNS resolution. Control-plane access from an external laptop is not
proof that private image uploads, application URLs or telemetry are reachable.
Express private ingress and each group's native VNet egress are independent:
every private group is linked to Express at creation and every Sandbox also
selects its group's VNet connection. Keep the platform's default egress proxy.

Private ACR is Premium, public access disabled, managed-identity pulls only.
Its trusted-services bypass defaults to `None`, not `AzureServices`.
`build.mode = "source"` is the default. Quick-build cannot target a
public-disabled registry: supply `platform.acr_agent_pool_subnet_id`, an
existing dedicated nondelegated build subnet, for each registry's native S1
agent pool. The pool builds and pushes runtime, simulator and gateway images
over its VNet; this path performs no ARM image import or public staging.
An in-VNet operator with private ACR DNS uploads the context using Azure CLI;
Docker is not required on that operator.

The pool needs approved outbound routes to the source OCI registries
(`docker.io`, `mcr.microsoft.com`, `ghcr.io`), the pinned MiniMax skill Git source
on GitHub, Debian/Ubuntu OS feeds, the Microsoft package-feed proxy for
PyPI/npm/NuGet, and ACR Tasks dependencies (Entra, Key Vault, Storage, Event Hub
and Azure Monitor). Configure customer-approved mirrors/proxies or allow these
build destinations; this is not an air-gapped build. Optional
`build.hermes_base_image` and `build.gateway_base_image` select customer OCI
mirrors of the pinned bases; retain the exact approved contents/digests.
The Hermes base remains the official v0.22.6 image, not a Hermes source checkout.

`build.mode = "import"` is an optional exception, not the customer default.
It requires **both** `build.allow_trusted_service_import = true` and
`platform.acr_trusted_services_bypass_enabled = true`, plus an approved,
customer-accessible source registry and immutable `repository@sha256:...`
references in `build.runtime_image`, `build.simulator_image` and
`build.gateway_image`. Set `build.source_registry_id` and
`build.source_registry_server` explicitly. Cross-tenant access/distribution is
the customer's prerequisite; a validation registry is not a distributable
artifact. Private import sources must also keep public access disabled. Import,
private `/v2/` connectivity and successful native Sandbox conversion are
separate checks, never evidence of a source build.

Private Foundry, ACR and Express use PEs with supplied central DNS
zone groups. Own monitoring disables public ingestion/query at creation.
`ampls_id` optionally creates scoped-resource links to central AMPLS; otherwise
the customer must complete the AMPLS handoff before telemetry is accepted.
Alternatively supply both existing monitoring IDs; they remain read-only.

Global `[platform]` is merged with each `[stacks.platform]`. The exact keys are:
`location` (AI region), `sandbox_location` (infrastructure region),
`existing_resource_group_name`, `existing_sandbox_subnet_id`,
`existing_private_endpoint_subnet_id`, `private_dns_zone_ids` (zone-name/ARM-ID
map), `required_tags`, `existing_log_analytics_workspace_id`,
`existing_application_insights_id`, `ampls_id`, `acr_agent_pool_subnet_id`,
`acr_agent_pool_name`, `acr_agent_pool_instance_count`, and the explicit import
exception `acr_trusted_services_bypass_enabled`.
Top-level `private_dns_zone_ids_file` optionally loads that map from a JSON file
relative to the TOML. `network_mode = "public"` and
`resource_group_mode = "create"` are explicit non-customer demo options only.

Required stack DNS zones are `privatelink.azurecr.io`,
`privatelink.cognitiveservices.azure.com`, `privatelink.openai.azure.com`,
`privatelink.services.ai.azure.com`, and
`privatelink.<sandbox_location>.azurecontainerapps.io`. Central AMPLS also needs
its existing `privatelink.monitor.azure.com`, `privatelink.oms.opinsights.azure.com`,
`privatelink.ods.opinsights.azure.com`, `privatelink.agentsvc.azure-automation.net`
and `privatelink.blob.core.windows.net` zones/links. The simulator profile has no
Service Bus namespace, so its zone is not required.

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
| `role` | Bundled local Role Blueprint, path, release, immutable role commit and manifest hash |
| `model_capacity` | Capacity per stack (default 15); explicit `stacks` can override each |
| `platform` | Optional regions/model deployment settings and adequate total model quota |
| `auth_mode` | `entra` by default; `demo` only for temporary test data |

Azure permissions: create resources inside the selected resource groups, assign Azure
roles, create custom role definitions, build/import ACR images, and use
SandboxGroup data-plane operations. Subscription Owner suffices; alternatively
combine Contributor, Role Based Access Control Administrator (including the
custom-role operations), and SandboxGroup Data Owner at the appropriate scopes.
Register the providers used by this profile: Microsoft.App,
Microsoft.CognitiveServices, Microsoft.ContainerRegistry,
Microsoft.ManagedIdentity, Microsoft.Network, Microsoft.OperationalInsights,
and Microsoft.Insights. Regional Sandbox availability and Foundry model quota are
prerequisites. `model_capacity` is a deployment capacity unit, not a concurrent
Worker count or guaranteed token rate.

Entra permissions: an Application Administrator or Cloud Application
Administrator can create/update the standard applications, service principals,
federated credentials, delegated consent, and role assignments. Tenant consent
policies may require a higher-privilege admin for consent. User/group assignment
requires permission to read the selected principals and assign enterprise-app
roles. Azure Owner alone does **not** grant these directory permissions.
Publishing a new delegated scope can briefly precede Graph's permission-set
visibility. Only the exact `InvalidValue` preauthorization publication error is
retried for the intended self-scope consent PATCH, with bounded delays. Other
400 errors and exhausted retries remain explicit; retained identity IDs make
the same command rerunnable without duplicate applications.

The identity command emits an admin request manifest on a Graph authorization
failure instead of inventing an identity or using a client secret. Give the
manifest and the same scripts to the customer's Entra admin; rerun the identity
stage with that admin's device-code login, then restore the deployment operator
login. The original deployment principal is pinned in `environment.json`.

## Inputs and deployment

From `autopilots-on-azure`, create a local TOML file (do not commit customer
identifiers). A complete six-stack file with **synthetic, nondeployable customer
identifiers** is [simulator-private.example.toml](simulator-private.example.toml).
Copy it outside the source snapshot and replace its identifiers. A shorter
two-stack example follows:

```toml
environment = "customer-demo"
subscription_id = "<subscription UUID>"
tenant_id = "<tenant UUID>"
owner_id = "<owner user object UUID>"
auth_mode = "entra"
network_mode = "private"
resource_group_mode = "existing"
private_dns_zone_ids_file = "dns-zones.json"
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
source = "local"
path = "blueprints/junior-project-manager"
release = "4.0.0"
commit = "<full 40-character commit containing this release>"
bundle_sha256 = "<role manifest SHA-256 supplied with the snapshot>"

[platform]
location = "swedencentral"
sandbox_location = "swedencentral"
model_name = "gpt-6.1-sol"
model_version = "2026-09-29"
model_sku_name = "GlobalStandard"
existing_private_endpoint_subnet_id = "<full customer PE subnet ARM ID>"
acr_agent_pool_subnet_id = "<full dedicated build subnet ARM ID>"
ampls_id = "<full central AMPLS ARM ID>"
required_tags = { project = "classroom", environment = "training" }

[build]
mode = "source"

[[stacks]]
name = "agent-1"
[stacks.platform]
existing_resource_group_name = "<customer RG for agent-1>"
existing_sandbox_subnet_id = "<full dedicated agent-1 Sandbox subnet ARM ID>"

[[stacks]]
name = "agent-2"
[stacks.platform]
existing_resource_group_name = "<customer RG for agent-2>"
existing_sandbox_subnet_id = "<full dedicated agent-2 Sandbox subnet ARM ID>"
```

This example intentionally proves two stacks first. Add explicit entries for
`agent-3` through `agent-6` with distinct RGs and Sandbox subnets before the
six-stack deployment. An explicit stack list can also set display names,
assignment, owner, role assignments and model capacity separately:

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
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml plan
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml
```

The default action is `all` for every selected stack: isolated infrastructure and UAMIs, identities,
immutable image builds, simulator creation, redirect reconciliation, Workers,
registrations through `TSIM_AGENTS_JSON`, and health/anonymous-access checks.
Image builds export only the pinned commit's tracked source into a temporary
context, rejecting dirty or changed source. The staged archive SHA is recorded
with the commit; local dependencies, caches and secrets are never uploaded.
Git-free release packages must include `source_commit` and a `files` map of
relative paths to SHA256 hashes in `deployment-manifest.json`.
In DEMO mode it also logs in, sends a unique challenge through the simulator,
and requires a real Hermes/model reply through the gateway and Connector API.
Each stack has its own three Entra applications (SPA/API, channel, agent).
Private stacks build in their own registries/pools by default. Copies between
stacks require the explicit trusted-services import exception; different
per-stack build settings are never silently reused. No stack runs from another
stack's registry or shares its identity.
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

The `plan` action performs read-only Azure checks before any deployment lock,
state-directory preparation, Terraform apply or Entra write. It reports exact
per-stack scope and blocks invalid/unreadable customer boundaries. Quota or
permissions that cannot be proven read-only remain explicit warnings.
Model catalog support (region, model, version and SKU) is checked separately
from quota. The example uses `swedencentral`, `gpt-6.1-sol`, version
`2026-09-29`, `GlobalStandard`, with deployment name `gpt-6-1-sol`.
A quota meter alone does not prove regional availability or deployment admission.
Admission reports requested, already-owned and incremental capacity. Only an
exact deployment recorded in this stack's ownership boundary and Terraform
workspace, verified against live ARM metadata, is credited on a rerun. Missing
state earns no credit; mismatched state fails. Unrelated deployments and
capacity that another operation might release are never credited.

Rerunning a stack reuses its identities, platform, state and DEMO keys. The
runtime dependency image is reused only while its Dockerfile, dependency
inputs and base reference match the recorded fingerprint. Selection never
redirects one stack to another's state.
An update that would delete/replace a UAMI or Worker group is rejected before
apply. Source-only Worker changes use the existing source-release mechanism.
Native dependencies in `runtimes\hermes\hermes-telemetry.txt` and
`runtimes\hermes\hermes-native.txt` are part of the runtime dependency-image
fingerprint. The latter corrects the pinned native base's `browser-harness`
Pillow constraint with a hash-verified Linux CP314 wheel and PNG roundtrip,
without changing `/app/.venv`. The native telemetry guard snapshots every
installed base package after this correction and rejects any replacement or
removal while adding HTTP tracing. Keep both manifests,
`runtimes\hermes\native_telemetry_smoke.py`, and
`bridge\telemetry.py` in the Docker build context. The image checks dependencies
and the shared telemetry initializer with the actual `/opt/hermes/.venv`
interpreter; a successful `/app/.venv` import does not prove native readiness.
Teardown removes only the exact Application Insights-generated failure-anomaly
alert inside the captured stack RG before Terraform deletes the platform;
Terraform's guard against deleting unexpected resources remains enabled.

## Extracted snapshots and durable state

The source works without `.git`. Package `deployment-manifest.json` beside
`pyproject.toml` containing `{"source_commit":"<full source SHA>"}`.
Terraform copying uses only `*.tf` and `.terraform.lock.hcl`, never Git
discovery or copied state. Use `--state-root D:\AutopilotsState` or
`AUTOPILOTS_STATE_ROOT` on every invocation to keep durable stack state outside
side-by-side extracted source revisions. Platform state references are absolute
paths inside that root.
Committed provider locks contain signed Windows and Linux checksums; deployment
keeps `terraform init -lockfile=readonly` and never relaxes checksum verification.

Local Role Blueprints are included in the verified runtime source release.
The package producer calls `scripts.role_bundle.build_role_bundle(role,
verify=False)` to obtain the deterministic role manifest hash and supplies it
as `[role].bundle_sha256`. Deployment and every runtime boot verify the exact
file inventory, hashes, path, release and recorded immutable commit; local mode
never clones or fetches a Git repository.

An extracted source snapshot is **not** an air-gapped dependency bundle.
Terraform providers, `uv`/Python packages, base OCI images, npm packages, OS
packages and the pinned MiniMax skill Git source used by the runtime Dockerfile
still need approved mirrors/proxies or a prebuilt dependency image. Gateway
source installation also downloads hash-pinned wheels from the approved feed.
Entra login/metadata, Azure control-plane APIs and managed-identity facilities
remain required. Never solve a blocked private mirror by enabling PaaS public
access or substituting an upstream package registry.

## Temporary hosted DEMO

DEMO mode is opt-in and accepts test data only. It is not a substitute for
customer Entra authorization. Add the following to the top level of the TOML:

```toml
auth_mode = "demo"
demo_acknowledge = "test-data-only"
demo_expires_at = "2026-11-15T23:59:00Z"
demo_owner_display_name = "Teacher"
```

Choose a future UTC expiry within 30 days for the actual exercise. Deployment
generates a private simulator access code and a different strong agent key per
stack in that stack's ignored `secrets.json`. Share the access code through an
approved secure channel, never a deployment log or public issue. Expiry is
enforced by the application, not by an Azure budget. DEMO deployment does not
require Entra app creation. To transition, reconcile identities, change
`auth_mode` to `entra`, remove `demo_owner_display_name`, and rerun `deploy`;
do not recreate the platform.

The optional `demo_owner_display_name` explicitly sets the Worker's owner to
the simulator's UUIDv5 identity for that trimmed, lower-cased name, allowing
the owner-only `/learning` card. Log in with that exact display name. The
Entra `owner_id`, deployment ownership and RBAC operator remain unchanged.
Without this option, the Worker keeps its Entra owner and an unauthenticated
DEMO persona cannot become that owner just by choosing a display name.

## Validation and boundaries

```powershell
uv run python -m unittest tests.test_simulator_deployment tests.test_simulator_identity tests.test_sandbox_services tests.test_sandbox_launcher -q
uv run python -m scripts.deploy_simulator_environment --config .local\customer.toml verify
```

The `verify` action includes a real personal-chat challenge/reply in DEMO mode
and saves `chat-verification.json` in the stack's local state directory.
In Entra mode an assigned user must use a real in-VNet browser. Never paste or
export access tokens. After actually completing the checks, save
`browser-acceptance.json` beside the stack's `deployment.json`:

```json
{
  "schemaVersion": 1,
  "environment": "customer-demo-agent-1",
  "url": "<exact URL from deployment.json>",
  "authMode": "entra",
  "verifiedAt": "<UTC ISO timestamp within the last seven days>",
  "checks": {
    "login": true,
    "messageReply": true,
    "typing": true,
    "reactions": true,
    "cards": true,
    "groupChat": true,
    "files": true
  }
}
```

This is explicitly manual operator evidence, not an automated Entra login.
Missing, incomplete, wrong-stack/URL or stale evidence makes `verify` exit
nonzero after saving limited health evidence. Do not copy the `true` values
without exercising the capabilities against real Hermes inference.

Per-stack evidence is `deployment.json` (URLs, source SHA and registrations),
`verification.json` (health, anonymous rejection and completed real chat),
and `chat-verification.json` (unique challenge, conversation and agent reply).
`browser-acceptance.json` records manual Entra checks. Health and DEMO challenge
files alone do not claim typing/reaction/card/group/file acceptance.

Health and unauthenticated-access checks are **not** complete chat acceptance.
With a real assigned Entra user (or the explicit DEMO access code), verify login,
message → gateway → Hermes → reply, typing, reactions, cards, group chats,
uploads/downloads, and exactly one agent registration per simulator. Repeat on
each selected stack. Check a denied/unassigned user,
expired DEMO session, and a restart retaining chat and uploaded files. Inspect
Foundry/App Insights traces for real inference, not a canned response.
Transport/unit fixtures that stub Hermes are not real-runtime feature evidence.

The simulator deployment enables `GENERATED_CHAT_FILES_ENABLED` on both
gateway and runtime; Agent 365 defaults remain off and unchanged. Native
Hermes `MEDIA:` exports are limited to the exact turn's workspace and are
retrieved through authenticated opaque file IDs, not arbitrary host paths.
The private runner must download fresh DOCX and UTF-8 TXT bytes through the
browser, verify their contents and hashes, and retain both through a real
restart. Implementation/unit checks do not waive this live acceptance.
Set `generated_chat_files_enabled = false` only for an explicitly reduced
deployment that is not claiming complete file acceptance.

For startup failures, read the owned simulator's
`/tmp/simulator-service.log` through its authenticated Sandbox client.
Settings activation explicitly applies POSIX mode 0600 before atomic rename;
the supervisor verifies that mode and UID 10001 before reading. Do not rely on
the Sandbox file-upload API's string-mode interpretation: a live upload using
`"0600"` produced mode 1110 and prevented the unprivileged process from reading.
Teardown drains all ownership-checked native instances before deleting their
VNet connections, including instances left by a partial deployment; retained
`outputs.json` preserves the native group boundary after Terraform outputs have
already disappeared. Uploaded-document transport and fixture downloads are not
model-generated-file evidence. Card transport is not proof that a card action
caused a real model/tool outcome. The key-authenticated gateway
`/internal/generated-files/evidence?conversation_id=<exact-ID>` links delivery
activity IDs and byte hashes to native run/trace IDs. The runtime's
`/internal/runs/<native-run-ID>/evidence` projects bounded native provider/model,
usage and tool metadata. Missing or expired native evidence is an explicit gap;
never export debug channel payloads to replace it.

User scheduling, Dreaming, and Microsoft 365 document retries remain disabled
in this simulator deployment until their channel-aware identity and delivery
contracts have been implemented. The original Agent 365 channel keeps its
default behavior. No DevOps/Clarity credentials or mock MCP endpoints are
provisioned. A later phase can add explicit scoped MCP registrations without
changing the simulator's channel identity.

## Cost and teardown

Billable private per-stack resources include Premium ACR, S1 build pool
instances while allocated, monitoring, private endpoints/networking, Sandbox
compute and volumes, and Foundry token
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
limits. Six separate default 15k TPM deployments need 90k TPM total; the
explicitly reduced validation configuration uses 10k per stack, not the customer
default. Customer quotas must cover
the sum of per-stack capacities. A 15k TPM allocation is a constrained classroom
starting point, not proof of an unrestricted concurrency SLA.
The compute figures exclude the private Premium registry, agent pools and
private endpoints; these can materially increase the total. Size/stop unused
build capacity explicitly after builds; never leave six S1 pools outside the
budget. The simulator profile does not provision Service Bus.

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
