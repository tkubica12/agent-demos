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
dependencies, a private Windows side-test jump host, and Basic Bastion.
Only Bastion and the outbound NAT have public IPs; the jump host has no public
ingress. It is an optional manual GUI tool, not part of any deployed agent or
skill. Its system identity has no operator role grants. The landing zone does
not provision a Linux runner or its former operator identity/roles.

**Existing Linux deployments are on deletion HOLD until protected state export
is verified and the owner explicitly authorizes retirement.** Do not apply this
changed root to such a deployment before that authorization. Preserve original
command failure receipts and verify exact resource absence after authorized
cleanup; do not rerun stuck guest workloads.

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
`clients.json` contains the Windows/Bastion IDs and the Windows private address.
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

Windows side-test preparation is explicit:

```powershell
az vm run-command invoke -g <network-RG> -n <windows-VM> --command-id RunPowerShellScript --scripts "@tests\e2e_private\prepare_windows.ps1"
```

Run browser acceptance on the operator host through authorized private routes
or the explicit temporary-public phase below. Linux operator hosts can use
`prepare_linux.sh` to inspect approved-feed versions and `install_linux_tools.sh`
only after reviewing those pins. Python/npm tools use the approved Microsoft
feed; Chromium uses the existing Playwright version's installer. This needs
approved outbound routes to package sources/browser distributions; private
ingress does not imply an offline environment.

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

The runner can also execute on the operator machine without a test VM.
For an explicitly approved **empty test environment only**, set root TOML
`network_mode = "temporary-public"` and `credential = "azure-cli"`.
It verifies global DNS, a global trusted TLS peer and successful readiness,
then runs the same real file/widget/native provenance and restart gates.
This phase is labelled temporary-public, never private certification.
After the deployment enables its Private Endpoint and disables public access,
run the outside `--public-negative` probe against the actual current origin.
Customer/skill deployments must start private; do not use this test phase there.

Private AMPLS query access is not waived for outside-browser testing. An
authorized, existing in-VNet Sandbox can perform the metadata-only query with
its **own managed identity**, while the operator uses Azure CLI solely for the
Sandbox API and ARM metadata. Add the following to that stack's TOML:

```toml
[stacks.query_sandbox]
subscription_id = "<UUID>"
resource_group = "<test-stack-RG>"
group = "<authorized-test-Sandbox-group>"
sandbox_id = "<actual-running-Sandbox-UUID>"
region = "swedencentral"
python_path = "/app/.venv/bin/python"
```

