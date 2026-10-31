---
name: dream-reflection
description: Reflect across Work History and improve memory, playbooks, or reusable skills.
---

# Dreaming

Use this skill only for an explicit dream run.

1. Review several recent sessions, outcomes, corrections, repeated tool patterns, failed approaches, Personal Memory, Private Playbooks, Role Skills, and Candidate Improvements.
2. Classify each observation as Personal Memory, Private Playbook, Role Skill improvement, new Candidate Improvement, duplicate, or do not store.
3. Use the memory tool for compact Personal Memory.
4. Use `skill_manage` category `private` for rich assignment-specific knowledge or procedure.
5. Patch an existing Role Skill only for a generalized reusable correction. Read it before patching.
6. Use `skill_manage` category `candidates` for a new reusable capability that does not fit an existing Role Skill.
7. Never place people, customers, tenants, raw message text, document excerpts, credentials, identifiers, internal URLs, private paths, or assignment-specific details in Role Skills or Candidate Improvements.
8. A shared Role Skill patch or Candidate Improvement is exported as one `SKILL.md` with YAML frontmatter whose `name` matches the skill directory and whose `description` is nonempty. Keep governed changes in that form; put supporting files only in Private Playbooks.
9. Do not delete governed skills. The owner can undo any change from the learning digest; native skill history records every write.
10. Do not claim that behavior improved. A separate real baseline/candidate evaluation with independent holdout cases decides that.

Finish with concise counts of Personal Memory updates, Private Playbooks changed, Role Skills patched, Candidate Improvements created, duplicates, and observations intentionally not stored. Do not include provenance or JSON blocks.
