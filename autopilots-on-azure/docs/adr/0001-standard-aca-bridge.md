# ADR 0001: Use standard Azure Container Apps for the bridge

## Status

Accepted.

## Context

The bridge was initially deployed to Azure Container Apps Express because it is a small public webhook receiver that should scale to zero and wake the selected autopilot runtime in ACA Sandbox on demand.

During Teams validation, inbound Teams messages reached the bridge `/api/messages` endpoint, but bridge replies failed. Diagnostics showed outbound HTTPS from ACA Express was intercepted by an ADC egress proxy that presented certificates issued by `CN=ADC Egress Proxy Root CA`. The root CA was not trusted by Debian or certifi in the bridge image, so Python HTTP clients failed TLS verification when calling Entra and Bot Framework endpoints.

Research across Microsoft Learn, web search, and WorkIQ found no documented customer contract for ACA Express to retrieve/trust the ADC egress proxy root CA, and no documented ACA Express setting to disable the egress proxy or traffic inspection for container apps. ACA Sandbox egress policies have inspection controls, but those controls are not documented for ACA Express bridge apps.

Standard Azure Container Apps workload profile environments provide the supported networking and identity model for this bridge, regardless of whether the runtime behind it is OpenClaw, Hermes, or another future autopilot:

- public HTTPS ingress for Teams and Bot Framework callbacks
- managed identity for Azure API calls
- ACR pull with managed identity
- documented outbound networking options through VNet, NAT Gateway, UDR, or Azure Firewall if needed later

## Decision

Deploy each bridge instance as a standard Azure Container App in a standard managed environment, not as an ACA Express app.

The bridge uses a user-assigned managed identity for Azure API calls and ACR pull. Terraform owns the standard bridge environment in `terraform\platform` and the per-autopilot bridge app, identity, role assignments, and Teams/Bot resources in `terraform\apps`.

## Consequences

- The bridge no longer depends on ACA Express preview egress behavior.
- The bridge no longer needs an Entra app registration/client secret for Azure Sandbox API calls.
- The app may not have the same Express-specific scale-to-zero behavior, but standard ACA Consumption still supports low-cost HTTP scaling.
- If tighter egress control is needed later, use documented standard ACA networking features rather than undocumented Express proxy behavior.
