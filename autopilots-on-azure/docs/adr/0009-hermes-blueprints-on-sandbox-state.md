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

The hosted implementation uses a full commit SHA, not a floating branch. The bridge passes the source repository, repository-relative distribution path, expected version, and commit to the sandbox. The persistent Hermes root remains `/data/hermes`; the distribution is installed into `/data/hermes/profiles/<blueprint-name>`, recorded as the sticky active profile, and passed to the gateway subprocess as its effective `HERMES_HOME`. The instance manifest is stored at `local\autopilots-instance.json`, which is instance-owned and preserved during updates.

Changing the pinned commit changes the ACA Sandbox labels. The bridge deletes the stale sandbox container and creates a new one against the same Data Disk volume. Startup then replaces only the paths listed by `distribution_owned`; memories, sessions, SQLite state, workspace, `.env`, `local\`, and any skills outside the distribution-owned namespace remain intact.

The runtime owns a small effective-config merge at startup. Blueprint defaults are loaded first, then Azure-required model, API server, path, and loopback MCP settings are applied. Existing `.env` content is preserved except for the explicit runtime-managed keys that must follow the current sandbox configuration.

OpenClaw is an explicit A8 exception. It keeps the lighter runtime-image plus persistent `/data/home` and `/data/workspace` model because it does not currently expose a Hermes-equivalent, reviewed Git profile-distribution lifecycle with clear distribution-owned update semantics. Do not create a second custom package manager for OpenClaw in this milestone.

Do not introduce a project database for Hermes core memory or blueprint skill storage in the first implementation. Databases remain appropriate for external business data, policy corpora, data-agent backends, fleet dashboards, audit search, or an admin UI, but those are not Hermes core storage.

## Consequences

- Blueprint upgrade can preserve personal/team memory and session history.
- Hermes remains close to its native operating model instead of requiring a custom state backend.
- Git diffs and pull requests become the natural review surface for releasable role skills.
- The sandbox Data Disk must be treated as single-writer per worker instance because Hermes state includes SQLite.
- The startup script must avoid overwriting distribution-owned or instance-owned files unexpectedly.
- Hosted upgrades require a reachable Git source for the new pinned commit; restarts at an already installed commit do not fetch Git again.
- Operators must not use ACA Dynamic Sessions for this track.
- A database can be added later for fleet operations without changing the blueprint source of truth.

## Validation

The lifecycle was live-validated on ACA Sandboxes on 2026-07-13:

1. Installed `junior-project-manager` v1.0.0 from commit `ecc07fad92122d6ae6d4e44bd145c1814a746071`.
2. Created native Hermes session state plus private files under `memories\`, `sessions\`, `skills\instance-local\`, and `local\`.
3. Changed the pinned source to v2.0.0 at commit `50342bd359a3f0fce9669a43b1d6eeb4fa690900`.
4. Confirmed the bridge deleted the v1 sandbox and created a v2-labeled sandbox against the same `hermes-data` Data Disk.
5. Confirmed v2 distribution files were active and all private markers plus `state.db` survived.
6. Confirmed the upgraded worker still invoked the private incidents MCP through Agent Identity.
