# ADR 0010: Signed learning evidence and human Promotion

## Decision

Keep private adaptation local. Export only governed skill changes, cumulative evidence, and synthetic proposed cases through a signed Learning Packet; review shared behavior in Git.

- Personal Memory, Private Playbooks, and raw Work History never enter packets.
- Provenance 3.0 links each exact `SKILL.md` create/patch to hashes, Worker/release, generalized rationale, privacy result, and declarative agent-proposed scenarios.
- Packet 2.0 retains the complete baseline-to-final chain. Scripts, references, and other artifacts are excluded.
- Human approval binds the digest and exact Worker state. The gateway holds the Ed25519 private key; Workers/reviewers receive public keys.
- Signed `reject_and_refresh` authorizes discard instead of export. It is a different disposition, not an approved empty packet.
- Central review compares compatible releases, support, conflicts, role fit, and privacy. Promotion creates a draft PR; only human review permits merge and subsequent refresh.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Direct skill synchronization | Leaks assignment-specific information and promotes local mistakes automatically. |
| File diffs alone | Loses why the Worker changed its behavior. |
| Narrative proposals alone | Loses the exact artifact and baseline relationship. |
| Separate review database/UI | Duplicates Git's review and release source of truth. |

Signatures establish origin/integrity, not quality. Agent-proposed cases remain distinct from mandatory independent evaluation. Local transactions reject malformed, private, or unprovenanced governed writes; quarantine/reconciliation handles direct-CLI drift.

Full validation, evaluation, and refresh contracts are in [SPEC.md](../../SPEC.md); runnable commands are in [DEMO.md](../../DEMO.md).
