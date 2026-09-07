# ADR 0008: Integrate Hermes through its API server in ACA Sandbox

## Status

Accepted.

Reviewed 2026-09-06 22:33 CEST. Deployment, native models, operator negative GETs/nonowner preflight, same-ID/Data-Disk resume, MCP, schedule, Dream, and bounded evaluation proofs stand. All 49 reviewed native parents resolve; 105 audited spans contain allowed metadata only. Two controlled requests isolate parent rewriting to Sandbox-origin egress while TraceId survives. Platform intermediate parents are absent from AppInsights; exact proxy implementation is unidentified. A fully connected waterfall, automatic eight-hour idle behavior, and targeted inbound remain unverified.

## Context

Hermes Agent is a Python agent runtime and gateway. It can expose an OpenAI-compatible API server from the gateway process when `API_SERVER_ENABLED=true`.

For Azure sandbox hosting, the relevant Hermes properties are:

- API server default port is `8642`.
- Container deployments must bind the API server to `0.0.0.0`.
- API calls require `API_SERVER_KEY`.
- `HERMES_HOME` controls durable state and should point at the sandbox data mount.
- Hermes stores sessions, memory, skills, and MCP tokens on disk, including SQLite state.
- A single `HERMES_HOME` should be treated as single-writer.
- Hermes can configure private MCP servers through `mcp_servers`.

Hermes also has native Teams support, but this project already has a common bridge that owns Teams and Agent 365 behavior.

## Decision

Run Hermes as a runtime inside ACA Sandbox and expose only its internal API server to the common bridge.

Initial Hermes runtime configuration:

- Start `hermes gateway` with the API server enabled.
- Set `API_SERVER_HOST=0.0.0.0`.
- Set `API_SERVER_PORT=8642`.
- Set `API_SERVER_KEY` from runtime secrets.
- Set `HERMES_HOME` to the mounted runtime data path.
- Configure the private incidents MCP service through Hermes `mcp_servers`.
- Keep one active Hermes runtime instance per persisted `HERMES_HOME`.
- Treat `Stopped` as resumable, not as failure requiring deletion. The legacy stopped-runtime recycling behavior is removed; only `Failed` is recycled. Both direct SDK and deployed application same-ID/Data-Disk resume are now live-proven, with different measurement scopes.
- Use native Hermes `azure-foundry` with an Entra token callback for model inference. Remove the custom model token proxy, but retain the separate loopback Agent Identity/Agent User MCP adapter.
- Keep Foundry external-agent registration and tracing separate from hosting; no Foundry Hosted Agent compute is introduced.

The bridge should call a sessionful Hermes endpoint when possible and map Microsoft 365 context to Hermes session metadata:

- `X-Hermes-Session-Id` from conversation/thread context.
- `X-Hermes-Session-Key` from autopilot instance, source, and user identity.

Use the simplest stable endpoint for the first proof. Prefer `/api/sessions/{id}/chat`; fall back to `/v1/responses` or `/v1/chat/completions` only if needed during implementation.

The September tracing implementation makes those endpoints observably different. Supported native `llm_execution`/`tool_execution` middleware wraps actual callbacks, including streaming model execution. Because gateway executor threads lose context and have no `traceparent` hook, the wrapper lends metadata-only trace context through a local lease keyed by the hashed native session. Explicit session chat and chat-completions with `X-Hermes-Session-Id` can correlate. `/v1/responses` creates its own session, so its missing mapping is marked `autopilots.trace.correlation=missing`; it is not a trace-equivalent fallback.

Cancellation, transport interruption, or an uncertain `5xx` retains the lease while the native executor may still run; conflicting reuse returns `409`. Restart the Hermes runtime wrapper and native gateway before reusing that session, not merely the public bridge. The earlier 87 local tests and idempotent external registration remain distinct from live ingestion. Across smoke, MCP, user cron, and ad-hoc Dream, all 49 native parents resolve; Dream's 38 native spans have zero missing correlation. Local urllib preserved its supplied parent; fresh uninstrumented urllib inside the gateway Sandbox changed it, preserving TraceId. This isolates Sandbox-origin egress, not ingress alone or application-exporter loss. The exact proxy is unidentified; platform intermediate parents remain absent from AppInsights. No custom trace headers, fake parent spans, or egress bypass were added. The 105-span privacy audit is bounded evidence, not an all-time guarantee. No Hosted Agent compute is introduced.

## Consequences

- Hermes can reuse the current Azure Sandbox lifecycle and common bridge.
- Hermes persistent memory and skills survive runtime restarts through the sandbox data mount.
- Hermes does not need public ingress or Teams credentials for the initial integration.
- Hermes native Teams features, dashboard exposure, and Hermes-managed approvals are deferred.
- Scaling a single Hermes deployment horizontally is not supported until state isolation is redesigned.
- Runtime health checks should verify Hermes `/health` before the bridge sends user turns.
