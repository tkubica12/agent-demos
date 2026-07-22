---
name: delivery-commitment-control
description: Record delivery decisions and dependency handoffs with acceptance verification and escalation criteria.
---

# Delivery Commitment Control

Use this skill whenever a decision or cross-party dependency handoff can affect delivery scope, schedule, ownership, or acceptance criteria.

## Record delivery decisions

For each delivery-affecting decision, record:

1. Decision: the agreed outcome.
2. Decision owner: the accountable party making or approving the decision.
3. Effective date: when the decision takes effect. If unknown, mark it unresolved; do not infer it.
4. Affected commitments: commitments changed or confirmed, including accountable owners and due dates where known.
5. Impact: the delivery, scope, schedule, risk, or acceptance impact.
6. Follow-up actions: actions required to implement or verify the decision.
7. Linked control record: the relevant plan, action tracker, change request, or dependency record.

## Define dependency handoffs

Use a handoff record whenever one party supplies work, information, access, a decision, or a service that another party needs to continue delivery.

For each handoff, record:

1. Provider: the accountable party supplying the dependency.
2. Receiver: the accountable party consuming or verifying it.
3. Deliverable: the specific item or service to be supplied.
4. Acceptance criteria: observable conditions the receiver will use to assess the deliverable.
5. Needed-by date: the date by which acceptance is required to protect the dependent plan.
6. Verification status: `pending` until assessment, `accepted` when every criterion is met, or `rejected` with unmet criteria recorded.
7. Linked plan or change record: the control record that owns the handoff.

Do not treat a handoff as committed when any required field is missing. Record unresolved ownership, acceptance criteria, or timing as a planning risk.

Set verification status to `pending` until the receiver assesses the deliverable. Set it to `accepted` only when every acceptance criterion is met; otherwise set it to `rejected` and record the unmet criteria.

Before marking a handoff complete, confirm that its verification status is `accepted`.

## Escalate incomplete or time-critical handoffs

Flag a handoff for escalation when its needed-by date will pass before verification status reaches `accepted`. State the affected commitment, delivery impact, unmet acceptance criteria, accountable provider and receiver, needed-by date, and required next action.

## Minimum decision record

Complete one record per delivery-affecting decision. Fill every field; mark an unknown value as `unresolved`.

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

Complete one record per dependency handoff. Fill every field and update verification status as the receiver assesses the deliverable.

| Field | Record |
|---|---|
| Provider | |
| Receiver | |
| Deliverable | |
| Acceptance criteria | |
| Needed-by date | |
| Verification status | |
| Linked plan or change record | |
