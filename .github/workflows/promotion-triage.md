---
name: Promotion Triage
description: Classifies Collective Learning Promotion pull requests and posts a concise release-impact summary.
on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - "autopilots-on-azure/blueprints/**"
  workflow_dispatch:
    inputs:
      pr_number:
        description: Pull request number to triage
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
    allowed: [promotion, role-release, collective-learning]
    max: 3
    target: "*"
    required-title-prefix: "Role Release "
  add-comment:
    max: 1
    target: "*"
    required-title-prefix: "Role Release "
    hide-older-comments: true
timeout-minutes: 10
max-ai-credits: 500
---

# Promotion triage

Triage pull request **#${{ github.event.pull_request.number || inputs.pr_number }}** in `${{ github.repository }}`.

This workflow handles only Collective Learning Promotion pull requests. Read the PR title, body, changed files, complete diff, `distribution.yaml`, and `collective-learning-review.json`.

If the PR is not a Role Release Promotion from a `collective-learning/` branch, produce no safe outputs.

For a valid Promotion:

1. Add all three labels: `promotion`, `role-release`, and `collective-learning`.
2. Post one concise comment containing:
   - source and proposed Role Releases;
   - changed Role Skills;
   - supporting Worker count and record count;
   - conflict and rejection counts;
   - whether the version increment and changed-file scope appear coherent;
   - the required next gates: privacy, role alignment, evidence, skill quality, and human review.

Do not approve, request changes, edit files, update the PR body, mark it ready, or merge it.
