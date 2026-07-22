---
name: Promotion Privacy Guardian
description: Reviews Role Release Promotions for private information or assignment-specific leakage.
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
    allowed: [privacy-clear, privacy-risk]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
  remove-labels:
    allowed: [privacy-clear, privacy-risk]
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

# Promotion Privacy Guardian

Review pull request **#${{ github.event.pull_request.number || inputs.pr_number }}** in `${{ github.repository }}`.

Read the complete PR diff plus the Role Blueprint's `SOUL.md`, `distribution.yaml`, and `collective-learning-review.json`. Review only the proposed Promotion, but use the existing Role Blueprint to understand whether generic-looking text could reconstruct private source material.

Check every changed Role Skill and review record for:

- people, customers, accounts, projects, teams, tenants, email addresses, identifiers, internal URLs, credentials, tokens, private paths, or raw message fragments;
- assignment-specific instructions disguised as reusable guidance;
- unique markers, dates, names, or combinations that could reveal the source Worker's Personal Memory, Private Playbooks, or Work History;
- rationale or evidence that contains more private detail than needed;
- instructions that encourage future Workers to persist or export private content.

If any plausible leak exists:

1. Remove `privacy-clear` if present.
2. Add `privacy-risk`.
3. Submit one `COMMENT` review headed `Privacy gate: RISK`, listing each concrete finding and the minimum safe generalization.

If no plausible leak exists:

1. Remove `privacy-risk` if present.
2. Add `privacy-clear`.
3. Submit one `COMMENT` review headed `Privacy gate: CLEAR`, naming the inspected artifacts and explaining why they remain generalized.

Never approve the PR, edit files, reveal suspected private source values in full, mark the PR ready, or merge it.
