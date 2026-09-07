# ADR 0015: Dispatch Hermes schedules through continuously running gateways

## Status

Accepted.

Updated 2026-09-06 21:46 CEST: Hermes 2 user scheduling produced a delivered receipt, verified SHA, and DLQ `0`, with gateway Running/auto-suspend `false` and runtime already Running—not wake proof. Its ad-hoc Dream completed at phase `prepared`, `success=true`, `recordCount=0`, `packet=null`; production cron unchanged, scheduled count `1`, DLQ `0`. This is no-change execution, not learning improvement or crash-recovery proof. Separately, deployed application resume preserved runtime ID `65db4109-ee34-4b52-a296-31d5499a8f3e` and Data Disk in 28.5 s including model work. Direct SDK resume took 1.3 s with a different scope. Automatic eight-hour idle behavior remains unverified; no KEDA/full-system scale-to-zero or exactly-once claim follows.

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

Use one Service Bus queue per Worker and a managed-identity receiver in that Worker's non-suspending gateway Sandbox.

The gateway is the queue consumer and HTTP/S messaging adapter. It remains one isolated control-plane process per Worker. Only the Worker runtime is OnDemand.

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
  -> running gateway receives the due message
  -> bridge PeekLocks message and renews lock
  -> bridge validates Worker, type, revision, and due time
  -> bridge wakes or reuses Sandbox
  -> protected runtime endpoint resolves active profile
  -> CronScheduler.fire_due(job_id)
  -> Hermes claim + execution ledger + fresh session + durable output receipt
  -> bridge proactively continues the bound Teams conversation
  -> runtime marks the delivery receipt
  -> provider reconciles the next occurrence
  -> bridge completes Service Bus message
  -> gateway continues receiving; runtime may suspend independently
```

Service Bus does not provide recurring scheduled messages. The provider schedules one future occurrence. Hermes computes the next occurrence; the provider re-arms it after the current fire path records the outcome.

### Correctness

- Use PeekLock, automatic lock renewal, bounded concurrency, and explicit complete/abandon/dead-letter operations.
- Keep one active gateway consumer and one writer per Worker profile.
- Use deterministic Service Bus message IDs plus Service Bus duplicate detection where available.
- Treat Service Bus as at-least-once transport. Hermes `fire_claim`, execution ledger, and schedule revision are the correctness boundary.
- Hermes execution is at-most-once for one schedule revision. Teams proactive delivery is at-least-once: a process failure after Teams accepts the activity but before the durable receipt is marked can repeat the visible message because Teams and Service Bus do not share a transaction.
- A stale message caused by update/cancellation is acknowledged without execution after revision mismatch.
- Store scheduled-message sequence number and revision as provider reconciliation metadata beside Hermes cron state so cancellation can be attempted.
- Persist an execution/delivery receipt before calling `fire_due`; after a process crash, recover a newly written Hermes output without rerunning or emit an explicit interrupted-run result when Hermes had already claimed the occurrence.
- Do not depend on cancellation being atomic near activation; revision checks remain mandatory.
- Complete the queue message only after execution and next-occurrence reconciliation are durable.
- For bound Teams schedules, complete the queue message only after proactive send and durable delivery acknowledgement. A delivery failure abandons the message without rerunning the Hermes job.
- Monitor and expose dead-letter queue depth; never auto-discard DLQ messages.
- Persist phase checkpoints and enforce fencing. A completed Dream response can be reconciled/replayed without repeating the model/tools.
- Stop on an ambiguous `dream_started` interruption instead of assuming it is safe to rerun.
- Give operator run-now a distinct occurrence identity; it cannot consume or advance the production occurrence.

### Identity

- Hermes Azure provider schedules messages as the Worker Agent Identity with Azure Service Bus Data Sender.
- The gateway's distinct user-assigned identity receives messages with Azure Service Bus Data Receiver; there is no Sandbox KEDA scaler.
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

### A12.1 Dreaming migration

The same queue carries `system.dream` messages. The bridge dispatches those to the existing scheduled-learning coordinator rather than an agent prompt.

The A11 scheduled ACA Job was removed after the Service Bus path proved:

- scheduled wake from zero;
- retry and DLQ behavior;
- packet preparation parity;
- no duplicate Dreaming;
- Terraform convergence.

## Consequences

- Bridge code gains queue receive, lock-renewal, settlement, and DLQ responsibilities.
- The separate A11 scheduled Job and dedicated auth/client surface are removed.
- No fixed polling wakes idle Workers.
- Runtime compute follows due work, but the gateway incurs an explicit always-running cost. Queue receive, not a KEDA polling interval, determines dispatch latency.
- Service Bus and bridge failure modes require explicit observability and operator replay.
- User schedules survive Worker Refresh because Hermes state remains on the Data Disk.
- Queue state is transport metadata, not the source of truth.

## Rejected alternatives

- **Always-on Worker compute:** rejected because it defeats the expensive runtime suspend boundary. The lightweight gateway stays running for both Agent 365 detached work and queue receive under ADR 0001.
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
