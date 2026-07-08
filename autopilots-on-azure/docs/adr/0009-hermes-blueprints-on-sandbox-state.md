# ADR 0009: Use Hermes profile distributions with ACA Sandbox state

## Status

Accepted.

## Context

The future digital-worker scenario needs two different lifecycles:

- A reviewed role blueprint, such as a junior project manager, must be releasable and updatable across many worker instances.
- Each assigned worker instance must keep personal/team memory, session history, preferences, and local context across blueprint upgrades.

Hermes already has native persistence and packaging concepts:

- `HERMES_HOME` contains profile-local config, `SOUL.md`, memories, sessions, skills, cron jobs, and SQLite state.
- Hermes profile distributions package a whole agent as a Git repository, including `SOUL.md`, `skills\`, `config.yaml`, `mcp.json`, and optional cron definitions.
- Hermes distribution update preserves user-owned data such as memories, sessions, `.env`, auth files, `state.db`, logs, workspace, and `local\`.
- Hermes can install skills and profile distributions from Git.

ACA Sandboxes are the relevant Azure runtime because they provide explicit lifecycle control, suspend/resume, snapshots, and volumes. ACA Dynamic Sessions are a different ephemeral session-pool concept and are not suitable for long-lived digital workers with durable state.

Options considered:

1. Store all worker state in a central database.
2. Store all worker state in Blob Storage and materialize files at startup.
3. Bake each blueprint version into the runtime image.
4. Use Hermes profile distributions from Git for blueprint-owned files and ACA Sandbox Data Disk for instance-owned state.

## Decision

Use Hermes profile distributions as the canonical blueprint packaging mechanism and use an ACA Sandbox Data Disk as the worker instance's persistent `HERMES_HOME`.

The blueprint repository owns releasable files:

- `distribution.yaml`
- `SOUL.md`
- `skills\`
- `mcp.json`
- selected `config.yaml` defaults
- optional cron templates

The worker instance owns private/durable runtime state:

- `.env`
- `auth.json`
- `memories\`
- `state.db*`
- sessions
- logs
- workspace
- `local\`
- assignment-specific private artifacts

The active worker copy lives on the sandbox Data Disk, but Git remains the source of truth for the releasable blueprint. The instance records the blueprint source, version, and commit in an instance manifest so central tools can compare local candidate changes against the correct base revision.

Do not introduce a project database for Hermes core memory or blueprint skill storage in the first implementation. Databases remain appropriate for external business data, policy corpora, data-agent backends, fleet dashboards, audit search, or an admin UI, but those are not Hermes core storage.

## Consequences

- Blueprint upgrade can preserve personal/team memory and session history.
- Hermes remains close to its native operating model instead of requiring a custom state backend.
- Git diffs and pull requests become the natural review surface for releasable role skills.
- The sandbox Data Disk must be treated as single-writer per worker instance because Hermes state includes SQLite.
- The startup script must avoid overwriting distribution-owned or instance-owned files unexpectedly.
- Operators must not use ACA Dynamic Sessions for this track.
- A database can be added later for fleet operations without changing the blueprint source of truth.
