# ADR 0011: Schedule dreaming outside ACA Sandboxes and submit through the bridge

## Status

Superseded for trigger selection by [ADR 0015](0015-service-bus-backed-hermes-cron.md). The bridge/Sandbox Dreaming boundary remains valid.

## Context

The digital-worker loop has three learning stages:

- Hot-path learning during or after a user turn.
- Dreaming, where one worker reflects over recent sessions and local evidence in batches.
- Collective Learning Review, where Candidate Improvements from many Workers are proposed for the next Role Release.

This decision covers platform-owned scheduled learning, not user-created reminders or recurring business tasks. Hermes native cron already models those jobs, including create/update/pause/resume/remove/run, delivery targets, attached skills, fresh-session execution, and durable execution history. User-scheduled work requires a separate delivery, authorization, privacy, and missed-run contract.

For hosted Azure workers, dreaming must run even when the ACA Sandbox has suspended. We investigated whether ACA Sandboxes have their own native schedule/trigger mechanism. Current Microsoft documentation describes ACA Sandboxes as stateful compute with explicit lifecycle control, suspend/resume, snapshots, volumes, ports, and data-plane management. It does not describe a Sandbox-native timer trigger.

Related Azure concepts are different:

- Azure Container Apps Jobs support manual, scheduled, and event-driven triggers.
- Event-driven jobs can wake from queues such as Azure Storage Queue or Service Bus through KEDA-style scaling rules.
- Service Connector wires compute resources to backing services and configures connection information; it is not a scheduler.
- ACA Dynamic Sessions are out of scope because they are ephemeral session-pool compute, not stateful Sandbox workers.

Hermes also has a cron subsystem, but Hermes cron depends on a running Hermes gateway ticker or an external managed-cron provider. Hermes cron jobs run in fresh sessions and may not match the stateful worker-conversation semantics we need for dreaming.

Options considered:

1. Use a hypothetical Sandbox-native timer trigger.
2. Use Hermes gateway cron as the primary hosted scheduler.
3. Put a cron loop in the proprietary bridge.
4. Use an Azure Container Apps scheduled Job to call the bridge.
5. Use event-driven ACA Jobs from Service Bus or Storage Queue.

## Decision

Schedule dreaming outside the ACA Sandbox and submit dream runs through the proprietary bridge.

The bridge is responsible for:

- Selecting the Worker.
- Waking or reusing the ACA Sandbox.
- Waiting for Hermes health.
- Calling a stateful Hermes endpoint with stable session identity.
- Recording dream-run status and errors.

The initial implementation uses a bridge-owned timer because it is simplest for the demo. Enabling it keeps one bridge replica active. The timer runs through the same Worker learning transaction as foreground work, prepares a Learning Packet only when transferable records exist, and never approves or exports the packet.

The first production path was an Azure Container Apps scheduled Job. It called the bridge on a schedule; the bridge then woke the Sandbox and submitted the Dream run.

The scheduled Job used the existing per-Worker bridge managed identity and a dedicated Entra resource application exposing `ScheduledLearning.Run.All`. This surface was removed after queue-driven Dreaming reached parity.

ADR 0015 selects a unified Service Bus trigger. In the approved September 2026 topology, Service Bus does not directly wake the gateway Sandbox: its auto-suspend is disabled and its receiver stays running.

```text
Service Bus message
  -> non-suspending gateway receives due message
  -> bridge consumes and validates message
  -> bridge wakes or reuses ACA Sandbox
  -> Hermes Dreaming
```

The earlier queue-driven implementation proved the workflow and retired the A11 Job. On September 6, 2026, the all-Sandbox topology delivered a real user schedule and completed an ad-hoc Dream on Hermes 2, preserving the production occurrence with an empty DLQ. Dream had no transferable records and produced no packet; this is execution evidence, not learning improvement. Queue-driven wake from suspension and live crash recovery remain separate checks. Phase checkpoints/fencing allow completed-response replay, while an ambiguous `dream_started` stops rather than blindly rerunning. This is not a full-system scale-to-zero or exactly-once-delivery claim.

## Consequences

- ACA Sandbox remains the stateful worker runtime, not the scheduler.
- The bridge stays the control plane for worker wakeup and stateful Hermes invocation.
- Bridge-owned cron is acceptable for v1 but requires the bridge to be alive.
- The former ACA scheduled Job is retained only as historical context for A11.
- The bridge gains bounded queue-consumer responsibilities under ADR 0015.
- Dreaming runs can be audited and throttled centrally rather than hidden inside individual worker sandboxes.
- User-created schedules remain out of scope for this ADR; ADR 0015 defines their canonical Hermes cron and Service Bus integration.

## References

- [Jobs in Azure Container Apps](https://learn.microsoft.com/azure/container-apps/jobs)
- [Azure Container Apps Sandboxes overview](https://learn.microsoft.com/azure/container-apps/sandbox-overview)
