`# ADR 0015: Native Hermes cron with Service Bus delivery

## Decision

Keep native Hermes cron and its execution ledger on the Worker Data Disk. An Azure `CronScheduler` plugin arms only the next occurrence in a per-Worker Service Bus queue. The non-suspending gateway receives it and wakes/reuses runtime compute.

```text
native cron mutation → next-occurrence message → gateway PeekLock
  → runtime claim/execution receipt → bound Teams delivery/receipt
  → next-occurrence reconciliation → queue completion
```

Messages contain only type, Worker/job ID, revision, and due time. Private prompts, skills, results, and delivery credentials stay on disk. Native cron sends as Agent Identity federated from runtime identity. The gateway receives and sends document retries as its own managed identity, without Agent Identity exchange. No SAS connection string is used.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Periodic polling | Adds idle work and delays arbitrary due times. |
| One cloud scheduled job per user task | Duplicates native schedule state and creates resource churn. |
| Separate consumer/relay | Adds another service while the Agent 365 gateway already must remain awake for post-ACK work. |
| Generic LLM prompt for document retries | Pays for reasoning where a fixed storage state machine is sufficient. |

## Correctness

- PeekLock, automatic renewal, bounded concurrency, explicit settlement, and visible DLQ.
- Revision checks reject stale update/cancel messages even when cancellation races activation.
- Durable claims/output receipts prevent duplicate model execution; delivery retries reuse output.
- Teams sends are not atomic with receipt persistence. A crash in that window can duplicate the message.
- Hosted jobs allow prompts/reviewed Role Skills, not arbitrary scripts or stored human delegation.
- Reserved `system.dream` uses fenced phases. Reconcile a durable completed response; stop on ambiguous `dream_started` rather than blindly rerun.
- Operator run-now is a separate occurrence and cannot advance production cron.
- Dreaming may prepare a packet, never approve/export/promote it. `packet=null` is valid when nothing transferable changed.

Document publishing reuses this queue with fixed runtime APIs; [ADR 0019](0019-durable-document-publish-and-interactive-choice.md) defines its separate state machine.
