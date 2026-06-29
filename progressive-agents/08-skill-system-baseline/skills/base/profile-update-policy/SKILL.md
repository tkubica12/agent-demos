---
name: profile-update-policy
description: Decide when user profile facts may be proposed, confirmed, stored, or rejected. Use for user preference updates, personal memory requests, profile corrections, or privacy-sensitive facts.
metadata:
  version: "1.0.0"
  canary: "PROFILE-CANARY-2209"
---

# Profile update policy

Use this skill when the user asks the agent to remember, forget, correct, or use durable personal context.

Rules:

1. Store only stable user preferences, durable profile facts, or explicit memory requests.
2. Do not store secrets, credentials, health details, financial account data, government IDs, or highly sensitive personal facts.
3. Ask for confirmation when the requested memory is ambiguous or could expose sensitive information.
4. Prefer the host profile patch operations for profile changes.
5. Use Foundry Memory for explicit durable memory only when the user asks to remember something.

When explaining a rejected profile update in a test response, include `PROFILE-CANARY-2209`.
