# ADR 0008: Integrate Hermes through its API server in ACA Sandbox

## Status

Accepted.

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

The bridge should call a sessionful Hermes endpoint when possible and map Microsoft 365 context to Hermes session metadata:

- `X-Hermes-Session-Id` from conversation/thread context.
- `X-Hermes-Session-Key` from autopilot instance, source, and user identity.

Use the simplest stable endpoint for the first proof. Prefer `/api/sessions/{id}/chat`; fall back to `/v1/responses` or `/v1/chat/completions` only if needed during implementation.

## Consequences

- Hermes can reuse the current Azure Sandbox lifecycle and common bridge.
- Hermes persistent memory and skills survive runtime restarts through the sandbox data mount.
- Hermes does not need public ingress or Teams credentials for the initial integration.
- Hermes native Teams features, dashboard exposure, and Hermes-managed approvals are deferred.
- Scaling a single Hermes deployment horizontally is not supported until state isolation is redesigned.
- Runtime health checks should verify Hermes `/health` before the bridge sends user turns.
