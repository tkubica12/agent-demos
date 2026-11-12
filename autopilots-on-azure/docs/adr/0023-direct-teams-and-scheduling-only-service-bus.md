# ADR 0023: Direct Teams execution and scheduling-only Service Bus

## Status

Accepted

## Context

The gateway previously persisted Teams activities and operator model requests in an activity-recovery queue before execution. Operators submitted `/invoke` requests and polled for completion; manual Dreaming and scheduled-learning operations also had bridge HTTP triggers. This duplicated the Agent 365 delivery path, kept private message payloads and results in a second durable journal, and added queue/connector behavior outside the product's interactive Teams flow.

The existing per-Worker Service Bus schedule queue has a separate purpose: native Hermes cron, scheduled Dreaming, and deterministic document retries must wake a gateway that can suspend to Memory. These scheduled jobs need PeekLock renewal, durable receipts, bounded active-work keepalive, and explicit settlement.

The Worker owner also chose to retain Learning Packets as a separate governed process. Dreaming must not prepare, approve, or export packets automatically.

## Decision

- Handle Agent 365/Teams activities directly in the gateway and invoke native Hermes Runs over authenticated OnDemand HTTPS.
- Remove the activity-recovery queue, its native trigger and roles, the public `/invoke` and invocation-polling routes, and bridge HTTP triggers for manual Dreaming and scheduled-learning execution/status.
- Keep one per-Worker Service Bus queue only for native scheduling, Dreaming, and deterministic document retries. Retain its lease renewal, journal, bounded keepalive, completion, and unknown-outcome dead-letter behavior.
- Run manual Dreaming as a distinct occurrence enqueued through the Azure Container Apps Sandbox management plane. This does not change the production cron definition.
- Keep Learning Packet preparation, review, approval, rejection, and export explicit. A scheduled or manual Dream does not invoke that workflow.
- Remove legacy activity-recovery rows and payload bindings from the gateway journal during transport initialization; preserve scheduling receipts and Teams conversation state.

## Alternatives

| Alternative | Reason not selected |
| --- | --- |
| Keep the activity-recovery queue as a backstop | Duplicates Agent 365 activity delivery, persists private activity content, and requires a second queue/trigger/settlement state machine. |
| Keep `/invoke` with durable admission and polling | Creates a parallel product interaction surface and retains the operator-job state machine after the Teams route is direct. |
| Put interactive requests on the schedule queue | Couples user response latency to connector polling and mixes prompts/results with the queue's bounded scheduling contract. |
| Remove Service Bus entirely | Breaks wake-up for cron, Dreaming, and deterministic document retries while the gateway is suspended. |
| Automatically prepare Learning Packets after Dreaming | Conflicts with the owner's explicit decision to make packet preparation and export opt-in operations. |

## Consequences

Teams turns have no Service Bus admission, receipt, or replay guarantee; Agent 365 and the direct gateway/native-run path own their delivery semantics. The durable scheduling journal continues to protect queued work and preserve native Run identity across exact redelivery. A manual Dream requires local Azure management-plane authorization rather than a public bridge endpoint.

The schedule queue is not a request/reply transport. Its source polling can delay background work, but does not affect the interactive Teams response path. CPU/RAM can suspend to zero between turns, while Service Bus, journal/backup storage, networking, registry, and monitoring retain their normal charges.
