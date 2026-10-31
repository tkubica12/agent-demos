# ADR 0009: Git Role Releases and private Worker state

## Decision

Use a commit-pinned Hermes distribution for shared role files and a per-Worker Hermes home for private state.

`distribution.yaml` declares the release and exact owned paths. `local\worker.json` records the source, immutable commit, release, owned paths, and skill baseline hashes. Startup applies Azure-required model, API, and loopback MCP settings after role defaults.

Worker Refresh replaces distribution-owned files, archives release-scoped candidates and learning decisions, and preserves Personal Memory, Private Playbooks, Work History, cron state, and workspace. It is transactional and forward-only; governed changes require state-bound approval or signed rejection before replacement.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Central database for native memory/skills | Duplicates Hermes persistence and adds a custom synchronization contract. |
| Live private state on object storage | SQLite snapshots and locking fail on the blobfuse mount; object storage holds only complete native backups ([ADR 0022](0022-memory-suspend-with-native-backups.md)). |
| Bake every Role Release into the runtime image | Couples role review to image builds and compute dependencies. |

Git remains the shared source of truth; the Worker home is the effective private state. Runtime restart at an installed commit does not need to fetch it again. A newer release requires reachable source and valid refresh preflight.

See [SPEC.md](../../SPEC.md) for owned paths and [ADR 0010](0010-candidate-improvements-and-collective-learning-review.md) for export gates.
