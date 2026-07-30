# ADR 0020: Separate Adaptive Cards, generated web apps, and MCP Apps

- Status: Accepted
- Date: 2026-07-28

## Context

Hermes needs interactions richer than text. Three technologies address different problems:

- Teams Adaptive Cards are JSON message attachments rendered natively by Teams.
- MCP Apps are sandboxed HTML and JavaScript widgets delivered by remote MCP servers through `ui://` resources.
- A generated web application is a complete shared artifact with its own URL, identity boundary, runtime, and lifecycle.

These surfaces are not interchangeable.

Teams sends an Adaptive Card as an Activity attachment with content type `application/vnd.microsoft.card.adaptive`. The bridge must create and validate the attachment, handle `adaptiveCard/action` invokes, return the required `Action.Execute` response, update stale cards, and preserve a text fallback. No separate web host is required.

MCP Apps is a stable MCP extension identified by `io.modelcontextprotocol/ui`. A tool declares `_meta.ui.resourceUri`; the host reads self-contained HTML from the MCP server with `resources/read` on a `ui://` URI and renders it in a sandboxed iframe. Microsoft 365 Copilot supports this for declarative agents with remote MCP plugins. VS Code GitHub Copilot also supports it.

The current Hermes 0.18 MCP client does not negotiate the UI extension, preserve MCP Apps metadata for a host, read `ui://` resources, or render widgets. Agent 365 AI teammates packaged with `agenticUserTemplates` are Teams teammate identities, not selectable Microsoft 365 Copilot declarative agents. Creating a parallel declarative agent would demonstrate the MCP server but would not make the widget part of the Hermes AI teammate experience.

Rich generated applications are broader than either chat surface. Hermes can write a complete web app, but serving it from the Worker process would mix user workloads with agent state and lifecycle. Azure Container Apps Express is agent-oriented but currently lacks required Entra authentication, managed identity, secrets, networking, regions, and other enterprise controls. Azure Container Apps Sandboxes already provide isolated execution, authenticated ports, principal allowlists, egress policy, secrets, auto-suspend, snapshots, and lifecycle operations.

## Options considered

### Let Hermes author arbitrary Adaptive Card JSON

Rejected. Schema validation alone does not prevent Hermes from inventing action verbs, callback data, operation identifiers, URLs, or oversized and inaccessible layouts. Prompt injection could convert untrusted content into consequential action payloads.

### Use reviewed cards only

Too restrictive for informational layouts. It is safe for actions but unnecessarily limits status summaries, facts, tables, and visual explanations.

### Typed action templates plus a constrained display DSL

Selected for A16. Reviewed templates own consequential behavior. A bounded display-only DSL gives Hermes control over safe content and presentation without exposing raw card structure or executable action data.

### Use MCP Apps as the universal UI

Deferred. MCP Apps is the strongest inline rich-UI surface for compliant hosts, but neither the current Hermes MCP client nor the Agent User Teams conversation renders it. A parallel declarative agent would create a second product identity and conversation rather than improving Hermes.

### Generate and host complete applications in the Hermes Worker

Rejected. The Worker Sandbox owns agent compute and private state, not arbitrary user-facing web processes. App failures, dependencies, ingress, and lifetime must not affect the Worker.

### Deploy generated applications to ACA Express

Deferred as the preferred future fast path. Express is operationally attractive, but its current preview lacks the required enterprise identity and networking features.

### Deploy generated applications to child ACA Sandboxes

Selected for A17. Microsoft publishes an MIT `aca-sandboxes` skill with web-app, coding-agent, authenticated-port, egress, secret, snapshot, and lifecycle workflows. A child sandbox preserves Hermes's ability to create and improve code while isolating the served application.

## Decision

