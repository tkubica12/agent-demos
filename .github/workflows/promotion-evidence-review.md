---
name: Promotion Learning Evidence Auditor
description: Audits whether Collective Learning proposals remain within their signed Worker evidence.
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
    allowed: [evidence-sufficient, evidence-weak]
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
  remove-labels:
    allowed: [evidence-sufficient, evidence-weak]
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

# Promotion Learning Evidence Auditor

Review pull request **#${{ github.event.pull_request.number || inputs.pr_number }}** in `${{ github.repository }}`.

Read the complete diff and `collective-learning-review.json`. The review file is the retained, privacy-scanned explanation of the merger/judge decision; do not assume access to private Worker state.

For every proposed Role Skill, check:

- every supporting record ID and Worker named by the proposal is represented coherently in the review decision;
- the proposal does not claim broader behavior than the stated rationale and evidence support;
- independent Workers are not falsely presented as repeated support when their observations are merely complementary;
- conflicts, outliers, and rejected records are acknowledged rather than silently erased;
- the proposal preserves uncertainty and verification gates where evidence is incomplete;
- the Role Release increment and proposal count match the retained decision.

Evidence from one Worker may justify a narrow proposal; it must not be described as organization-wide consensus. Complementary evidence may be combined only when the rationale explains the shared outcome without inventing facts.

If support is missing, overstated, contradictory, or not traceable:

1. Remove `evidence-sufficient` if present.
2. Add `evidence-weak`.
3. Submit one `COMMENT` review headed `Evidence gate: WEAK`, identifying unsupported clauses or missing decision accounting.

If every proposal stays within the retained evidence:

1. Remove `evidence-weak` if present.
2. Add `evidence-sufficient`.
3. Submit one `COMMENT` review headed `Evidence gate: SUFFICIENT`, summarizing the traceability and any limitations.

Never approve, edit, mark ready, or merge the PR.
