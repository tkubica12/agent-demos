# Private landing-zone acceptance

These are real-service probes and an in-VNet browser acceptance runner, not a
replacement simulator or a replay suite. They also work in an extracted package
without `.git`. Only test data is allowed in DEMO mode. A feature that the actual
agent cannot produce fails explicitly; no connector token, canned response,
synthetic agent, or test identity provider is substituted.

## Landing zone

`terraform\test-landing-zone` creates its own nine resource groups: network,
central DNS, experiments, and six initially empty stack groups. Its hub/spoke
have six dedicated delegated `/26` Sandbox subnets, separate private-endpoint,
client and build subnets, peerings, twelve private DNS zones linked to both
VNets, central PrivateOnly AMPLS plus PE, a NAT gateway for client/build outbound
dependencies, private Linux/Windows clients, and Basic Bastion. Only Bastion and
the outbound NAT have public IPs; neither client has public ingress.

The Linux system-assigned identity has Contributor, RBAC Administrator and
SandboxGroup Data Owner only on the six test stack groups; Network Contributor
on the test network group; Private DNS Zone Contributor on the test DNS group;
and Monitoring Contributor on the central test AMPLS. It has no directory
administration or subscription-wide custom-role-definition permissions.
Windows has an unprivileged system identity. These are **test operator**
permissions, not the workload identities to use in customer stacks.

From `autopilots-on-azure`, with Azure CLI device-code login, Terraform and uv:

```powershell
uv run python terraform\test-landing-zone\manage.py prepare --prefix <unique-prefix> --subscription <subscription-UUID>
uv run python terraform\test-landing-zone\manage.py apply --prefix <unique-prefix> --subscription <subscription-UUID>
```

The backend and generated credentials live in ignored
`.local\landing-zone-<unique-prefix>\`, never another Terraform root's default
state. Keep this directory private. `deployment-inputs.json` contains
`network_mode`, `resource_group_mode`, shared `platform` inputs and six
`stacks[].platform` overrides. `private-dns-zone-ids.json` is the preflight
`{ "full.zone.name": "/subscriptions/.../privateDnsZones/full.zone.name" }` map.
`clients.json` contains VM IDs, private addresses and the Linux principal ID.
No tenant/customer identifier is hard-coded into the Terraform.

The nested `experiments` root is separate and optional. It has its own local
backend and takes `subscription_id`, `prefix`, `operator_id`, and `location`.
It creates two test groups sharing the first delegated subnet, Express private
ingress, a Premium ACR and S1 VNet build pool, private Foundry with a small real
model deployment, and private authenticated monitoring linked to central AMPLS.
Initialize it with an explicit `-backend-config=path=<ignored-absolute-path>`.
Destroy its Sandbox data-plane objects before destroying its Terraform root.
Do not run experiments while deploying a stack into that same first subnet.

Subnet and VNet writes use AzAPI locks to avoid Azure concurrent-write/SAL lease
conflicts. Foundry PE creation retries only the explicit
`AccountProvisioningStateInvalid` provisioning race. Responses for changing VNet
subnet arrays are not exported, avoiding a provider tuple-length mismatch.

Client preparation is explicit:

```powershell
az vm run-command invoke -g <network-RG> -n <linux-VM> --command-id RunShellScript --scripts "@tests\e2e_private\prepare_linux.sh"
az vm run-command invoke -g <network-RG> -n <linux-VM> --command-id RunShellScript --scripts "@tests\e2e_private\install_linux_tools.sh"
az vm run-command invoke -g <network-RG> -n <windows-VM> --command-id RunPowerShellScript --scripts "@tests\e2e_private\prepare_windows.ps1"
```

The first Linux script inspects configuration and queries approved-feed tool
versions. Review/update the explicit tool pins in the second script only after
that query; the pins were resolved on 2026-11-13. Python/npm tools use the
approved Microsoft feed. Terraform is checksum-verified from HashiCorp.
Chromium is installed by the existing Playwright version's installer. This
needs approved outbound routes to package sources/browser distributions;
private ingress does not imply an offline environment.

To stop compute costs, use `manage.py stop-clients` with the same arguments,
or deallocate just the idle Windows VM with `az vm deallocate`. Bastion, NAT,
disks, ACR, PE and DNS charges continue when VMs stop. The research ACR
pool defaults to zero; apply `build_pool_instances=1` explicitly for builds and
return it to zero when idle. Destroy stack roots first,
then experiment resources, and finally the landing zone. `manage.py destroy`
refuses nonempty stack/experiment groups; do not bypass that guard.
App Insights may auto-create an alert outside Terraform ownership. After the
experiment root is destroyed, inventory its exact RG with `az resource list`.
If present, delete only
`<experiment-RG-ID>/providers/Microsoft.AlertsManagement/smartDetectorAlertRules/Failure Anomalies - ai-monitor-<prefix>`
using `az resource delete --ids "<exact-ID>"`. The live cleanup required this
one named orphan removal; do not recursively delete a populated landing-zone RG.

## Browser acceptance

Requirements: a host using the customer's private DNS and routes; Python 3.11+
through uv; Node 20+; Chromium; the existing simulator frontend's locked
`@playwright/test` package. `npm ci` in `chat-simulator\frontend` restores that
lockfile without upgrading it. Alternatively set `PRIVATE_PLAYWRIGHT_MODULE`
to an already installed `@playwright/test` directory, as on the prepared VM:
`/opt/private-playwright/node_modules/@playwright/test`.

First run the negative probe **outside** the VNet/VPN/private DNS, with all
selected simulator origins. Normal public DNS may still return a shared
frontend address; NXDOMAIN is not required. The actual health request must be
unreachable/rejected, while it must succeed with a trusted certificate inside.
The outside probe also verifies a public Entra HTTPS connectivity control, so
an offline operator cannot produce a false pass. Requests are direct, not
silently routed through an HTTP proxy:

```powershell
uv run python tests\e2e_private\network_probe.py --public-negative --url https://<native-private-simulator-FQDN> --output .local\public-negative.json
```

Before application boot completes, the in-VNet `--transport-only` option
independently proves private DNS, a private TLS peer and trusted hostname
certificate. Its explicit scope is `private-dns-tls`: it sends no application
request and does **not** certify health, login or agent readiness. The default
private probe and the full runner still require successful `/api/health`;
HTTP503 remains a failure rather than a success-shaped fallback.

Transfer that nonsecret evidence to the in-VNet host. Create a private TOML
configuration (do not commit live origins, credentials or command arguments):

```toml
output_directory = ".local/private-acceptance"
public_negative_evidence = ".local/public-negative.json"
timeout_seconds = 180

