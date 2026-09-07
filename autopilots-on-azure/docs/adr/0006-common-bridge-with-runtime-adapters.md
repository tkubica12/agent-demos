# ADR 0006: Keep a common bridge with runtime adapters

## Status

Accepted. Updated 2026-09-06 for the service-role Sandbox architecture; application parity is still being validated.

## Context

The bridge already owns the project-specific Microsoft 365 behavior:

- Teams `/api/messages` ingress.
- Agent 365 Activity Protocol handling through the Microsoft 365 Agents SDK.
- Teams signal classification for mentions, 1:1 messages, targeted private messages, thread replies, reactions, and unmentioned RSC-observed messages.
- Bounded Teams context construction.
- Temporary status reactions and semantic `TEAMS_REACTION:` control lines.
- Agent 365-compatible endpoint shape.
- `/invoke` smoke-test endpoint.

Hermes has its own native Teams adapter, but using it first would create a second Microsoft 365 boundary with different routing, context, and Agent 365 behavior. That would bypass the existing bridge investment and make OpenClaw and Hermes demonstrations behave differently.

## Decision

Keep one common bridge implementation as the Microsoft 365 boundary.

Move runtime-specific protocol details behind bridge-side runtime adapters:

- `OpenClawRuntimeAdapter` wakes ACA Sandbox and calls the OpenClaw Gateway websocket protocol.
- `HermesRuntimeAdapter` wakes ACA Sandbox and calls the Hermes API server over HTTP.

The bridge owns transport behavior, Teams UX, prompt envelope construction, response shaping, and Agent 365 ingress. The selected runtime owns semantic reasoning, whether to answer, response text, and the semantic reaction decision. The bridge both adds and removes temporary eyes, then executes an agent-selected semantic reaction through the supported Agent User path; live permission and delivery evidence must be checked separately.

The bridge itself runs in the Worker's dedicated `gateway` Sandbox Group with a distinct user-assigned managed identity. Auto-suspend is disabled to preserve detached post-ACK processing and continuous Service Bus receive. Runtime services retain independent OnDemand lifecycles. Office behavior policy belongs in runtime skills, not an expanding bridge prompt.

Hermes native Teams support is deferred as an optional future mode for pure-Hermes deployments. It is not the initial integration path.

## Consequences

- OpenClaw and Hermes share the same Teams and Agent 365 behavior.
- The bridge can be tested once for Teams routing and response behavior across runtimes.
- Runtime adapters stay small and focused on protocol translation.
- Hermes-specific features that require owning the Teams adapter, such as Hermes-native Adaptive Card approvals, are deferred.
- The bridge must avoid importing OpenClaw protocol code directly from Teams handlers.
- Runtime adapter request/response models become the contract for adding future runtimes.
