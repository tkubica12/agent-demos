# ADR 0008: Hermes API server in an OnDemand Sandbox

## Decision

Run Hermes 0.19.0 with its native API server behind a runtime wrapper. The per-Worker Agent 365 gateway owns messaging and compute lifecycle; Hermes owns agent execution.

- The wrapper exposes port `8642`; the native gateway API behind it uses `9119` by default. Requests authenticate with `API_SERVER_KEY`; the Data Disk profile is `HERMES_HOME`.
- One writer owns each profile. Unchanged stopped compute resumes; failed or stale deployments are replaced while retaining the Data Disk.
- Session chat is the primary integration; [ADR 0017](0017-messaging-session-lifecycle.md) defines transcript rotation.
- Native `azure-foundry` uses Entra model authentication. The separate loopback MCP federation adapter remains necessary.
- Foundry external-agent registration supplies discovery/tracing metadata, not Hosted Agent compute.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Hermes-native Teams ingress | Duplicates the Agent 365 identity and notification boundary. |
| One CLI process per message | Discards native API session management and complicates lifecycle/learning coordination. |
| Shared concurrent profile writers | Conflicts with SQLite and transactional skill state. |

The wrapper instruments actual native model/tool callbacks and supplies explicit-session trace context across executor threads. It is not another agent runtime. [SPEC.md](../../SPEC.md) defines trace-lease recovery and current correlation limits.
