# ADR 0015: Use Service Bus to wake per-Worker bridges for Hermes schedules

## Status

Accepted.

## Context

Users need one-shot and recurring Worker tasks such as reminders, status preparation, and periodic checks. Hermes already provides the correct user-facing job model:

- `cronjob` tool and `/cron` management;
- canonical per-profile `cron/jobs.json`;
- fresh-session execution with optional Role Skills;
- delivery targets and originating channel metadata;
- pause, resume, update, remove, and run-now;
- durable `cron/executions.db`;
- file-locked claims and `fire_due(job_id)` for multi-process at-most-once behavior;
- a pluggable `CronScheduler` provider interface.

The built-in Hermes scheduler runs a 60-second ticker inside the Hermes Gateway. Our Worker Sandbox can suspend, so that ticker cannot be the production trigger.

ACA Sandboxes expose lifecycle and connectivity, but no documented native timer, Service Bus trigger, or connector trigger. Standard Azure Container Apps support custom KEDA Service Bus scaling, scheduled Jobs, and event-driven Jobs.

A11 currently uses one scheduled ACA Job to call the bridge for platform-owned Dreaming. A12 must support arbitrary user-created due times without polling or creating one ARM Job resource per schedule.

## Options considered

1. Keep each bridge always running and use an in-process ticker.
2. Poll every 1, 5, or 30 minutes with a scheduled ACA Job.
3. Create one scheduled ACA Job resource per user task.
4. Use Logic Apps recurrence or Service Bus connectors.
5. Use Service Bus scheduled messages with a separate event-driven ACA Job consumer.
6. Use Service Bus scheduled messages to scale the existing per-Worker bridge directly.

## Decision

Use one Service Bus queue per Worker and configure that Worker's bridge Container App with a managed-identity `azure-servicebus` KEDA scaler.

The bridge becomes the queue consumer as well as the HTTP/S messaging adapter. It remains one isolated, single-replica control plane per Worker.

Hermes native cron remains canonical. Implement an `azure` `CronScheduler` plugin through Hermes' supported plugin interface; do not fork Hermes or create a second schedule database.

### Create or update

```text
user request
  -> Hermes cronjob tool
  -> jobs.json mutation on Worker Data Disk
  -> Azure CronScheduler.on_jobs_changed()
  -> reconcile desired next_run_at
  -> schedule one Service Bus message
```

The scheduled message contains only:

- schema version;
- message type;
- Worker ID;
- cron job ID;
- schedule revision;
- due timestamp.

It never contains the private prompt, skills, delivery content, credentials, or human data.

### Fire

```text
scheduled message becomes active
  -> KEDA scales bridge from zero
  -> bridge PeekLocks message and renews lock
  -> bridge validates Worker, type, revision, and due time
  -> bridge wakes or reuses Sandbox
  -> protected runtime endpoint resolves active profile
  -> CronScheduler.fire_due(job_id)
  -> Hermes claim + execution ledger + fresh session + delivery
  -> provider reconciles the next occurrence
  -> bridge completes Service Bus message
  -> bridge scales to zero
```

Service Bus does not provide recurring scheduled messages. The provider schedules one future occurrence. Hermes computes the next occurrence; the provider re-arms it after the current fire path records the outcome.

### Correctness

- Use PeekLock, automatic lock renewal, bounded concurrency, and explicit complete/abandon/dead-letter operations.
- Set `maxReplicas = 1` per Worker.
- Use deterministic Service Bus message IDs plus Service Bus duplicate detection where available.
- Treat Service Bus as at-least-once transport. Hermes `fire_claim`, execution ledger, and schedule revision are the correctness boundary.
- A stale message caused by update/cancellation is acknowledged without execution after revision mismatch.
- Store scheduled-message sequence number and revision as provider reconciliation metadata beside Hermes cron state so cancellation can be attempted.
- Do not depend on cancellation being atomic near activation; revision checks remain mandatory.
- Complete the queue message only after execution and next-occurrence reconciliation are durable.
- Monitor and expose dead-letter queue depth; never auto-discard DLQ messages.

### Identity

- Hermes Azure provider schedules messages as the Worker Agent Identity with Azure Service Bus Data Sender.
- The bridge user-assigned identity receives messages with Azure Service Bus Data Receiver and is used by the KEDA scaler.
- No Service Bus connection string, SAS key, bridge API key, or application secret is stored in the Worker schedule.

### Hosted safety boundary

- Initially allow prompt jobs and reviewed Role Skills.
- Disable arbitrary user-created `script` and `no_agent` jobs in hosted mode.
- Block Gateway lifecycle commands and persistent writes outside Worker-owned paths.
- Scheduled work may use autonomous Agent Identity or Agent User access.
- Human-owned resource access requires valid per-run delegated authorization and is out of scope until human OBO is implemented.

### Delivery privacy

Persist the originating delivery boundary with the Hermes job:

- personal chat;
- targeted private message;
- channel/group conversation;
- email;
- Office comment.

A private origin defaults to private delivery. Scheduled output is never promoted to a public destination without explicit user approval.

### Dreaming migration

The same queue carries `system.dream` messages. The bridge dispatches those to the existing scheduled-learning coordinator rather than Hermes cron.

Keep A11's scheduled ACA Job during migration. Remove it only after the Service Bus path proves:

- scheduled wake from zero;
- retry and DLQ behavior;
- packet preparation parity;
- no duplicate Dreaming;
- Terraform convergence.

## Consequences

- Bridge code gains queue receive, lock-renewal, settlement, and DLQ responsibilities.
- The separate A11 scheduled Job can eventually be removed.
- No fixed polling wakes idle Workers.
- Cost follows actual due occurrences; trigger latency is approximately the KEDA polling interval.
- Service Bus and bridge failure modes require explicit observability and operator replay.
- User schedules survive Worker Refresh because Hermes state remains on the Data Disk.
- Queue state is transport metadata, not the source of truth.

## Rejected alternatives

- **Always-on bridge:** simplest but defeats scale-to-zero.
- **Fixed polling:** wastes executions and adds interval-sized latency.
- **Per-task ACA Jobs:** turns schedule data into ARM-resource churn and complicates update/cancel.
- **Logic Apps:** adds another workflow engine and billed polling without replacing Hermes schedule state.
- **Separate event-driven ACA Job:** provides stronger process isolation, but duplicates an execution container and managed endpoint when the existing isolated bridge can consume the queue directly.

## References

- [Hermes cron internals](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/cron-internals.md)
- [Hermes CronScheduler interface](https://github.com/NousResearch/hermes-agent/blob/main/cron/scheduler_provider.py)
- [Hermes Chronos provider](https://github.com/NousResearch/hermes-agent/tree/main/plugins/cron_providers/chronos)
- [Service Bus scheduled messages](https://learn.microsoft.com/azure/service-bus-messaging/message-sequencing#scheduled-messages)
- [Service Bus duplicate detection](https://learn.microsoft.com/azure/service-bus-messaging/duplicate-detection)
- [Service Bus dead-letter queues](https://learn.microsoft.com/azure/service-bus-messaging/service-bus-dead-letter-queues)
- [Azure Container Apps scaling](https://learn.microsoft.com/azure/container-apps/scale-app)
