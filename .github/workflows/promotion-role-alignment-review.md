---
name: Promotion Role Alignment Reviewer
description: Checks that proposed Role Skills belong within the Role Blueprint's mission and job boundaries.
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
network:
  allowed:
    - defaults
    - local
tools:
  github:
    toolsets: [pull_requests, repos]
safe-outputs:
  add-labels:
    allowed: [role-aligned, role-drift]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
  remove-labels:
    allowed: [role-aligned, role-drift]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
  submit-pull-request-review:
    allowed-events: [COMMENT]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
    footer: "always"
timeout-minutes: 12
max-ai-credits: 700
---

# Promotion Role Alignment Reviewer

Review pull request **#${{ github.event.pull_request.number || inputs.pr_number }}** in `${{ github.repository }}`.

Treat the Role Blueprint's `SOUL.md`, existing Role Skills, tool configuration, and repository `SPEC.md` non-goals as the mission contract. Read the complete Promotion diff and `collective-learning-review.json`.

Evaluate whether each proposed Role Skill:

- directly supports the named job and its expected responsibilities;
- is useful across assignments rather than a single Worker or user;
- stays within the role's authority and does not invent executive, legal, HR, security, financial, or operational powers;
- avoids unrelated capabilities such as games, generic entertainment, personal hobbies, or broad autonomous behavior;
- complements rather than contradicts or needlessly duplicates existing Role Skills;
- remains small and domain-focused.

If any proposal is out of mission, too broad, contradictory, or unrelated:

1. Remove `role-aligned` if present.
2. Add `role-drift`.
3. Submit one `COMMENT` review headed `Role alignment gate: DRIFT`, identifying the proposal and whether it should be rejected, narrowed, or moved to another Role Blueprint.

If every proposal fits:

1. Remove `role-drift` if present.
2. Add `role-aligned`.
3. Submit one `COMMENT` review headed `Role alignment gate: ALIGNED`, connecting each proposed skill to explicit Role Blueprint responsibilities.

Never approve, edit, mark ready, or merge the PR.
