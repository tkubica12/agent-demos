# Agent Chat Simulator deployment

Deploy six independent `agent-1` through `agent-6` stacks. Each has its own RG,
Foundry account/model deployment, simulator, gateway, Hermes runtime, workload
identities and persistent data. Customer networking, DNS and AMPLS may be shared
explicitly. This channel is not Microsoft Teams: no Agent 365, `a365`, publishing,
Work IQ or Microsoft 365 licensing is required. DevOps/Clarity MCP is phase 2.

## Architecture

All three services run on ACA Sandboxes. Private browser ingress uses an ACA
Express environment and PE; every Sandbox also selects its native VNet egress
connection. Foundry and monitoring remain private. Classic ACA is not used.
The deployment uses authenticated Sandbox management APIs, not a Linux VM,
private ACR, build pool, source installer or an in-VNet deployment runner.
An assigned user's browser still needs private network/DNS access.

GitHub Actions builds generic, self-contained images. Tenant/app/FIC IDs, owner
IDs, Foundry settings, origins, keys and role references are runtime configuration,
never image inputs. The SPA/API, channel and agent each have separate standard
single-tenant Entra applications. UAMIs and federated credentials replace app
secrets. Runtime inference uses its own UAMI and Foundry RBAC.

Automatic suspend **and** automatic delete are explicitly disabled for simulator,
gateway and runtime. Deployment checks the actual raw Sandbox policies, rejecting
missing/inferred values. Persistent single-writer Data Disks hold simulator chat
and files (`/mnt/tsim`, 5 GiB), runtime home/role data (`/data`, 10 GiB) and gateway
journal backups (`/backup`, 5 GiB). Runtime native backup archives are written at
the actual `/data` mount root alongside its home/role directories; `/data/backups`
is not a separate mount and fails the unchanged native backup mount guard.
No suspend mode alone proves never-suspend.

The packaged launcher starts the actual application directly with environment
variables, or waits for mode-0600 deployment configuration and supervises the same
fixed startup. No application source or dependencies are installed at deployment.
The simulator initializes its mounted directory as root, then drops to UID/GID
10001 before reading settings or serving requests. Runtime retains its upstream
root bootstrap and subsequent Hermes UID/GID drop.

Local Role Releases are verified configuration data, not baked customer images.
Deployment publishes the exact manifest/file inventory under
`/data/role-bundles/<manifest-sha256>`; `HERMES_ROLE_BUNDLE_ROOT` selects it.
Every runtime boot verifies its manifest, content hashes, role, release and commit
before materializing the profile. Local mode does not fetch a role repository.

Demo MCP services and native scheduling namespaces are omitted. Interactive chat,
the conversation journal and generated documents do not require those services.

## Pinned release and inputs

Use the source snapshot and its **matching** `deployment-images` artifact from
`.github/workflows/autopilots-images.yml`. The artifact is:

```json
{
  "schema": 1,
  "source_commit": "<full lowercase Git SHA>",
  "images": {
    "runtime": "ghcr.io/<owner>/<repository>@sha256:<64 lowercase hex>",
    "gateway": "ghcr.io/<owner>/<repository>@sha256:<64 lowercase hex>",
    "simulator": "ghcr.io/<owner>/<repository>@sha256:<64 lowercase hex>"
  }
}
```

The loader rejects extra/missing fields, tags and non-GHCR references. Deployment
refuses a source/image commit mismatch before preparing or changing resources.
Publication/authentication alone does not establish public visibility: anonymously
GET each exact manifest digest using the standard registry Bearer challenge and
verify its byte hash. Native conversion checks that the Ready disk retains that
same reference. Neither check proves application startup or feature acceptance.
The release workflow also starts each exact image on an offline Docker network,
loads protected service settings and checks real application health; runtime also
checks its native activity endpoint. This is container startup evidence, not
Azure deployment, identity, inference or end-to-end feature acceptance.

Copy [simulator-private.example.toml](simulator-private.example.toml) outside the
source snapshot. Its identifiers are synthetic and nondeployable. Replace them
with customer values and provide the matching artifact:

```toml
[images]
manifest = "deployment-images.json"
```

The path is relative to the inputs TOML. `[build]`, ACR import exceptions and
build-pool settings are not simulator deployment inputs.

