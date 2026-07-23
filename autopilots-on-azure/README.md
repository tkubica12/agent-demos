# Autopilots on Azure

Run durable digital Workers in Azure Container Apps Sandboxes and expose them through Microsoft Agent 365.

OpenClaw and Hermes share one hosting, identity, networking, and tool-access pattern. Hermes additionally demonstrates Role Blueprints, Personal Memory, Private Playbooks, native skill evolution, Dreaming, and Collective Learning Review.

## Current system

```text
Teams / Agent 365 / direct invoke
                |
                v
      per-Worker bridge Container App
                |
                v
         ACA Sandbox + Data Disk
        /                       \
 OpenClaw Gateway          Hermes Worker
                                  |
                   loopback identity adapter
                    /           |          \
          private incidents   shipments   Work IQ
```

| Capability | Current implementation |
| --- | --- |
| Microsoft 365 installation | Agent 365 only |
| Runtime hosting | ACA Sandboxes with persistent Data Disks |
| Models | Sweden Central Foundry `gpt-5-6-terra` |
| Application region | North Europe Container Apps and ACR |
| Autonomous authorization | Agent Identity federation from Sandbox managed identity |
| Worker-owned Microsoft 365 data | Agent User |
| Private tools | VNet-only MCP plus Entra app roles |
| Public tools | Entra-protected HTTPS MCP |
| Worker learning | Personal Memory, Private Playbooks, Role Skills, Candidate Improvements |
| Shared learning | Ed25519-attested Learning Packets and reviewed GitHub Promotion |

The live demonstration has two isolated Hermes Workers, `hermes` and `hermes2`, using the same Junior Project Manager Role Release. Their Agent 365 platform blueprints and bridges remain isolated under [ADR 0014](docs/adr/0014-per-worker-agent365-blueprints-and-bridges.md).

## Quick check

```powershell
Set-Location .\autopilots-on-azure
uv sync

uv run python -m scripts.demo_ops status --runtime openclaw
uv run python -m scripts.demo_ops status --runtime hermes --state-name hermes
uv run python -m scripts.demo_ops status --runtime hermes --state-name hermes2
```

Run a fresh direct smoke:

```powershell
uv run python -m scripts.demo_ops smoke `
  --runtime hermes `
  --state-name hermes2 `
  --message "Reply exactly: hermes2 ready"
```

## Documentation

| Document | Use it for |
| --- | --- |
| [SPEC.md](SPEC.md) | Product requirements, terminology, architecture, security, and lifecycle contracts |
| [DEPLOYMENT.md](DEPLOYMENT.md) | First deployment, additional Workers, updates, validation, and cleanup |
| [DEMO.md](DEMO.md) | Teams demonstrations, memory/skills, Dreaming, and Collective Learning Review |
| [Hermes on Azure visual overview](docs/hermes-on-azure-overview.html) | Offline presentation-ready HTML explanation of architecture, identity, memory, learning, Promotion, and Worker Refresh |
| [Hermes learning source deep dive](docs/hermes-learning-deep-dive.html) | Interactive, source-cited explanation of memory, skills, Dreaming, learning governance, packets, and Worker Refresh |
| [PLAN.md](PLAN.md) | Delivery status, history, next work, and deferred items |
| [`docs\adr`](docs/adr) | Decisions and reconsideration triggers |
| [`docs\runbooks`](docs/runbooks) | Detailed identity or preview-service procedures |

## Repository map

```text
bridge\                    Agent 365 and direct-invoke bridge
bridge\runtime\            OpenClaw and Hermes adapters
runtimes\openclaw\         OpenClaw Sandbox image
runtimes\hermes\           Hermes Sandbox image and learning governance
blueprints\                Git-backed Role Blueprints
autopilots_identity\       Agent Identity and Agent User token exchange
private-incidents-mcp\     VNet-only Entra-protected MCP
public-shipments-mcp\      Public Entra-protected MCP
agent365\                  Agent 365 Tooling manifest
terraform\platform\        Shared Azure platform
terraform\apps\            Per-Worker application workspaces
scripts\                   Repeatable operator automation
tests\                     Runtime, identity, learning, and deployment tests
```

## Important boundaries

- A **Role Blueprint** is the shared Git definition of a job.
- An **Agent 365 platform blueprint** is the Microsoft 365 endpoint and permission envelope for a deployed Worker.
- Personal Memory, Private Playbooks, and Work History never enter Collective Learning Review.
- Candidate Improvements remain local until a human-reviewed Promotion.
- `/learn <instruction>` enters one explicit, transactional Hermes learning turn; ordinary prose is never keyword-scanned into a hidden second model call.
- Teams delivers direct messages, explicit mentions, and targeted activities; it does not provide every unmentioned channel message.
- Direct `hermes --cli` skill writes receive provenance on the next bridged turn or Dreaming run.
