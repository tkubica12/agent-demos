# Private rollout and operations

Run from the immutable initialized source directory. All example identifiers
are placeholders; replace them in private inputs. Review tool output and stop
on nonzero exit before the next stage.

If authentication is needed, use `az login --use-device-code --tenant <tenant>`.
Wait for the human to complete login, report a problem or request a fresh code
after expiry; never automate the sensitive login step. Inspect the effective
`pyproject.toml` uv index and environment before resolving packages. Preserve
approved feeds and existing lockfiles; `uv` does not inherit pip settings.
For online prerequisite checks supply that approved `-PythonIndexUrl` and
the explicit `-SubscriptionId`. Do not run `az account set`.

```powershell
.\scripts\Test-DeployPrerequisites.ps1 -SkipNetwork
# From the installed skill folder, before changing source directory:
$work = .\scripts\Initialize-Workspace.ps1 -PackageRoot 'D:\customer-package' -WorkRoot 'D:\customer-work'
Set-Location $work.Source
$config = $work.Config
$state = $work.State
$canary = 'agent-1'
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary plan
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary prepare
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary infrastructure
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary identities
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary images
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary deploy
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary verify
```

Skip `identities` for demo. `plan` validates inputs without provisioning;
add `--offline` before `plan` for a no-Azure-read configuration rehearsal.
For shared Foundry run the deployment-wide `shared` stage before the first
`infrastructure` command; see [exact shared commands](foundry.md).
`prepare` captures ownership and generates private secrets/state.
`infrastructure` provisions the private platform, managed identities and
Sandbox groups; `images` records digest-pinned prebuilt images without building
or provisioning ACR. `deploy` converts/pulls those images and configures
services. `all` executes stages and loopback lifecycle readiness, not the
browser acceptance path.

After the canary passes customer acceptance, select additional stacks using
repeated `--stack` arguments, or omit selection for all configured stacks:

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-2 --stack agent-3 all
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack agent-2 --stack agent-3 verify
```

Each stack has simulator/gateway/runtime Sandboxes, managed identities,
persistent Data Disks, storage, messaging and monitoring in its own resource
group. Suspend/delete policies must stay explicitly disabled. Customer-owned
network/DNS/monitoring resources may be referenced, never imported into owned
state. Shared Foundry is the explicit exception to per-stack isolation.

## Failure handling

Stop after the first failing stage. Keep the same private state and ownership.
Graph 403: use the [admin request](authentication.md).
Source mismatch: choose a matching [release](releases.md), never relabel it.
Private DNS/TLS/network failure: validate VNet access and approved CA/proxy;
do not switch to public or disable certificate verification.
Quota/capacity failure: inspect region/model quota and the shared pool before
changing allocation. ACK/HTTP 409, reply timeout, widget or file failures:
capture actual user behavior, stack/release and redacted logs, and stop rollout.
Do not turn an incomplete test into a passing acceptance receipt.

A deployment lock prevents overlapping writers. Investigate its owner before
removing a demonstrably stale lock; never run parallel writes to one stack.
Image changes may replace containers but should retain owned data volumes;
check volume ownership, private URL and redirects afterwards.

## Teardown

Only after explicit authorization and confirmation of exact owned stack:

```powershell
uv run python -m scripts.deploy_simulator_environment --config $config --state-root $state --stack $canary destroy
```

Preserve state until Azure and Graph cleanup both complete. Directory failure
can leave apps behind after resource removal; retry with the appropriate admin.
Never delete existing customer networking, shared services or another stack.
