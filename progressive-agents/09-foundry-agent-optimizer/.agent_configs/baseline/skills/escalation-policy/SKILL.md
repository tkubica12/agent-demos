---
name: escalation-policy
description: Decide whether a Contoso Outdoors support case requires escalation. Use for legal threats, injury claims, press inquiries, high-value refunds, safety issues, or severe customer complaints.
metadata:
  version: "1.0.0"
  canary: "ESC-CANARY-7742"
---

# Escalation policy

Use this skill when a support conversation may require escalation.

Process:

1. Read `references/escalation-matrix.md` before deciding.
2. Classify the case as `standard`, `specialist-review`, or `urgent-escalation`.
3. Explain the classification in one sentence.
4. If escalation is needed, include the exact escalation canary from the matrix.

Do not guess escalation thresholds from memory. Read the matrix resource.