Top-level inputs are `environment` (new 3-14 character lowercase prefix),
`tenant_id`, `subscription_id`, `owner_id`, `auth_mode`, `assignments`, `role`,
`model_capacity`, and explicit `[[stacks]]`. Six stacks are generated when no
explicit list is supplied; `stack_count` is configurable. Each explicit stack can
override display name, owner, assignment, role assignments and model capacity.
Each stack has exactly one simulator and Worker.

`network_mode = "private"` and `resource_group_mode = "existing"` are defaults.
Global `[platform]` is merged with `[stacks.platform]`: AI `location`,
`sandbox_location`, model name/version/deployment/SKU, existing RG and dedicated
delegated Sandbox subnet, shared nondelegated PE subnet, central
`private_dns_zone_ids`, required tags, optional existing monitoring IDs and
`ampls_id`. `private_dns_zone_ids_file` optionally loads the map from a JSON file
relative to the TOML. Existing-mode RGs, VNets, subnets, routes, NSGs, central DNS
zones/links and existing monitoring resources are not owned by these stacks.

Required service DNS zones are `privatelink.cognitiveservices.azure.com`,
`privatelink.openai.azure.com`, `privatelink.services.ai.azure.com`, and
`privatelink.<sandbox_location>.azurecontainerapps.io` (or its parent
`privatelink.azurecontainerapps.io`). Existing central AMPLS also needs its
`privatelink.monitor.azure.com`, `privatelink.oms.opinsights.azure.com`,
`privatelink.ods.opinsights.azure.com`, `privatelink.agentsvc.azure-automation.net`
and `privatelink.blob.core.windows.net` DNS/links. No ACR or Service Bus zone is
required. Workload egress must permit public GHCR pulls and necessary Entra/Azure
service endpoints; blocked egress is an error, never a public-PaaS fallback.

## Prerequisites and operator flow

Install Azure CLI, Terraform >=1.7 and `uv`; preserve committed provider/dependency
locks and use the approved Microsoft feed. Operator package/provider restoration
still requires approved network access; this is not an air-gapped package.

Azure permissions must cover resource deployment, role/custom-role assignments
and SandboxGroup data-plane operations. Azure Owner alone does not grant Entra
application administration. Entra requires permission to create/update apps,
service principals, federated credentials, consent and enterprise-app role
assignments. On Graph authorization failure the identity stage saves an exact
admin request manifest, not an invented identity or secret. An Entra admin runs
the same stage, then the original operator restores their device-code login.
The first deployment principal remains pinned in `environment.json`.

Register Microsoft.App, Microsoft.CognitiveServices, Microsoft.ManagedIdentity,
Microsoft.Network, Microsoft.OperationalInsights and Microsoft.Insights.
Model catalog availability and quota are separate prerequisites. The template uses
Sweden Central `gpt-6.1-sol`, version `2026-09-29`, `GlobalStandard`, deployment
`gpt-6-1-sol`, capacity 15 per stack. Six deployments require total capacity 90;
this is not a concurrency or token-rate guarantee.

From `autopilots-on-azure`:

```powershell
az login --use-device-code --tenant <tenant UUID>
az account set --subscription <subscription UUID>
uv sync --frozen
uv run python -m scripts.deploy_simulator_environment --config D:\Deployment\inputs.toml --state-root D:\AutopilotsState plan
uv run python -m scripts.deploy_simulator_environment --config D:\Deployment\inputs.toml --state-root D:\AutopilotsState
```

Default `all` performs isolated infrastructure/UAMIs, identities, pinned-image
selection, simulator creation, actual-URL redirect reconciliation, Workers,
registrations and management-channel readiness checks. It does **not** certify
full browser/chat acceptance. Individual actions are `prepare`, `infrastructure`,
`identities`, `images`, `deploy`, `verify`, `all` and `destroy`; `plan` is read-only.
There is no `build` action. Select a subset with repeated `--stack agent-N`.
Prove two stacks before deploying the remaining four.

Every stack selects a non-default Terraform workspace inside its own durable
`simulator-<environment>-agent-N` directory. Only tracked Terraform files and
locks are copied; platform-state paths are absolute. Existing default-workspace
state, `.local\hermes`, `.local\hermes2` and unrelated Agent 365 objects are never
used. Extracted snapshots need a verified `deployment-manifest.json` containing
their full `source_commit` and file hashes; Git is not needed.

