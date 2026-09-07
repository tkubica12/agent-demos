# ADR 0019: Durable document publication and explicit user choice

## Decision

Prepare/validate an edit once. If bounded foreground attempts cannot update the original, retain it privately and offer predefined Teams suggested actions:

- **Keep trying original**: deterministic background publication, then an explicitly shared copy if the deadline or a terminal rebase conflict prevents same-item publication.
- **Send shared copy now**: create an Agent User copy, verify the invoking user's write permission, then deliver its URL.

Action tokens are encrypted/authenticated, Worker/user/conversation/operation-bound, expiring, and idempotent. The model cannot author action payloads. Every retained operation requires a hashed stable conversation scope; opaque IDs never enter visible responses or learning.

Staging TTL defaults to one hour, configurable within 5 minutes–24 hours. Choosing background retry creates a separate 24-hour retry deadline and sets staged-file expiry one hour beyond it for completion/cleanup.

Background retries use `document.publish.retry` on the existing Worker queue, without an LLM turn. The gateway sends and receives using its own managed identity. Queue content is limited to operation ID, attempt, Worker ID, and due time. Private bytes, edit intent, ETag, recipient, and delivery reference remain on the Data Disk.

Retry after 2, 5, 15, 30, and 60 minutes, then hourly, within a 24-hour deadline. Re-read the ETag; rebase only reconstructible guarded edits. Verify explicit sharing before copy delivery and delete an unshareable copy.

Persist mutation receipts and delivery leases; keep terminal receipts seven days. Delivery completes only after Teams returns an activity ID and runtime acknowledgement is durable. The external send/receipt crash window can still duplicate a message.

Word comment requests remain in their originating thread. Failed body publication returns exact proposed content, rationale, not-applied status, and a fresh-mention retry instruction.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Keep the agent turn open | Locks can outlive the turn budget; repeated inference adds no value. |
| Store a generic cron prompt | Exposes private operation metadata to model context and makes retries nondeterministic. |
| Require a typed `retry`/`copy` response | Harder to bind safely to a particular retained edit. |
| Arbitrary model-authored card JSON | Allows invented actions and unsafe payloads. |

WOPI locks expire after 30 minutes unless refreshed; a Graph lock response may not identify its holder. Report sanitized service diagnostics, not a guessed person. A copy has independent sharing, comments, and history.

Reconsider when Work IQ supports coauthoring-compatible server-side body edits.

- [WOPI locks](https://learn.microsoft.com/microsoft-365/cloud-storage-partner-program/rest/concepts)
- [Graph sharing permissions](https://learn.microsoft.com/graph/api/driveitem-invite)
- [Office editing decision](0016-office-document-collaboration.md)
