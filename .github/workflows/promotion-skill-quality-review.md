---
name: Promotion Skill Quality Reviewer
description: Reviews promoted Role Skills for correctness, progressive disclosure, duplication, and actionable structure.
on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - "autopilots-on-azure/blueprints/**"
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request number to review
        required: true
        type: string
permissions:
  contents: read
  issues: read
  pull-requests: read
  copilot-requests: write
engine:
  id: copilot
tools:
  github:
    toolsets: [pull_requests, repos]
safe-outputs:
  add-labels:
    allowed: [skill-quality-clear, skill-quality-risk]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
  remove-labels:
    allowed: [skill-quality-clear, skill-quality-risk]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
  create-pull-request-review-comment:
    max: 3
    target: "*"
    required-title-prefix: "Role Release "
  submit-pull-request-review:
    allowed-events: [COMMENT]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
    footer: "always"
timeout-minutes: 12
max-ai-credits: 800
---

# Promotion Skill Quality Reviewer

Review pull request **#${{ github.event.pull_request.number || inputs.pr_number }}** in `${{ github.repository }}`.

Read the complete diff, existing Role Skills, `SOUL.md`, `distribution.yaml`, and `collective-learning-review.json`.

Check each proposed `SKILL.md` for:

- valid frontmatter, unique globally useful name, and discovery-oriented description;
- progressive disclosure: the description should trigger only relevant work and the body should avoid always-on role duplication;
- concrete, executable guidance with explicit inputs, outputs, verification, failure handling, and escalation where needed;
- contradictions, semantic duplication, overlap, or naming collisions with existing Role Skills;
- accidental expansion beyond the retained merger/judge decision;
- vague slogans, excessive prose, fake capabilities, unsupported tools, or non-testable requirements;
- consistency between the proposed skill, `distribution.yaml` Role Release, and review decision.

For at most three concrete changed-line defects, create inline review comments with actionable fixes.

If any material issue exists:

1. Remove `skill-quality-clear` if present.
2. Add `skill-quality-risk`.
3. Submit one `COMMENT` review headed `Skill quality gate: RISK`.

If the skill is coherent and ready for human review:

1. Remove `skill-quality-risk` if present.
2. Add `skill-quality-clear`.
3. Submit one `COMMENT` review headed `Skill quality gate: CLEAR`.

Never approve, push fixes, mark ready, or merge the PR.
