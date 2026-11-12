# ADR 0022: Memory suspend with native backups on a Sandbox Blob volume

## Decision

Suspend the runtime Sandbox to **Memory** and keep the Hermes home on its root disk. Make the home durable with native `hermes backup` archives on a platform-managed `AzureBlob` Sandbox volume, and restore the newest archive with native `hermes import` when a runtime starts with an empty home.

- The wrapper backs up at most once a minute and only after the home changed, ignoring files idle Hermes rewrites by itself. Archives are staged locally, then copied and renamed onto the volume; the newest 24 are kept.
- Deployment runs a final backup inside a runtime before deleting it for an image, setting or Role Release change. A `Failed` runtime cannot run it; its replacement restores the newest automatic backup.
- Startup never overwrites an existing home. A damaged archive, another Worker's archive or an unreadable volume stops startup instead of starting a fresh Worker.

The gateway recovery journal follows the same filesystem boundary. Live SQLite and its cross-thread/process checkpoint lock stay on local disk. A checkpoint takes and integrity-checks a local snapshot, then copies and renames a uniquely staged file onto the Blob volume. Publication is serialized so an older concurrent snapshot cannot replace a newer checkpoint. Restore copies it locally before opening SQLite; the Blob mount is never a live database. Native API session/run state stays with the runtime home, not another custom actor journal.

## Evidence

Measured on 2026-10-31 in Sweden Central with 2 vCPU and 2 GiB:

| Measurement | Result |
| --- | --- |
| Platform resume, either mode | Under 1 s to VM boot |
| Memory resume to native Hermes API | 1.5–2.7 s, lab and deployed `hermes2` |
| Disk resume to native Hermes API | 17–20 s root disk only; 21–25 s with a Data Disk |
| Native backup of the `hermes2` home | 8.5–9.6 s, 24 MB archive |
| Restore into a new Sandbox | About 3 s; `/health` 10–12 s after creation |
| Blob volume after Memory suspend and resume | Mounted and writable |

Disk mode is slow because every wake restarts Hermes: launcher, official bootstrap, imports and plugins, and eager MCP discovery of eleven servers before the API opens.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Disk suspend with a Data Disk volume | Data Disk volumes allow only Disk mode, so every wake costs a full Hermes start (21–25 s). |
| Keep Disk mode and suspend later | Hides the cold start only while traffic is frequent and pays idle compute for it. |
| Memory suspend without durable copies | The platform documents local disk and memory as unsuitable as the only durable store; a `Failed` or deleted Sandbox would lose the Worker. |
| Sandbox snapshots | Persist independently, but restore the old image, so they cannot carry state into a new runtime image. |
| Live Hermes home on the Blob volume | SQLite snapshots and locking fail on blobfuse; native backup of all databases failed there. |
| Customer-owned Storage account | Subscription policy forces public network access off, adding Private Endpoint, private DNS and RBAC, while the platform volume needs none and lives with the Worker's Sandbox Group like the Data Disk did. |

The platform volume shares the Sandbox Group's lifecycle and offers no separate retention or replication settings. Deleting the group deletes the backups, as it would have deleted the Data Disk.
