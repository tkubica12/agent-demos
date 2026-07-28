# ADR 0001: Use standard Azure Container Apps for the bridge

## Status

Accepted.

Review when Azure Container Apps express reaches the capability and regional gates below.

## Context

The bridge was initially deployed to Azure Container Apps Express because it is a small public webhook receiver that should scale to zero and wake the selected autopilot runtime in ACA Sandbox on demand.

During Teams validation, inbound Teams messages reached the bridge `/api/messages` endpoint, but bridge replies failed. Diagnostics showed outbound HTTPS from ACA Express was intercepted by an ADC egress proxy that presented certificates issued by `CN=ADC Egress Proxy Root CA`. The root CA was not trusted by Debian or certifi in the bridge image, so Python HTTP clients failed TLS verification when calling Entra and Microsoft 365 messaging endpoints.

Research across Microsoft Learn, web search, and WorkIQ found no documented customer contract for ACA Express to retrieve/trust the ADC egress proxy root CA, and no documented ACA Express setting to disable the egress proxy or traffic inspection for container apps. ACA Sandbox egress policies have inspection controls, but those controls are not documented for ACA Express bridge apps.

Standard Azure Container Apps workload profile environments provide the supported networking and identity model for this bridge, regardless of whether the runtime behind it is OpenClaw, Hermes, or another future autopilot:

- public HTTPS ingress for Agent 365 / Teams Activity Protocol callbacks
- managed identity for Azure API calls
- ACR pull with managed identity
- documented outbound networking options through VNet, NAT Gateway, UDR, or Azure Firewall if needed later

## Decision

Deploy each bridge instance as a standard Azure Container App in a standard managed environment, not as an ACA Express app.

The bridge uses a user-assigned managed identity for Azure API calls and ACR pull. Terraform owns the standard bridge environment in `terraform\platform` and the per-autopilot bridge app, identity, and role assignments in `terraform\apps`.

## Consequences

- The bridge no longer depends on ACA Express preview egress behavior.
- The bridge no longer needs an Entra app registration/client secret for Azure Sandbox API calls.
- Standard ACA Consumption supports HTTP scale-to-zero, but Agent 365 Workers keep one lightweight bridge replica ready. A measured standard ACA cold start took about 16 seconds and exceeded the Activity Protocol response window before application acknowledgement code ran.
- The expensive Worker Sandbox remains independently scale-to-zero. This decision keeps ingress warm, not agent compute.
- If tighter egress control is needed later, use documented standard ACA networking features rather than undocumented Express proxy behavior.

## 2026-07-27 review: Express remains the preferred future serverless candidate

Microsoft now documents ACA Express as a preview tier with subsecond scale-from-zero based on prewarmed Sandbox pools. Microsoft also states that this architecture is specific to Express and isn't planned for standard ACA environments. That makes Express the most credible path back to a fully scale-to-zero bridge; the current always-ready standard bridge is a compatibility decision, not a permanent preference.

Express cannot host this bridge yet. The current preview lacks:

- app runtime and image-pull managed identity;
- VNet integration and private/routed outbound networking;
- health probes and KEDA-based autoscaling;
- the North Europe and Sweden Central regions used by this deployment;
- an SLA;
- a documented resolution for the ADC egress-proxy trust failure that originally caused this ADR.

Do not introduce an Express relay in front of the standard bridge merely to obtain faster acknowledgement. That would add another ingress, identity, retry, observability, and delivery boundary.

Reconsider this ADR when Express:

1. is available in the selected bridge region;
2. supports managed identity for Sandbox management and ACR pull;
3. supports the required VNet/private connectivity or an equivalent documented path;
4. provides health probes and the operational controls needed by this deployment;
5. has documented outbound TLS behavior that passes Entra, Graph, Agents SDK, and proactive-delivery tests without custom trust bypasses;
6. demonstrates Agent 365 cold-start acknowledgement comfortably inside the workload deadline across a repeatable sample, with a target p95 below five seconds;
7. has an acceptable preview/GA and SLA posture for the intended use.

If all gates pass, run the same attachment acknowledgement, proactive continuation, scheduling, identity, private MCP, and failure-recovery smokes against an Express candidate. Prefer `minReplicas = 0` only after those tests; otherwise retain one warm standard bridge replica.

## References

- [Azure Container Apps express overview](https://learn.microsoft.com/azure/container-apps/express-overview)
- [Azure Container Apps express FAQ](https://learn.microsoft.com/azure/container-apps/express-faq)
