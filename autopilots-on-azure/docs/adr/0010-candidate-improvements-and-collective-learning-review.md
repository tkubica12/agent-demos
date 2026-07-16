# ADR 0010: Promote Candidate Improvements through Collective Learning Review

## Status

Accepted.

## Context

Hermes can self-improve locally by writing Personal Memory and creating or patching skills. Across Workers of one Role Release, that creates a useful but risky split:

- Some adaptations are Personal Memory or Private Playbooks and must never be shared.
- Some Role Skill patches and new skills are Candidate Improvements that could improve the Role Blueprint.
- Some apparent learnings are mistakes, one-off local context, or statistically weak outliers.

We need the Role Blueprint to improve without blindly merging every Worker's Personal Memory, Private Playbooks, or local skill edits.

Options considered:

1. Directly sync local worker skills back into the shared blueprint.
2. Have a central process diff local worker files against the base blueprint and infer everything from diffs.
3. Have each worker create only narrative GitHub issues describing desired improvements.
4. Have each worker keep local file changes plus a rationale journal; central tooling computes exact diffs and creates learning packets for LLM and human review.
5. Build a custom database/admin app as the primary consolidation surface.

Direct sync is unsafe because it can leak private context and overfit the blueprint to one assignment. Central diff-only keeps exact changes but loses the instance's "why". Issue-only proposals preserve rationale but discard exact file context. A custom admin app may be useful later, but it is unnecessary as the first source of truth and would duplicate GitHub review mechanics.

## Decision

Use a hybrid Learning Packet flow.

Each Worker may self-improve locally, but it must classify durable adaptation before storing it:

- Personal Memory.
- Private Playbook.
- Role Skill improvement.
- New Candidate Improvement.
- Do not store.

For Role Skill improvements and Candidate Improvements, the Worker records provenance in `learning\records.jsonl`. The Worker does not compute Git diffs.

Foreground learning and Dreaming may create or patch native Hermes skills. The provenance validator links eligible changes to evidence, confidence, hashes, and the originating stage.

Use two distinct local durability lanes:

- Private adaptation: Personal Memory, Private Playbooks, and Work History. This survives every Worker Refresh and is excluded from Collective Learning Review.
- Release-scoped adaptation: local Role Skill patches and Candidate Improvements with linked provenance. The Worker uses these in a fresh session; Worker Refresh replaces or retires them after export.

Candidate Improvements may be produced during foreground learning or Dreaming. Both paths use the same provenance and privacy boundary. Workers never write the central Role Blueprint directly.

Collective Learning Review does the following:

1. Reads the Worker manifest to identify the Role Blueprint and Role Release commit.
2. Exports only Role Skill diffs, Candidate Improvements, and linked provenance.
3. Excludes Personal Memory, Private Playbooks, Work History, credentials, logs, workspace data, and secrets.
4. Checks out the recorded Role Release commit.
5. Computes exact diffs centrally.
6. Combines diffs, rationale journal entries, evidence summaries, source metadata, confidence, support count, and redaction status into learning packets.
7. Runs a merger/judge across packets from many Workers.
8. Opens a GitHub pull request with proposed Role Blueprint changes, evidence notes, conflict analysis, and rejected-candidate rationale.
9. Requires human expert review before Promotion.

After Promotion publishes a new Role Release, each Worker Refresh archives previous-release provenance and Candidate Improvements, replaces Role Skills, and preserves Personal Memory, Private Playbooks, and Work History. Rejected or superseded Candidate Improvements are intentionally not carried forward.

GitHub is the v1 governance surface. Use branch protection, rulesets, CODEOWNERS, checks, and pull requests. A future admin app may visualize and triage proposals, but it must write GitHub issues, branches, or pull requests rather than becoming a parallel blueprint source of truth.

## Consequences

- Workers remain Hermes-native; they edit local skills and record why.
- Central extraction owns diffing, redaction, deduplication, conflict detection, and pull request creation.
- Personal Memory and Private Playbooks are not Collective Learning Review inputs.
- Repeated patterns across multiple workers can be promoted with stronger confidence.
- Outliers can be preserved as conditional guidance when useful, or rejected when they appear to be mistakes or local-only context.
- Local Worker behavior can improve in the next fresh session, while Role Blueprint changes still require Promotion.
- Assignment-specific knowledge belongs in a Private Playbook, never a Candidate Improvement.
- Collective Learning Review must fail closed on allowed paths and privacy checks before invoking the merger/judge.