Retain the complete durable directory with restricted access and backups. It
contains authoritative Terraform state, identities, DEMO/API/signing keys and
deployment receipts. Do not commit it, put it in a build context or send it to an
LLM. Never apply stale copied state. Use one operator per environment and preserve
the same state root across source revisions. Do not delete state after a failed
deployment or teardown.

## DEMO and acceptance

Entra is the default. Temporary hosted DEMO requires top-level `auth_mode="demo"`,
`demo_acknowledge="test-data-only"` and a future UTC `demo_expires_at` within
30 days. Access codes and per-agent keys are generated in private `secrets.json`,
never printed. Share codes through an approved secure channel. Optional
`demo_owner_display_name` explicitly maps the Worker owner to that DEMO persona;
it does not change the deployment operator or RBAC.

`readiness.json` records actual loopback health, release identity and explicit
never-suspend/delete policies through authenticated management. Health commands
require their invocation's completion nonce, not the SDK's default exit zero.
This avoids requiring a VM/private runner merely to provision or check startup.

Run `verify` from a client with application-network access. DEMO verifies a real
challenge/reply through simulator, gateway and Hermes. Entra additionally requires
an assigned user's actual browser checks in `browser-acceptance.json`, with
`schemaVersion=1`, exact environment/URL, `authMode="entra"`, UTC `verifiedAt`
within seven days and true checks for `login`, `messageReply`, `typing`,
`reactions`, `cards`, `groupChat`, `files`. This is explicit manual evidence, not
an automated Entra login; missing/stale evidence fails.

Full acceptance must exercise real inference, owner/denied access, personal and
group chat, typing, reactions, card actions, upload/download, fresh model-generated
DOCX/TXT and widgets, and a real restart retaining chat/files. Preserve native
run/model/usage/trace and exact skill-load evidence. Installed skill hashes or a
prompt asking to load it are not proof it was used. Unit fixtures, uploaded files,
health or a transport-only card do not establish these outcomes.

Generated files default on for both services; disabling
`generated_chat_files_enabled` is an explicitly reduced deployment. Runtime and
gateway internal evidence endpoints require their API key and exact conversation
or run IDs. Never export debug payloads/tokens as evidence. Frozen widget/skill
contents must remain unchanged.

Read startup logs only through authenticated Sandbox access:
`/tmp/simulator-service.log` or `/app/.sandbox-service.log`. Settings activation
uses explicit POSIX chmod before atomic rename; do not rely on the SDK upload
string-mode interpretation. Unexpected ownership, missing completion or failed
native/backend startup remains an error.

## Cost and teardown

All three services stay allocated continuously. Default resources per stack are:

| Service | vCPU | Memory | Root disk | Persistent Data Disk |
| --- | ---: | ---: | ---: | ---: |
| Simulator | 1 | 2 GiB | 10 GiB | 5 GiB |
| Gateway | 0.75 | 1 GiB | 10 GiB | 5 GiB |
| Hermes runtime | 2 | 2 GiB | 20 GiB | 10 GiB |

The native disk budget includes both root and attached Data Disks. The gateway
needs at least 750m CPU for its combined 15 GiB; 500m is rejected by the service.
Six complete stacks allocate 22.5 vCPU/30 GiB before generated apps.
Foundry tokens, disks, monitoring and
network/PE charges are additional; there are no per-stack registries/build pools.
Model capacity is quota, not an included token allowance. Establish a budget
using current regional rates; stopping one service does not make the stack free.

Teardown deletes selected Workers, persistent chat/files/profile data, owned apps
and platform resources. Export anything to retain first, then use the original
inputs and authoritative durable state:

```powershell
uv run python -m scripts.deploy_simulator_environment --config D:\Deployment\inputs.toml --state-root D:\AutopilotsState --stack agent-3 destroy
```

Ownership-checked native instances are drained before their VNet connections.
Captured outputs retain boundaries after partial Terraform teardown. Directory
cleanup needs Entra permissions too. On failure retain state and resolve the exact
error; never delete existing unrelated stacks, registries, pools or VM/disks as
part of this new deployment flow.
