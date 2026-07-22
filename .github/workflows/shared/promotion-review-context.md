---
description: Deterministically fetches the exact Promotion pull request head for semantic reviewers.
permissions:
  contents: read
  issues: read
  pull-requests: read
steps:
  - name: Prefetch authoritative Promotion context
    env:
      GH_TOKEN: ${{ github.token }}
      PR_NUMBER: ${{ github.event.pull_request.number || inputs.pr_number }}
      REPOSITORY: ${{ github.repository }}
      ROLE_PATH: autopilots-on-azure/blueprints/junior-project-manager
    run: |
      set -euo pipefail
      context=/tmp/gh-aw/agent
      head_root="$context/pr-head"
      mkdir -p "$head_root"

      gh pr view "$PR_NUMBER" \
        --repo "$REPOSITORY" \
        --json number,title,body,state,isDraft,baseRefName,headRefName,headRefOid,labels,files \
        > "$context/pr-meta.json"
      gh pr diff "$PR_NUMBER" --repo "$REPOSITORY" > "$context/pr.diff"
      gh api "repos/$REPOSITORY/pulls/$PR_NUMBER/reviews" --paginate > "$context/pr-reviews.json"
      gh api "repos/$REPOSITORY/pulls/$PR_NUMBER/comments" --paginate > "$context/pr-review-comments.json"

      head_sha=$(jq -r '.headRefOid' "$context/pr-meta.json")
      git fetch --no-tags --depth=1 origin "$head_sha"
      git archive "$head_sha" "$ROLE_PATH" | tar -x -C "$head_root"
---

## Authoritative Promotion context

Use only these prefetched artifacts when evaluating the pull request:

- `/tmp/gh-aw/agent/pr-meta.json`
- `/tmp/gh-aw/agent/pr.diff`
- `/tmp/gh-aw/agent/pr-reviews.json`
- `/tmp/gh-aw/agent/pr-review-comments.json`
- `/tmp/gh-aw/agent/pr-head/autopilots-on-azure/blueprints/junior-project-manager/`

The ordinary repository checkout may represent the base branch. Never use it as the proposed Role Release and do not fetch PR data agentically.
