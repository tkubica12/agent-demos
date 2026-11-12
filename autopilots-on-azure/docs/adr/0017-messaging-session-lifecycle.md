# ADR 0017: Bounded transcripts, stable memory identity

## Decision

Rotate Hermes API transcripts while preserving the stable memory key.

- `X-Hermes-Session-Id`: source, conversation/thread hash, and hourly bucket by default.
- `X-Hermes-Session-Key`: Worker, source, authenticated user, and conversation/thread hash; no time bucket.
- Create/query native sessions, freeze the selected transcript, then admit `/v1/runs` with a stable idempotency key and persist the returned run ID before polling. A known run is recovered by that exact ID, never by guessing the newest assistant message.
- Personal `/new` or `/reset` creates a distinct generation. Subsequent turns recover the newest generation from native inventory, including after gateway restart.
- Keep follow-ups inside the active bucket; use native Personal Memory and Work History across rotations. Exact raw wording is not automatically replayed.

`HERMES_API_SESSION_ROTATION_HOURS` accepts 1–24 hours; `HERMES_API_SESSION_RESET_HOUR_UTC` aligns buckets. Native compression remains available inside a transcript.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Lifetime Teams transcript | Accumulates unrelated topics and large tool/document results. |
| Rotate the memory key | Breaks cross-transcript identity instead of bounding short-term context. |
| Gateway-owned conversation memory/history | Duplicates native memory/history. The selected transport journal is a narrow receipt/outbox/payload store, not another agent memory. |
| Recover a failed chat request from the latest assistant message | Cannot bind the response or external effects to the exact original native admission. |
| Model-generated continuity summaries | Adds inference cost and another derived-private-content store. |

Group transcripts remain conversation-based; user-specific memory keys do **not** isolate them. Personal Memory files and Private Playbooks are also Worker-wide, not per-caller profiles. Do not treat a group as a private continuity boundary. Attachment content is private turn context, excluded from durable adaptation.

See [ADR 0018](0018-teams-command-surfaces.md) for the command surface and [SPEC.md](../../SPEC.md) for current privacy/tracing limits.
