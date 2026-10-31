# ADR 0010: Signed learning evidence and human Promotion

## Decision

Keep private adaptation local and let Hermes learn natively. Export only governed skill changes that the Worker owner chose to share, with their native ledger chain, through a signed Learning Packet; review shared behavior in Git.

- Personal Memory, Private Playbooks, and raw Work History never enter packets.
- Hermes' native skill ledger records every skill change with actor, action and before/after hashes. The Worker owner reviews changes in a private Teams digest and can undo them, approve or reject staged writes, and choose Share, Keep (private playbook) or Discard for Role Skill and Candidate Improvement changes.
- Packet 3.0 contains each shared skill's single `SKILL.md`, release-baseline hashes, ledger chain and owner decisions. Hashes are recomputed from disk; scripts, references, and other artifacts are excluded, and every unshared or untraceable change is listed as excluded.
- Human approval binds the digest and exact Worker state. The gateway holds the Ed25519 private key; Workers/reviewers receive public keys.
- Signed `reject_and_refresh` authorizes discard instead of export. It is a different disposition, not an approved empty packet.
- Central review compares compatible releases, support, conflicts, role fit, and privacy. Promotion creates a draft PR; only human review permits merge and subsequent refresh.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Direct skill synchronization | Leaks assignment-specific information and promotes local mistakes automatically. |
| Custom per-turn provenance transaction | Duplicated the native skill ledger, needed its own prompt contract and recovery, and blocked native background review. |
| Narrative proposals alone | Loses the exact artifact and baseline relationship. |
| Separate review database/UI | Duplicates Git's review and release source of truth. |

Signatures establish origin/integrity, not quality; mandatory independent evaluation judges behavior. The owner's Share decision, not an agent-written rationale, admits a change to the shared role.

Full validation, evaluation, and refresh contracts are in [SPEC.md](../../SPEC.md); runnable commands are in [DEMO.md](../../DEMO.md).