The guest interpreter must contain `azure-identity`; its identity needs
Monitoring Reader on the exact Application Insights component and Log Analytics
Reader on the exact workspace, not a broad Reader grant. The operator needs the
existing Sandbox execution permission. The official SDK runs the pinned
metadata-only helper, proves private query DNS/TLS, queries only the exact native
trace, and verifies the observed Foundry host privately. No operator tokens,
evidence keys, file contents or submission tokens go to this helper. Missing
permission, runtime or query evidence fails explicitly. Each invocation must
return its own completion nonce; the SDK's default exit zero alone is not proof.
Without this option,
the original direct private managed-identity query path remains required.

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
gateway_url = "https://<native-private-gateway-FQDN>"
runtime_url = "https://<native-private-runtime-FQDN>"
source_commit = "<operator-approved-40-character-source-SHA>"
widget_attestation_path = "/var/lib/<private-state>/widget-attestation.json"
evidence_key_env = "PRIVATE_AGENT_1_INTERNAL_KEY"
app_insights_resource_id = "/subscriptions/<UUID>/resourceGroups/<stack-RG>/providers/Microsoft.Insights/components/<component>"
# Explicit operator-approved argv that really restarts the simulator.
# Do not use page reload, a health check, or an arbitrary sleep here.
restart_command = ["uv", "run", "python", "tests/e2e_private/restart_sandbox.py", "--subscription", "<UUID>", "--resource-group", "<stack-RG>", "--group", "<simulator-group>", "--sandbox-id", "<simulator-Sandbox-UUID>"]
```

Supply each existing DEMO code and matching internal `API_SERVER_KEY` through
the named private environment variables without printing them. The evidence
collector uses the explicitly selected credential or the query Sandbox's own
managed identity for the configured Application Insights resource; it needs
exact-resource query roles, private AMPLS DNS/routes, and no exported operator
credentials.
Repeat `[[stacks]]` for every deployed stack. From the extracted
`autopilots-on-azure` directory inside the VNet:

```powershell
uv run python tests\e2e_private\run.py --config .local\private-acceptance.toml
```

On the operator host, set `PRIVATE_PLAYWRIGHT_MODULE` as above first.
No Git access is used. The output is `private-network.json`, per-stack
`result.json`, a final `summary.json`, test upload fixtures, and a screenshot
after authentication. Actual agent-generated DOCX/TXT browser downloads are
also retained, with their fresh nonces, message/file IDs, sizes and SHA256 hashes.
Access codes and bearer tokens are not recorded;
browser traces/HAR are deliberately disabled. Keep screenshots/chat evidence
private even though this runner uses test data.

The same source-pinned run separately reports live native widget acceptance.
Before sending a widget prompt, it requires the deployment's guest-byte
attestation: `source_commit`, `runtime_release`, `bundled_skills_path`, and
`files` mapping `bridge/html_widgets.py` and
`runtime_skills/teams-html-widget/SKILL.md` to `guest_path`, `sha256` and
`git_blob_sha1`. The bridge must match blob
`63582be6a82b4e0ff261cc1505305372e87c74df`; the unchanged skill must match
`b899de1ab25ad95f4e6719b3b3b48b24689f8f40` in the selected bundled skill directory.
This validates the deployment's actual guest attestation, not local file copies.

A fresh, independently logged-in DEMO browser identity creates a personal chat
with exactly that human and the agent (the simulator otherwise reuses personal
chats for the same two members). It requests a real Hermes widget, checks the `allow-scripts`
iframe initialization/resize and local recompute, then clicks its actual action.
The accepted invoke, disabled button, same-chat follow-up and same-token
`already_submitted` replay are checked. Authenticated `/internal/turns/evidence`
links both actual replies to their native runs; the follow-up must reference
the exact opaque accepted invoke, and the replay must not have a native reply.
Both completed runs require actual runtime metadata and the same strict
Foundry dependency/served-model/positive usage proof used for files.
File turns still require their real terminal start/completion; widgets do not
need a dummy file or terminal call. Missing linkage fails explicitly.

`widget-result.json`, separate producing/follow-up evidence, `live-widget.png`
and `live-widget.gif` remain outside Git. GIF encoding additionally requires
an approved `ffmpeg` on the private client; absence is an explicit failure.
Only current browser screenshots are encoded. Widget tokens, submitted values,
HTML bodies and HTTP traces are not persisted.

An operator outside the VNet uses the explicit temporary-public test phase or
an authorized tunnel/private route, never a Linux VM execution adapter.
Use the full project with its frozen dependencies restored for native SDK
lifecycle calls, not a standalone copy of the browser scripts.
Earlier managed-command failure receipts remain private evidence until exact
authorized cleanup is verified. HTTP202 deletion acceptance alone is not
resource removal; the exact GET must return HTTP404.

Checks include RFC1918-only DNS and verified HTTPS; anonymous API rejection;
two separate real DEMO browser sessions and websocket readiness; exactly one
registered agent; personal dynamic random-code/arithmetic reply and visible
typing; a rendered reaction; two humans plus agent in group chat with an
actual mention; a real agent Adaptive Card; DOCX and TXT uploads whose contents
the agent reads; actual agent-generated DOCX and UTF-8 TXT browser downloads; and a genuine
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
This is the rendering criterion only. The full bridge checklist's action on a
real available proposal is separate: an empty ledger leaves that action
**NOT RUN**. Never seed a proposal to obtain a passing action. The automated
report explicitly leaves real proposal actions and interactive Entra sign-in
not run. Native Foundry/tool provenance is required for both generated-file
turns, not inferred for other turns.
Its `pass` means only `private-automated-browser-subset`, not blanket simulator,
bridge, Entra, or customer certification.
Real agent document download remains a separate mandatory criterion. Enable
`GENERATED_CHAT_FILES_ENABLED=true` on both gateway and runtime, with matching
`WORKER_ID` and `API_SERVER_KEY`, and deploy the native generated-file runtime
bundle. The runtime supplies an exclusive per-turn output directory and the
standard `MEDIA:"<absolute-file>"` contract; the runner never prescribes a fixed
host path or substitutes fixtures/model links. It requests one real DOCX and
one UTF-8 TXT using independent fresh nonces and actual terminal/file tools.
Each is downloaded through its rendered chat attachment button, saved from
the browser, validated against Word document text or strict UTF-8 text, and
compared to the authenticated API bytes. ZIP comments/metadata containing the
nonce cannot substitute for Word text. A successful learning card does not waive
either file check. `verify_native_evidence.py` immediately reads the authenticated
gateway `/internal/generated-files/evidence?conversation_id=...` route, using
only `X-Autopilot-Key`, and matches exact request/reply Activity IDs, filename,
size and browser SHA. This simulator's `make_activity` and Connector
ResourceResponse use message IDs as those Activity IDs; no guess based only
on timestamps or filenames is permitted. It then reads the authenticated
runtime `/internal/runs/<nativeRunId>/evidence` route: exact completed run,
available served runtime metadata, and actual terminal start/completion events
with matching tool-call ID and `error=false` are mandatory. Capture the
64-delivery process-local ring before restart/eviction; missing, expired or
mismatched records fail explicitly. `/diag/teams` is not used or enabled.

Native `custom` or a configured `azure-foundry` label alone is **not** backend
proof. The collector queries Application Insights by the exact trace ID and
requires both a successful outgoing HTTP dependency to a first-party Foundry
host and a successful `chat` dependency with served response model and positive
input/output usage. That observed host must also pass private DNS/TLS checks.
Gateway/runtime must actually export these dependencies with the required
permissions and `OTEL_TRACES_SAMPLER_ARG=1.0`; missing instrumentation or query
access fails rather than substituting configuration. The helper uses the
[Microsoft Entra-authenticated Application Insights query API](https://learn.microsoft.com/azure/azure-monitor/app/azure-ad-authentication#query-application-insights-by-using-microsoft-entra-authentication).
Metadata reads create/export nothing and store no outputs, reasoning, tool
arguments, file claims, headers or keys. Each file check retains its safe
native/Foundry JSON beside the actual browser download.
The generated-file implementation is available but this flow is **not yet
live-certified against the private deployment**.

A random model challenge rejects canned static replies but **does not alone
prove which inference backend was used**. The generated-file turns collect
their own native/Foundry proof; complete application claims still require the
remaining simulator/bridge checklists and independent checks.
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