[[stacks]]
name = "agent-1"
url = "https://<native-private-simulator-FQDN>"
access_code_env = "PRIVATE_AGENT_1_CODE"
owner_display_name = "<configured-DEMO-Worker-owner-display-name>"
worker_owner_id = "<deployed-WORKER_OWNER_ID-UUID>"
# Explicit operator-approved argv that really restarts the simulator.
# Do not use page reload, a health check, or an arbitrary sleep here.
restart_command = ["uv", "run", "python", "tests/e2e_private/restart_sandbox.py", "--subscription", "<UUID>", "--resource-group", "<stack-RG>", "--group", "<simulator-group>", "--sandbox-id", "<simulator-Sandbox-UUID>"]
```

Supply each existing DEMO code through the named environment variable without
printing it. Repeat `[[stacks]]` for every deployed stack. From the extracted
`autopilots-on-azure` directory inside the VNet:

```powershell
uv run python tests\e2e_private\run.py --config .local\private-acceptance.toml
```

On the prepared Linux host, set `PRIVATE_PLAYWRIGHT_MODULE` as above first.
No Git access is used. The output is `private-network.json`, per-stack
`result.json`, a final `summary.json`, test upload fixtures, and a screenshot
after authentication. Access codes and bearer tokens are not recorded;
browser traces/HAR are deliberately disabled. Keep screenshots/chat evidence
private even though this runner uses test data.

For an operator outside the VNet, `run_on_client.py` submits a uniquely named
**Managed Run Command** to the explicitly authorized private Linux VM. Write
the actual in-VM invocation into a local `.sh` file, using paths in the extracted
package and sourcing the existing private access-code environment file inside
the VM. Do not put codes, operator tokens or credentials into that script.

```sh
#!/usr/bin/env bash
set -eu
cd "/opt/<extracted-package>/autopilots-on-azure"
set -a
. "/opt/<private-access-code-environment-file>"
set +a
export PRIVATE_PLAYWRIGHT_MODULE=/opt/private-playwright/node_modules/@playwright/test
uv run python tests/e2e_private/run.py --config .local/private-acceptance.toml
```

Use the full extracted project with its frozen dependencies restored for
native SDK lifecycle calls, not just a standalone copy of the browser scripts.

```powershell
uv run python tests\e2e_private\run_on_client.py --vm-id "<Linux-VM-ARM-ID>" --script .local\run-private-acceptance.sh --output .local\managed-acceptance.json
```

The ARM script is ordinary source text; secrets must remain in the VM's private
environment file, not in its public `source.script` property. CLI credentials
authenticate only the ARM request and are never sent to the guest. The helper
sets `asyncExecution=true`, reads expanded `instanceView`, and requires
`executionState=Succeeded` **and** `exitCode=0`. It does not mistake Pending/exit0
for success or wait forever on stale parent resource `Creating`. It preserves
private execution evidence and deletes only its own terminal temporary command
record, including terminal script failures. Pending/running timeouts leave that
record for diagnosis; inspect it rather than blindly resubmitting or stopping
another session's job.
Legacy `az vm run-command invoke` returned `InternalExecutionError` during
concurrent deployment in the live test; the named managed alternative
succeeded and verified the runner file hash. Prefer this method for prepared
private-client execution. The operator must have scoped VM run-command rights.

Checks include RFC1918-only DNS and verified HTTPS; anonymous API rejection;
two separate real DEMO browser sessions and websocket readiness; exactly one
registered agent; personal dynamic random-code/arithmetic reply and visible
typing; a rendered reaction; two humans plus agent in group chat with an
actual mention; a real agent Adaptive Card; DOCX and TXT uploads whose contents
the agent reads; an agent-generated downloadable document; and a genuine
restart retaining the original chat and file hashes. Independent feature
failures accumulate, rather than silently skipping the rest.

The restart helper uses the locked native Sandbox SDK stop/resume lifecycle,
not a Git operation or Terraform apply. It reads the guest kernel boot UUID
before/after and fails if it did not change; a paused guest or page reload
cannot masquerade as a reboot. Resume is attempted even if stop reports an
error, without swallowing that error. Invoke only against the explicitly
authorized simulator instance after obtaining the deployment owner's instance handoff.
The runner allows ten minutes for this lifecycle and records the helper's JSON
reboot evidence before checking retained chat/files. This mechanism is
implemented and unit-tested, **not yet certified against a live private
simulator**; if platform stop/resume preserves boot state, it fails explicitly.

The first human signs in as the configured Worker owner. In DEMO mode its
identity is UUIDv5(namespace `51f8c9cd-dcd4-4c33-9ef6-48b4c5919185`,
`displayName.strip().lower()`), **not** the simulator database user ID. Its
authenticated `/api/me` identity must match the supplied deployed
`WORKER_OWNER_ID`. The second human is independent.

Adaptive Card acceptance sends exactly `/learning` in that owner's personal
chat. This supported production handler calls the actual Hermes runtime's
delivery-reference and native learning-ledger/approval endpoints and returns
an AdaptiveCard v1.4 headed `What I learned`. Even an empty native ledger emits
`Nothing new since the last digest.`; no fabricated learning entry or actionable
button is required. This proves native runtime/channel/card integration, **not
model inference**. The separate random challenge retains that inference check.
Wrong owner, missing native tools, runtime/auth failure, or a text/error reply
fails explicitly. No group-command/model-created-card guarantee is implied.
Real agent document download remains a separate mandatory criterion: the
currently unwired Hermes local-document channel capability does not get waived
by a successful learning card.

A random model challenge rejects canned static replies but **does not alone
prove which inference backend was used**. Retain the matching Hermes/Foundry
telemetry trace alongside browser results for a complete application claim.
Entra/MSAL sign-in must additionally be checked interactively by an assigned
user on the private Windows/Edge client; DEMO login does not certify Entra.

## Focused empirical probes

`probe_disk.py` runs on an authenticated operator host with the project's
existing locked Sandbox SDK. It preserves request/operation evidence and
returns nonzero on a real conversion failure:

```powershell
uv run python tests\e2e_private\probe_disk.py --subscription <UUID> --resource-group <research-RG> --group <research-group> --image <private-ACR/repository:tag> --identity-client-id <UAMI-client-UUID> --vnet-connection private --output .local\disk-conversion.json
```

The native v2 request is:

```json
{
  "source": {
    "kind": "registry",
    "imageUrl": "<private-ACR/repository:tag>",
    "managedIdentityClientId": "<UAMI-client-UUID>"
  },
  "customerVnetConnectionName": "private",
  "labels": { "name": "<unique-probe-name>" }
}
```

`customerVnetConnectionName` is **top-level**. The control request omitting it
failed with a denied public source IP despite identity and trusted bypass.
The request including it converted a VNet-built image with bypass `None`.
Do not infer that ACR managed identity alone supplies network reachability.

`image_probe\Dockerfile` provides a tiny real build payload. Run
`az acr build --registry <private-ACR> --agent-pool <VNet-pool> --image probe/private-build:1 --file tests\e2e_private\image_probe\Dockerfile tests\e2e_private\image_probe`.
The observed external operator could upload local context and queue the private
build without a Docker engine. Read the resulting digest from the build run;
private registry metadata queries from outside the VNet are a separate issue.

`probe_workload.py` runs **inside a Sandbox** inheriting a UAMI. It requests
tokens from the native identity endpoint without printing them, performs a real
Foundry challenge, and optionally posts authenticated telemetry to `/v2.1/track`.
`/v2/track` does not accept that Authorization header. An accepted ingestion
envelope is not the same evidence as later querying the stored event.

`probe_websocket.py --serve 8081` is an explicitly labeled transport experiment,
not simulator acceptance. Publish its native private port, then run
`probe_websocket.py --url https://<port-FQDN>` inside the VNet: it verifies TLS,
HTTP 101, handshake proof, and a masked-frame/echo round trip.

