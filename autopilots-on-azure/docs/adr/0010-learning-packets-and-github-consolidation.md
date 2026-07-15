# ADR 0010: Promote transferable learning through packets and GitHub PRs

## Status

Accepted.

## Context

Hermes can self-improve locally by writing memory and creating or patching skills. In a fleet of digital workers, that creates a useful but risky split:

- Some learnings are private to the assigned person/team and must never be shared.
- Some learnings are procedural or domain patterns that could improve the shared role blueprint.
- Some apparent learnings are mistakes, one-off local context, or statistically weak outliers.

We need the shared blueprint to improve over time without blindly merging every worker's private memory or every local skill edit.

Options considered:

1. Directly sync local worker skills back into the shared blueprint.
2. Have a central process diff local worker files against the base blueprint and infer everything from diffs.
3. Have each worker create only narrative GitHub issues describing desired improvements.
4. Have each worker keep local file changes plus a rationale journal; central tooling computes exact diffs and creates learning packets for LLM and human review.
5. Build a custom database/admin app as the primary consolidation surface.

Direct sync is unsafe because it can leak private context and overfit the blueprint to one assignment. Central diff-only keeps exact changes but loses the instance's "why". Issue-only proposals preserve rationale but discard exact file context. A custom admin app may be useful later, but it is unnecessary as the first source of truth and would duplicate GitHub review mechanics.

## Decision

Use a hybrid learning-packet flow.

Each worker may freely self-improve locally, but it must classify durable learnings before storing them:

- Private personal/team memory.
- Private local cache.
- Candidate transferable procedural learning.
- Candidate transferable domain knowledge.
- Do not store.

For transferable candidates, the worker records rationale in a local journal such as `learning\records.jsonl`. The worker does not need to compute Git diffs.

Use two distinct local durability lanes:

- Private-permanent adaptation: `USER.md`, `MEMORY.md`, `local\private-cache.md`, and private session state. This survives every blueprint generation and is excluded from consolidation.
- Transferable generation-scoped adaptation: validated `learning\records.jsonl` entries rendered into `skills\hot-learning\SKILL.md`. The current worker uses this immediately, but it may be replaced by reviewed fleet knowledge in a later blueprint generation.

Transferable candidates may be produced either on the hot path after a normal turn or during a dream run. Both paths use the same runtime validator and redaction boundary. Workers never write the shared blueprint directly.

Central consolidation does the following:

1. Reads the worker instance manifest to identify the blueprint source and base commit.
2. Exports only allowed paths and learning journals from the worker sandbox.
3. Excludes `.env`, auth files, `memories\`, raw sessions, `state.db*`, logs, private workspace data, and secrets.
4. Checks out the recorded blueprint commit.
5. Computes exact diffs centrally.
6. Combines diffs, rationale journal entries, evidence summaries, source metadata, confidence, support count, and redaction status into learning packets.
7. Runs a merger/judge LLM across packets from many instances.
8. Opens a GitHub pull request with proposed blueprint changes, evidence notes, conflict analysis, and rejected-candidate rationale.
9. Requires human expert review before merge.

After the reviewed blueprint increments `learning_generation`, each upgraded worker archives the previous generation journal and removes the generated hot-learning skill. Permanent private adaptation remains. The reviewed blueprint becomes the new common baseline; rejected, incorrect, or superseded transferable hot patches are intentionally not carried forward.

GitHub is the v1 governance surface. Use branch protection, rulesets, CODEOWNERS, checks, and pull requests. A future admin app may visualize and triage proposals, but it must write GitHub issues, branches, or pull requests rather than becoming a parallel blueprint source of truth.

## Consequences

- Workers remain simple and Hermes-native; they only edit local skills and explain why.
- Central extraction owns diffing, redaction, deduplication, conflict detection, and pull request creation.
- Private memory is not part of the shared consolidation input by default.
- Repeated patterns across multiple workers can be promoted with stronger confidence.
- Outliers can be preserved as conditional guidance when useful, or rejected when they appear to be mistakes or local-only context.
- Local worker behavior can improve immediately, while shared blueprint changes still require review.
- Instance-specific knowledge cannot be placed in the generation-scoped hot-learning lane; misclassification would cause it to disappear when the next learning generation is installed.
- Consolidation tooling must be strict about allowed paths and redaction before invoking the merger LLM.
