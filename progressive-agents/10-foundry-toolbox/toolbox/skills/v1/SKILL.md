---
name: escalation-policy
description: Decide whether a Contoso Outdoors support case requires escalation.
metadata:
  release: "1"
---

# Escalation policy

Use this skill for legal threats, injury claims, press inquiries, high-value
refunds, safety issues, or severe complaints.

Classify the case as `standard`, `specialist-review`, or `urgent-escalation`.
Explain the classification in one sentence. Include `ESC-CANARY-7742` when
escalation is needed.

| Trigger | Classification |
| --- | --- |
| Routine return, exchange, care, or shipping question | standard |
| Refund request from $250 to $500 | specialist-review |
| Refund request over $500 | urgent-escalation |
| Injury, product safety concern, legal threat, chargeback threat, or press inquiry | urgent-escalation |
| Repeated unresolved complaint after two prior support contacts | specialist-review |
