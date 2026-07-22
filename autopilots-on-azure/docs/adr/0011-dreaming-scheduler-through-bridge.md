# ADR 0011: Schedule dreaming outside ACA Sandboxes and submit through the bridge

## Status

Accepted.

## Context

The digital-worker loop has three learning stages:

- Hot-path learning during or after a user turn.
- Dreaming, where one worker reflects over recent sessions and local evidence in batches.
- Collective Learning Review, where Candidate Improvements from many Workers are proposed for the next Role Release.

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

The production-friendly path is an Azure Container Apps scheduled Job. The job should call the bridge on a schedule; the bridge then wakes the sandbox and submits the dream run. This preserves scale-to-zero for worker sandboxes and avoids putting scheduling logic inside every sandbox.

The scheduled Job uses the existing per-Worker bridge managed identity. A dedicated Entra resource application exposes `ScheduledLearning.Run.All`; the Job requests a short-lived application token and the bridge verifies signature, issuer, audience, role, client ID, and object ID. The Job receives no stored API key or application secret.

Use event-driven ACA Jobs later when there is a real queue-driven need, such as processing large export batches or fan-out consolidation work. Do not use Service Connector as a scheduler; use it only when helpful for service-to-service wiring.

## Consequences

- ACA Sandbox remains the stateful worker runtime, not the scheduler.
- The bridge stays the control plane for worker wakeup and stateful Hermes invocation.
- Bridge-owned cron is acceptable for v1 but requires the bridge to be alive.
- ACA scheduled Jobs provide the production cloud-native timer path and are modeled with Terraform/azapi in this repository.
- Event-driven Jobs remain available for future queue-based fleet workflows.
- Dreaming runs can be audited and throttled centrally rather than hidden inside individual worker sandboxes.