Local focused tests:

```powershell
uv run python -m unittest discover -s tests\e2e_private -p test_probes.py -q
```

## Evidence and limits (2026-11-13)

Live verified in Sweden Central: create Express with public access Disabled
from the outset; create linked groups directly without a public intermediate;
two groups sharing a delegated `/26` when connection writes are serialized;
Azure DNS resolution inside the Sandbox and Linux client; private trusted TLS;
public TLS reset; full WebSocket frame transport; trusted-service ARM import
(explicitly distinct from private build); private S1 build/push with bypass None;
private managed-identity disk conversion; boot and read its actual build marker;
private Foundry UAMI inference; authenticated private AMPLS ingestion; and
Graph accepting the private-only HTTPS SPA redirect URI. The temporary Graph
probe application was deleted immediately.

Custom VNet DNS inheritance inside the Sandbox, peak subnet capacity, isolated
environments sharing a subnet, and actual user MSAL login are **not empirically
certified** by those probes. A `/26` worked for this small experiment; the
Sandbox documentation recommends `/23` for headroom. It is not a capacity SLA.
The full six-stack application suite must still be run against deployed stacks;
transport fixtures and provisioned private endpoints are not substitutes.

Primary references:

| Source | Supported claims |
| --- | --- |
| [Sandbox private endpoints](https://sandboxes.azure.com/docs/sandboxes/private-endpoints) | Express binding, private domain/DNS, immutable group link, ingress distinct from egress |
| [Sandbox VNet](https://sandboxes.azure.com/docs/sandboxes/sandbox/vnet) | delegation, same region/subscription, reserved ranges, sizing guidance |
| [Sandbox egress](https://sandboxes.azure.com/docs/sandboxes/sandbox/egress) | opt-in proxy policy and inspection |
| [Sandbox disk images](https://sandboxes.azure.com/docs/sandboxes/disk-images) | custom OCI conversion and private registry reachability |
| [Sandbox ports](https://sandboxes.azure.com/docs/sandboxes/sandbox/ports) | port URL and publication concepts; consulted |
| [Sandbox ingress auth](https://sandboxes.azure.com/docs/sandboxes/sandbox/ingress-auth) | distinct transport authentication layer; consulted |
| [Sandbox identity](https://sandboxes.azure.com/docs/sandboxes/identity) | group UAMI inheritance and native token endpoint |
| [ACR private link](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-private-link) | default quick-build limitation with public access Disabled |
| [ACR VNet agent pools](https://learn.microsoft.com/en-us/azure/container-registry/tasks-agent-pools) | Premium, Sweden Central, subnet, outbound dependencies, scaling |
| [ACR import](https://learn.microsoft.com/en-us/azure/container-registry/container-registry-import-images) | Docker-free ARM import and explicit trusted-service prerequisite |
| [AMPLS configuration](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/private-link-configure) | central scope, linked resources, private DNS zones |

These references were retrieved on 2026-11-13 with first-party web fetch /
Microsoft Learn fetch (one relevant page each); Sandbox pages report update
2026-11-09. Public first-party sources and isolated Azure observations were
the requested evidence classes; no internal or community source was used.
The attempted Sandbox `/sandbox/ingress` and `/sandbox/private-ingress` URLs
returned 404 and support no claim; the actual `/private-endpoints` page above
was then retrieved. Retail cost estimates must be distinguished from measured
Azure billing and include persistent Bastion/NAT/PE/disk charges.