1. A16 covers governed Teams Adaptive Cards only.
2. Consequential cards use reviewed typed templates. Hermes may provide bounded text, labels, choices, facts, and locale; it never provides action verbs, operation IDs, tokens, recipient bindings, callback URLs, or raw action data.
3. Informational cards use a constrained display-only DSL compiled and schema-validated by reviewed code. Arbitrary model-authored Adaptive Card JSON is not delivered.
4. The bridge is the Teams host adapter. It owns card attachment creation, Activity Protocol delivery, `Action.Execute` and fallback handling, update/replace behavior, action authentication, idempotency, and text fallback.
5. A small Hermes skill may teach when to use each interaction and how to populate the typed contract. Skills do not implement rendering or action security.
6. Direct reply and proactive delivery are separate capabilities. A16 live-validated direct card rendering, replacement, and Hermes continuation. Proactive delivery previously stripped card attachments, so that path retains suggested-action/text fallback until independently proven.
7. A17 covers Hermes-generated web applications.
8. Hermes creates and iterates source in its private workspace. A governed deployment wrapper creates a child sandbox, transfers the reviewed artifact, runs tests, starts the app, and exposes an Entra-authenticated port to explicit participant email addresses supported by the current Sandbox SDK.
9. Generated applications run in a dedicated Sandbox Group. The Worker Agent Identity receives only Sandbox Group Data Owner on that group. Deny-default egress, quotas, owner labels, five-minute auto-suspend, native 24-hour post-suspension retention by default, explicit 1/6/24/72-hour renewal, and deterministic deletion are mandatory.
10. Generated-app ports use `activationMode: OnDemand`. The Sandbox ADC proxy performs Entra authentication, resumes idle compute, and forwards the request. The URL is the native Sandbox URL; the bridge manages lifecycle but never hosts or proxies application traffic.
11. Ephemeral generated apps remain distinct from maintained applications. Promotion requires source review and deployment to standard ACA with Easy Auth, managed identity, durable observability, and operations.
12. Do not create an A18 MCP Apps milestone. Keep MCP Apps as a strategic watch item.
13. Re-evaluate MCP Apps when the Hermes/Agent User client path:
    - negotiates `io.modelcontextprotocol/ui`;
    - preserves tool `_meta.ui.resourceUri` and structured fallback;
    - reads `ui://` resources;
    - renders sandboxed widgets in the Hermes conversation;
    - forwards authenticated widget tool calls without a parallel declarative agent.
14. Re-evaluate ACA Express when Entra authentication, managed identity, secrets, required regions, networking, and lifecycle controls satisfy the A17 contract.

## Consequences

- Simple interactions stay native to Teams and require no hosting.
- Consequential actions remain deterministic and auditable.
- Hermes retains meaningful flexibility through a display DSL and through full generated application code.
- Rich applications can use any suitable web framework without weakening the Worker runtime boundary.
- The generated-app path introduces explicit Azure resource, quota, sharing, and cleanup responsibilities.
- MCP Apps research is preserved without creating a second Hermes identity or a milestone that cannot be validated through the current client.
- The same semantic interaction may eventually gain more host adapters, but Teams cards and generated web apps remain distinct delivery products.
- Live A17 validation proved that the native Sandbox URL can require Entra authentication and use `activationMode: OnDemand`; the bridge does not need to host a wake endpoint or proxy application traffic.
- Generated-app inventory and lifecycle actions are scoped to the authenticated requesting user. Reviewed Adaptive Card actions call the bridge directly to update ACA lifecycle policy or delete the Sandbox; a short-lived bridge replay cache handles normal Activity retries while the native operations remain idempotent across bridge restarts. They do not require Worker wake, model interpretation, or Service Bus.
- The current Agent 365 host acknowledges generated-app actions but does not render the returned replacement card or an action-context reply. Cards therefore identify themselves as snapshots; users request the live inventory again after an action. The bridge does not add a relay, scheduler, or custom delivery workaround for this noncritical host limitation.

## References

- [Adaptive Cards in Teams](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-reference)
- [Universal Actions for Adaptive Cards](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/overview)
- [MCP Apps specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx)
- [MCP apps in Microsoft 365 Copilot](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/plugin-mcp-apps)
- [Cowork MCP Apps host contract](https://learn.microsoft.com/en-us/microsoft-365/copilot/cowork/mcp-apps-support)
- [Microsoft MCP interactive UI samples](https://github.com/microsoft/mcp-interactiveUI-samples)
- [Azure Container Apps Sandboxes skill](https://github.com/microsoft/azure-container-apps/tree/main/plugin/skills/aca-sandboxes)
- [Azure Container Apps Express overview](https://learn.microsoft.com/en-us/azure/container-apps/express-overview)
- [ADR 0019](0019-durable-document-publish-and-interactive-choice.md)
