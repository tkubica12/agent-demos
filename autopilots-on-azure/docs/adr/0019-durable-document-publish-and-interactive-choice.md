# ADR 0019: Durable document publishing with predefined Teams suggested actions

- Status: Accepted
- Date: 2026-07-27

## Context

Hermes edits existing Word documents offline with reviewed Open XML tooling, then attempts an ETag-protected Graph `PUT /content` to the same drive item. This preserves the canonical sharing URL, comments, tracked revisions, version history, and Agent User attribution when Microsoft 365 accepts the replacement.

Word and Teams can hold a WOPI/coauthoring lock that rejects this binary replacement with `423 notAllowed`. WOPI locks are client-owned rather than user-owned, expire after 30 minutes unless refreshed, and are not exposed through the Graph replacement response as a named holder. Keeping an interactive agent turn open for that period wastes context and model cost and still cannot guarantee release.

The user needs a low-friction choice:

1. keep trying the original without keeping the chat session active;
2. receive an explicitly shared editable copy immediately.

If background publication still cannot update the original after 24 hours, the prepared work must not disappear. The accepted fallback is an explicitly shared copy and a proactive explanation.

Word comment notifications have a different interaction surface. The comment thread is already the review workspace, so a failed body publish must still return the exact proposed content and rationale there rather than reducing the result to a lock notice.

## Options considered

### Keep the agent turn running

Rejected. Hermes has a bounded 600-second turn timeout, WOPI locks can outlive the turn, and repeated model calls add no value to a deterministic storage retry.

### Store a generic Hermes cron prompt

Rejected for the retry loop. It would persist private operation identifiers in model prompts, pay for repeated inference, expose tool choice to nondeterminism, and mix a storage state machine with user-authored schedules.

### Ask the user to type `retry` or `copy`

Rejected as the primary UX. It is unnecessarily slow and ambiguous in Teams, and duplicate or delayed messages are harder to bind safely to one operation.

### Let Hermes generate arbitrary Adaptive Card JSON

Deferred to A16. Card schema, actions, accessibility, localization, security, host compatibility, and idempotency require a governed UI contract. A model should not invent consequential action payloads.

### Predefined card plus deterministic durable operation

Selected. The agent prepares and validates the edit once; the bridge owns the interaction and the runtime owns durable state.

## Decision

1. Keep foreground behavior bounded:
   - prepare and validate the edit once;
   - attempt same-item upload;
   - perform at most two short retries approximately 20 seconds apart;
   - retain the validated edit privately when the lock persists.
2. After an attachment turn, the bridge queries the runtime for pending document choices. It does not depend on model-authored control markers.
3. Render predefined Teams `suggestedActions` with two `Action.Submit` actions:
   - **Keep trying original**
   - **Send shared copy now**
   The accompanying text says Microsoft 365 usually releases the lock within an hour but can take longer. This is user guidance, not an SLA; WOPI clients can refresh the 30-minute lock timer.
4. Encrypt and authenticate the card action token. Bind it to Worker, user, Teams conversation, operation, and expiry. Never put a plaintext operation ID in the card.
5. Make `operation_scope` a required MCP argument for every publish, retry, copy, and cancel tool. Prompt guidance alone is insufficient because a model can omit optional arguments; unscoped retained edits must never be surfaced to a conversation.
6. Accept both Teams message-value and invoke-action payload shapes. Treat payload data as untrusted and validate user, conversation, Worker, expiry, and action allowlist.
7. The copy action:
   - creates a file in the Agent User's `Hermes Results` folder;
   - grants the invoking user a verified Graph `write` permission;
   - sends the accessible URL/file through the originating Teams conversation;
   - records an idempotent terminal receipt.
8. The background action:
   - extends private operation retention to 24 hours;
   - stores staged bytes, edit intent, ETag, recipient, delivery reference, attempt, deadline, and sanitized Graph error only on the Worker Data Disk;
   - schedules `document.publish.retry` on the existing per-Worker Service Bus queue;
   - puts only operation ID, attempt, Worker ID, and due time in Service Bus.
9. Process retries through fixed internal runtime APIs without an LLM turn. Use bounded backoff of 2, 5, 15, 30, and 60 minutes, then hourly.
10. Before every retry:
   - retrieve current metadata and ETag;
   - safely rebase guarded Word text-node edits when possible;
   - never overwrite an unexplained source change.
11. On success, proactively report that changes were merged into the original and include the canonical URL.
12. On a terminal rebase conflict or after 24 hours, create/share the copy and proactively explain why the original was not replaced.
13. Keep terminal receipts for seven days so Service Bus redelivery and duplicate card clicks do not repeat document mutations.
14. Use a durable delivery lease before sending a terminal result. Duplicate handlers do not send while a lease is active; failed sends release the lease; successful sends retry receipt acknowledgement before leaving an uncertain lease for bounded reconciliation.
15. A terminal operation is complete only after Teams returns a concrete delivery activity ID and that ID is persisted.
16. For Word comment-originated work, keep the response in the originating thread. If body publication fails, include the exact proposed content, concise rationale, explicit not-applied status, and instructions to mention the Agent User again to retry after closing the document.

## Work IQ Word review trigger

Work IQ Word remains the managed-first path for new documents, semantic reads, comments, and replies. New documents created under Agent User identity must receive explicit participant permissions before delivery.

Review this ADR and ADR 0016 whenever Work IQ Word exposes a supported server-side operation for arbitrary existing-body or range edits that participates in Microsoft 365 coauthoring. Such an API could replace offline package mutation and binary publication for supported edits.

## Consequences

- User interaction is one click rather than a typed command.
- WOPI lock duration no longer consumes an agent session.
- Background retries reuse the proven Service Bus/KEDA wake path.
- Private document bytes remain on the Worker Data Disk and never enter queue messages, learning, or diagnostics.
- The bridge and runtime gain a small deterministic document state machine.
- External Teams delivery cannot be made transactionally atomic with local storage. The delivery lease closes normal retry races and makes the rare send/ack crash window explicit instead of silently duplicating.
- Teams receives native suggested-action buttons. Word comments use a review-first text fallback that preserves the proposed work even when body publication fails.
- A live Agent 365 proactive-delivery probe preserved text but stripped an Adaptive Card attachment. Suggested actions are therefore the proven narrow A14 surface; Adaptive Cards and MCP Apps remain A16 research.
- The card is intentionally predefined. Broader agent-authored or MCP-rendered UI remains an A16 decision.

## References

- [WOPI concepts and locks](https://learn.microsoft.com/microsoft-365/cloud-storage-partner-program/rest/concepts)
- [Microsoft 365 for the web coauthoring](https://learn.microsoft.com/microsoft-365/cloud-storage-partner-program/online/scenarios/coauth)
- [Teams suggested actions](https://learn.microsoft.com/microsoftteams/platform/bots/how-to/conversations/suggested-actions)
- [Graph driveItem invite](https://learn.microsoft.com/graph/api/driveitem-invite)
- [ADR 0016](0016-office-document-collaboration.md)
- [ADR 0015](0015-service-bus-backed-hermes-cron.md)
