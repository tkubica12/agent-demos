---
name: dream-reflection
description: Reflect across Work History and improve memory, playbooks, or reusable skills.
---

# Dreaming

Use this skill only for an explicit dream run.

1. Review several recent sessions, outcomes, corrections, repeated tool patterns, failed approaches, Personal Memory, Private Playbooks, Role Skills, Candidate Improvements, and any local `learning/quarantine` observations.
2. Classify each observation as Personal Memory, Private Playbook, Role Skill improvement, new Candidate Improvement, duplicate, or do not store.
3. Use the memory tool for compact Personal Memory.
4. Use `skill_manage` category `private` for rich assignment-specific knowledge or procedure.
5. Patch an existing Role Skill only for a generalized reusable correction. Read it before patching.
6. Use `skill_manage` category `candidates` for a new reusable capability that does not fit an existing Role Skill.
7. Never place people, customers, tenants, raw message text, document excerpts, credentials, identifiers, internal URLs, private paths, or assignment-specific details in Role Skills or Candidate Improvements.
8. For every governed change, propose 1–10 privacy-safe, response-only test scenarios specific to that change. Each scenario contains exactly `scenarioId` (unique lowercase kebab-case), `input`, `setupAssumptions` (1–10 nonempty strings), `expectedObservableOutcomes` (1–10 nonempty strings), `acceptanceCriteria` (1–20 assertions), and `scope` (tested behavior and limitations).
   Each assertion contains exactly `observable: "response.text"`, `operator` (`contains`, `not_contains`, or `equals`), and a nonempty literal string `value`. Matching is case-sensitive. Do not generate executable code, commands, tool-action assertions, or environment setup procedures.
9. Return the exact provenance block requested by the bridge. Include one provenance object for every Role Skill or Candidate Improvement changed, including its `agentProposedScenarios`, and none for Personal Memory or Private Playbooks. The runtime stores provenance version 3.0; Learning Packets version 2.0 retain the cumulative provenance array for each artifact.
10. These are agent-proposed tests, not executed evaluations or independent evidence. A separate real Hermes baseline/candidate evaluation must also use operator-supplied independent regression or holdout cases. Never claim quality improved merely because the proposed cases look plausible or pass.
11. Governed artifacts support only creation or patching of one `SKILL.md` with YAML frontmatter whose `name` matches the skill directory and whose `description` is nonempty. Do not delete governed skills or add scripts, references, or bundles.
12. Never edit `learning/records.jsonl` directly or bypass a runtime rollback. Invalid changes may be rejected with an explicitly signed `reject_and_refresh` disposition; this is not approval or an export and authorizes only a subsequent normal Role Release refresh.

Finish with concise counts of Personal Memory updates, Private Playbooks changed, Role Skills patched, Candidate Improvements created, duplicates, rollbacks, and observations intentionally not stored.
