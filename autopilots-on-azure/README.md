# Hermes on Azure

A Junior Project Manager digital Worker: Hermes 0.19.0 in Azure Container Apps Sandboxes, Microsoft Agent 365 for Teams and Microsoft 365, and Foundry for Entra-authenticated inference.

Each Worker owns its identity, persistent state, memory, and skills. Local learning becomes shared behavior only through signed Learning Packets, human-reviewed Git Promotion, and Worker Refresh.

```text
Teams / Agent 365 / operator
            |
     gateway Sandbox ── Service Bus
            |
     Hermes + Data Disk
       |            |
 native Foundry   loopback identity adapter
                    |
          private MCP / public MCP / Work IQ
```

## Start

From the repository root, with existing Worker state and an Azure login:

```powershell
Set-Location .\autopilots-on-azure
uv sync --frozen --index-url https://packagefeedproxy.microsoft.io/pypi/simple
uv run python -m scripts.demo_ops
uv run python -m scripts.demo_ops smoke --state-name hermes2 `
  --message "Reply exactly: Hermes ready" --timeout 600
```

The no-argument command checks `hermes`; use `status --state-name hermes2` for the other Worker. User scheduling and Service Bus Dreaming are enabled on `hermes2`, disabled on `hermes`.

## Read next

| Document | Contents |
| --- | --- |
| [Deployment](DEPLOYMENT.md) | Existing-Worker updates, authentication, validation, diagnostics, cleanup |
| [Demo](DEMO.md) | Prompts and observable results |
| [Specification](SPEC.md) | Domain, identity, lifecycle, learning, and current limitations |
| [Identity runbook](docs/runbooks/identity-mcp.md) | Consent, federation, and MCP troubleshooting |
| [Architecture decisions](docs/adr) | Current choices and rejected alternatives |
| [Visual overview](docs/hermes-on-azure-overview.html) | Architecture and learning overview |
| [Architecture deep dive](docs/hermes-architecture-deep-dive.html) | Hosting, identity, and lifecycle |
| [Learning deep dive](docs/hermes-learning-deep-dive.html) | Memory, provenance, review, and refresh |

**Boundaries:** the gateway stays awake; runtime compute is OnDemand. Foundry registers an external agent, not Hosted Agent compute. The supported deployment path updates existing Workers; fresh Agent 365 bootstrap is unresolved. Shared group conversations are not private storage. See the specification for the consolidated limitations.
