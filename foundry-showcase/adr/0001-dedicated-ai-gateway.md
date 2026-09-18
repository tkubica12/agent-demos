# 0001: Showcase the dedicated AI Gateway tier on a separate traffic path

Date: 2026-09-16

Status: Accepted and implemented. Dedicated gateway, isolated model publication,
governed MCP, and native OTLP are deployed and Terraform-managed. Real model,
request-rate, Chat Completions cost-budget, tool-denial, and telemetry evidence
is verified; protocol-specific limits are recorded below.

## Context

Foundry Showcase currently calls the Foundry project directly. Its hosted agents
use native Responses, Memory, Toolbox, authenticated A2A, and governed MCP write
approvals. Neither the AG-UI BFF nor the platform Invocations gateway is an Azure
API Management AI Gateway.

The dedicated AI Gateway tier is a separate public-preview product experience
at [ai.gateway.azure.com](https://ai.gateway.azure.com). It publishes model and
MCP endpoints, applies structured policies, and provides gateway observability.
It must be part of the showcase, but not at the expense of the existing native
Foundry capabilities or their authentication design.

The current authentication contracts are different:

| Path | Current dedicated-tier authentication |
|---|---|
| Administrator to gateway management | Microsoft Entra ID |
| Application to gateway runtime | Gateway-scoped runtime access key |
| Gateway to a supported Azure backend | Managed identity |

Managed identity on the backend leg does not make the whole path secretless.
OpenAI-compatible Responses support does not establish compatibility with the
complete Foundry project API, native tool orchestration, or approval
continuations. Neither a roadmap item nor an internal design is proof of a
released runtime or ARM contract.

## Options considered

| Option | Assessment |
|---|---|
| Replace the existing agents' Foundry endpoint with the dedicated gateway | Rejected for now. Inbound authentication and preservation of native Foundry features are not established. |
| Use conventional APIM with Entra validation | Viable for different requirements, but rejected for this showcase addition because it would not demonstrate the requested dedicated tier. |
| Wait for full compatibility before adding any gateway | Rejected. It would omit useful governance capabilities that can already be demonstrated independently. |
| Deploy the dedicated tier alongside the existing agent path | Selected. Demonstrates the real product now while keeping existing agent behavior and identity intact. |

## Decision

Deploy a clearly named and tagged Foundry Showcase gateway in Sweden Central,
using the dedicated AI Gateway tier rather than substituting Basic v2 or another
conventional APIM SKU.

Keep the main, helper, optimizer, and knowledge agents on their current
authentication and routing paths. Do not attach the gateway to the Foundry
account in a way that silently reroutes existing deployments or tools.

```text
Existing showcase path, unchanged
  User -> Foundry hosted agent -> native Memory / Toolbox / model / A2A
                                      |
                                      +-> approved case writes

Separate gateway demonstration
  Presenter / gateway playground -> dedicated AI Gateway
                                      |
                                      +-> isolated showcase model deployment
                                      |      using gateway managed identity
                                      +-> explicitly selected MCP tools
                                      +-> policy decisions and telemetry
```

Use managed identity for supported Azure backends and least-privilege resource
permissions. Do not store gateway runtime keys in application configuration,
Terraform variables or state, generated environment files, or repository files.
Do not build a key-distribution service or authentication relay to make this
preview appear secretless. The operator CLI retrieves a gateway key through
Entra-authenticated ARM and holds it only in process memory. This administrative
demonstration is distinct from a secretless application runtime integration.

Publish only the assets needed for the demonstration. Gateway keys are currently
gateway-wide; budget overrides do not provide per-asset authorization. Do not
expose the existing case-write tool as an unapproved alternate write path.
Gateway tool filtering is defense in depth, not a replacement for the existing
human approval exchange.

### Backend ownership

The existing agents retain the shared `tomaskubica-foundry-resource` account and
`tomaskubica-foundry-project`. The gateway addition does not require moving or
duplicating that project, its agents, Memory, Toolbox, or connections.

For gateway model traffic, use the separate `fshow-aigw-models-si4ons` Foundry
account in the showcase resource group. The native import UI selects complete
accounts. Importing the shared account through that UI would publish
21 deployments, including unrelated image, audio, and expensive reasoning models,
to gateway-wide runtime keys. After considering shared-account reuse versus
low-capacity showcase-owned models, the isolated backend was selected. Subsequent
ARM investigation established individual deployment publication, but does not
change the deliberate ownership and cost boundary of the deployed design.

Its only deployment is `showcase-chat`, GPT-5.4-mini version `2026-03-17`, using
pay-per-token `GlobalStandard` capacity 10. The live backend reports 10 requests
and 10,000 tokens per minute. These throughput limits are not a spending cap.
Local-key authentication and dynamic throttling are disabled. The gateway's
system-assigned identity has Foundry User on this account only; this addition
does not grant it access to the shared Foundry account.

Use Terraform with `azapi` for the verified infrastructure contract and Python
for any supported imperative configuration between explicit infrastructure
layers. Do not guess an ARM resource type, API version, SKU literal, or policy
schema from the product's display name. If a preview surface cannot be automated
with a verified contract, record the gap explicitly instead of creating a
success-shaped placeholder.

### Deployed infrastructure contract

The native portal bootstrap created `fshow-aigw-si4ons` in
`rg-foundry-showcase-si4ons`, Sweden Central:

- `Microsoft.ApiManagement/service@2025-09-01-preview`, actual SKU `AIGateway`,
  system-assigned identity, runtime `https://fshow-aigw-si4ons.azure-api.net`.
- Companion `Microsoft.Web/connectorGateways@2026-05-01-preview`, same name.

Both were imported into `terraform/ai-gateway`; an in-place apply preserved the
gateway identity and added `app=foundry-showcase`, `layer=ai-gateway` ownership
tags. Terraform created the isolated Foundry account, model, scoped role
assignments, model provider/publication, MCP resource, and native monitoring.

The gateway configuration uses ordinary Entra-authenticated ARM under
`workspaces/default`, API `2025-09-01-preview`: `modelProviders`, provider child
`models`, `toolServers`, and `telemetryExporters`. These are verified live
resources with structured JSON policies, not conventional APIM XML policies.
AzAPI 2.11.0 validates the core gateway, Foundry, and RBAC resources. Embedded
schema validation is disabled for observed preview child resources, the
connector, and the OTLP-enabled Application Insights preview where bundled
schemas do not cover the live contract. No runtime keys are read or managed by
Terraform.

The separate `appi-fshow-aigw-si4ons` Application Insights resource has native
OTLP ingestion enabled and local authentication disabled. Azure provisions its
managed LAW, Azure Monitor workspace, DCR, and DCE. Terraform consumes the
returned endpoints and grants gateway MI Monitoring Metrics Publisher on that
DCR only. Logs, traces, and metrics use managed identity with payload capture
disabled. The original agent monitoring resource is unchanged.

Native first creation remains the verified bootstrap path. Terraform adoption,
updates, and backend creation are verified; recreating the complete dedicated
tier from an empty environment is not yet verified. After native gateway
bootstrap/adoption, publication, governance, and native monitoring are automated
in the same dedicated Terraform root; no custom collector or imperative relay
is needed.

A cosmetic telemetry credential audience edit through a full AzAPI PUT hit
preview validation about simultaneous nested and legacy identity
representations. The original working configuration was retained. Native
export is verified; arbitrary credential updates through PUT are not.

## Demonstration scope and evidence

Observed against the deployed resources on 16 September 2026:

| Capability | Live evidence and boundary |
|---|---|
| Dedicated tier and ownership | Live ARM `AIGateway` SKU, system MI, Sweden Central, showcase ownership tags, and companion connector |
| Model access | Real Responses and Chat Completions requests to isolated `showcase-chat` return `GATEWAY_OK`; backend uses MI with local keys disabled |
| Request rate | Configured 5 requests/minute per key; bounded burst produces gateway HTTP 429 and `Retry-After`. Approximate enforcement, not an exact request-number guarantee |
| Token governance | Configured 5,000 tokens/minute per key; real `policy tokenLimit` spans observed. Independent token-threshold rejection remains untested |
| Estimated-cost budgets | $0.05/calendar day per key; a temporary $0.000001 override produces real HTTP 403 `LLM cost quota is exceeded` on Chat Completions. Responses budget enforcement remains unverified |
| Per-key overrides | Bounded four-call probe, ETag-safe update/restoration preserving unrelated overrides, then temporary key revocation with HTTP 404 verification. No per-asset authorization claim |
| MCP governance | Public Microsoft Learn search succeeds. Selected-but-blocked fetch returns HTTP 200, MCP `isError: true`, `ToolNotAvailable` metadata. Unpublished code search returns HTTP 404 |
| Observability | Real `OTelLogs`, `OTelSpans`, request/token/cost policy spans, and MCP outcomes; actual `azure.ai_gateway.client.token.usage` and `.token.cost` samples in managed Prometheus |
| Existing agents | Endpoint/authentication implementation unchanged; existing Memory, Toolbox, A2A, and approved-write path are not rerouted or duplicated |

The no-argument `scripts/ai_gateway.py` performs real model and MCP requests and
bounded negative checks. `budget-demo --approve` exercises budget enforcement
with a disposable key, and `telemetry` reads native monitoring. See the project
[README](../README.md#dedicated-ai-gateway) for commands and permissions.

Rate counters and cost budgets use `Identity`, currently the runtime API key.
Backend `x-ratelimit-*` headers describe backend capacity, not the gateway policy.
Observed `x-cost-per-day-consumed` is an estimated consumption header, not a
verified remaining-budget or billing statement. Responses returned nonzero token
usage with a zero cost header; that alone does not establish enforcement parity
or a bypass. Additional Responses-only budget probes were inconclusive due to
initial key propagation and an ARM TLS timeout; temporary keys were revoked.

Telemetry queries correlate logs/spans by W3C trace ID. Recent metric sample
aggregates are gateway-wide and must not be presented as that trace's total
tokens/cost or the Azure bill. Ingestion is asynchronous.

Use only synthetic demonstration prompts and the smallest useful requests.
Estimated-cost budgets are not a strict Azure spending cap: provider pricing,
accounting delay, concurrent requests, and non-model infrastructure charges
remain relevant. Do not describe preview pricing as a guarantee of zero cost.

## Consequences and revisit conditions

The gateway becomes a real, separately demonstrated showcase capability, not
the enforcement boundary for all existing agent traffic. Presentation diagrams,
deployment status, and commands must make that distinction explicit.

Moving agent traffic is a later decision, gated on released inbound Entra
authentication, a documented supported integration for the native Foundry
features in use, and live regression evidence for Memory, Toolbox, A2A, and
approved-write continuation. A generic model request succeeding is insufficient.

The new tier must not be presented as feature-equivalent to every conventional
APIM SKU. No broad retirement of APIM AI functionality has been established by
the reviewed public evidence.

## Public evidence

- [Dedicated AI Gateway tier overview](https://learn.microsoft.com/en-us/azure/api-management/ai-gateway-overview)
- [Create a dedicated AI Gateway](https://learn.microsoft.com/en-us/azure/api-management/quickstart-ai-gateway-create)
- [Model and MCP access](https://learn.microsoft.com/en-us/azure/api-management/ai-gateway-manage-models-tools)
- [Current portal security and identity contract](https://ai.gateway.azure.com/docs/security-identity)
- [August 28, 2026 release: OTLP logs, traces, cost metrics and budget enforcement](https://ai.gateway.azure.com/docs/releases/2026-08-28)
- [September 10, 2026 release: per-key budget overrides and MCP tool selection/blocking](https://ai.gateway.azure.com/docs/releases/2026-09-10)
- [Microsoft Learn MCP: public read-only documentation tools](https://learn.microsoft.com/training/support/mcp)
- [Native Application Insights OTLP resource orchestration](https://learn.microsoft.com/azure/azure-monitor/containers/collect-use-observability-data)
- [Azure Monitor OTLP ingestion and Entra authentication](https://learn.microsoft.com/azure/azure-monitor/containers/opentelemetry-protocol-ingestion)

The portal release notes are newer than Learn passages that describe token-only
OTLP export. Use current release evidence and live behavior rather than repeating
that superseded limitation. Internal roadmap research informs investigation but
is intentionally not reproduced here or treated as an external commitment.
