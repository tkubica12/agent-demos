---
name: delivery-commitment-control
description: Record delivery decisions and dependency handoffs with acceptance verification and escalation criteria.
---

# Delivery Commitment Control

Use this skill whenever a decision, dependency handoff, or requested change can affect delivery scope, schedule, ownership, acceptance criteria, or commitments.

## Record delivery decisions

For each delivery-affecting decision, record:

1. Decision: the agreed outcome.
2. Decision owner: the accountable party making or approving the decision.
3. Effective date: when the decision takes effect. If unknown, mark it unresolved; do not infer it.
4. Affected commitments: commitments changed or confirmed, including accountable owners and due dates where known.
5. Impact: the delivery, scope, schedule, risk, or acceptance impact.
6. Follow-up actions: actions required to implement or verify the decision.
7. Linked control record: the relevant plan, action tracker, change request, or dependency record.

Do not silently rewrite a plan when a commitment changes. Record the requested change, impact, decision owner, decision, and effective date.

## Define dependency handoffs

Use a handoff record whenever one party supplies work, information, access, a decision, or a service that another party needs to continue delivery.

For each handoff, record:

1. Provider: the accountable party supplying the dependency.
2. Receiver: the accountable party consuming or verifying it.
3. Deliverable: the specific item or service to be supplied.
4. Acceptance criteria: observable conditions the receiver will use to assess the deliverable.
5. Needed-by date: the date by which acceptance is required to protect the dependent plan.

Do not treat a handoff as committed when any required field is missing. Record unresolved ownership, acceptance criteria, or timing as a planning risk.

Set verification status to `pending` until the receiver assesses the deliverable. Set it to `accepted` only when every acceptance criterion is met; otherwise set it to `rejected` and record the unmet criteria.

Before marking a handoff complete, confirm that its verification status is `accepted`.

## Escalate incomplete or time-critical commitments

Flag commitments and handoffs as incomplete when they lack an accountable owner, due date or needed-by date, acceptance criteria, decision, or effective date where applicable.

Escalate promptly when an unresolved decision, dependency, or timing issue threatens a critical-path commitment. State:

- Blocker
- Delivery impact
- Accountable decision owner
- Latest safe decision date
- Required next action

## Minimum decision record

| Field | Record |
|---|---|
| Decision | |
| Decision owner | |
| Effective date | |
| Affected commitments | |
| Impact | |
| Follow-up actions | |
| Linked control record | |

## Minimum dependency handoff record

| Field | Record |
|---|---|
| Provider | |
| Receiver | |
| Deliverable | |
| Acceptance criteria | |
| Needed-by date | |
| Verification status | |
| Linked plan or change record | |
