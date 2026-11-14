# Hermes on Azure

A Junior Project Manager digital Worker: Hermes 0.22.6 in Memory-suspended Azure Container Apps Sandboxes, Microsoft Agent 365 for Teams and Microsoft 365, Service Bus for scheduled work, and Foundry `gpt-6.1-sol` with Entra-authenticated inference.

Each Worker owns its identity, persistent state, memory, and skills. Hermes learns natively; the Worker owner reviews each change, and local learning becomes shared behavior only through signed Learning Packets, human-reviewed Git Promotion, and Worker Refresh.

Workers can answer with [model-generated interactive HTML widgets](DEPLOYMENT.md#generated-html-widgets-and-hermes-selections). The [Agent Chat Simulator](chat-simulator/README.md) emulates the Teams channel so Workers can be [deployed and tested](docs/simulator-deployment.md) without Agent 365 or real Teams.

![Architecture: Agent 365, or the Agent Chat Simulator for testing, reaches a thin gateway, which handles the Teams turn and uses native Runs over OnDemand HTTPS. Service Bus is reserved for scheduling; the runtime calls Foundry and MCP directly.](docs/assets/architecture.svg)

## Start

From the repository root, with existing Worker state and an Azure login:

```powershell
Set-Location .\autopilots-on-azure
uv sync --frozen
uv run python -m scripts.demo_ops
```

The no-argument command checks the `hermes` Worker. Validate model and tool behavior by messaging the Agent User in Teams; there is no general model-invocation HTTP endpoint. The deployment pins Hermes 0.22.6 with native learning, user scheduling, Service Bus Dreaming, scheduling receipts, and Blob-volume backups.

## Read next

Three architecture documents, each an interactive HTML page (hover or pin any diagram component) with an offline copy and a printable PDF:

| Document | Contents | Offline and print |
| --- | --- | --- |
| [Architecture](docs/architecture.html) | Operating model, conversation turns, identities, documents, state and recovery | [Standalone](docs/architecture.standalone.html), [PDF](docs/architecture.pdf) |
| [Collective learning](docs/collective-learning.html) | Memory layers, native learning and owner review, signed packets, Git Promotion, Worker Refresh | [Standalone](docs/collective-learning.standalone.html), [PDF](docs/collective-learning.pdf) |
| [Azure implementation](docs/azure-implementation.html) | Deployed topology, roles, Terraform layers, suspend behavior, telemetry, production network | [Standalone](docs/azure-implementation.standalone.html), [PDF](docs/azure-implementation.pdf) |

Then:

| Document | Contents |
| --- | --- |
| [Architecture decisions](docs/adr) | Current choices and rejected alternatives |
| [Deployment](DEPLOYMENT.md) | Worker updates, fresh Worker bootstrap, validation, diagnostics, teardown |
| [Simulator deployment](docs/simulator-deployment.md) | Isolated multi-Worker Sandbox deployment without Agent 365 or real Teams |
| [Demo](DEMO.md) | Prompts and observable results |
| [Agent Chat Simulator](chat-simulator/README.md) | Neutral collaboration UI, Entra sign-in and explicit expiring test-data demo mode |
| [Specification](SPEC.md) | Domain, identity, lifecycle, learning, and current limitations |
| [Identity runbook](docs/runbooks/identity-mcp.md) | Consent, federation, and MCP troubleshooting |

After editing a document, refresh, validate, bundle, and print it from `docs` (needs Playwright with Chromium or Edge):

```powershell
Set-Location docs
node assets\sync-head.js architecture.html
node assets\validate.js architecture.html
node assets\bundle.js architecture.html
node assets\export-pdf.js architecture.standalone.html
```

## Boundaries

- Gateway, runtime, and MCP Sandboxes suspend to Memory after five idle minutes. Teams turns go straight to the gateway, which calls native Runs over OnDemand HTTPS. Service Bus carries only cron, Dreaming, and document retries ([ADR 0015](docs/adr/0015-service-bus-backed-hermes-cron.md#receiver-timing)).
- Source updates keep compute IDs, URLs, and Agent 365 identities. Only the runtime dependency base needs ACR. Storage, Service Bus, ACR, and monitoring cost money even while CPU and memory are suspended.
- Native backups restore the Worker home; a separate gateway journal keeps scheduling receipts and conversation bindings.
- On 2026-11-05 a real Teams warm check returned two MCP tool results in 26.7 s and a model reply in 11.8 s. These are observations, not an SLA.
- Foundry registers an external agent, not Hosted Agent compute. Fresh Teams package upload stays manual, and group chats are not private storage. [SPEC.md](SPEC.md#current-limitations) lists the remaining limits.
- The simulator deployment is a release candidate. Login, a real model reply, file upload/download, and a local script that edits an uploaded file passed on 2026-11-14; the browser widget/download check and Entra browser acceptance are still open. See [current validation status](docs/simulator-deployment.md#current-validation-status).
