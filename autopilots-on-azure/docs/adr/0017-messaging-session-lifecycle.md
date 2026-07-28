# ADR 0017: Messaging transcript rotation with stable Hermes memory identity

- Status: Accepted
- Date: 2026-07-25

## Context

Teams personal chats reuse one `conversation.id` for the lifetime of the installation. Teams channels and group chats also provide stable conversation identifiers, with a thread root only when users reply in a real thread. Slack classic messaging is similar unless an application explicitly keys by `thread_ts`; only Slack's Assistant surface creates a natural new AI thread.

Neither platform defines an AI session lifecycle. Daily reset, idle reset, compaction, bounded history, summaries, durable memory, and explicit new-topic behavior are application policy.

The bridge sends Teams activities to Hermes through its API-server surface rather than a native Hermes messaging-platform adapter. Hermes exposes two independent identifiers:

- `X-Hermes-Session-Id` scopes the short-term transcript and is expected to rotate on `/new` semantics.
- `X-Hermes-Session-Key` scopes stable channel memory and is expected to persist across transcript rotations.

The previous bridge used a stable Teams-derived value as `X-Hermes-Session-Id` indefinitely. One real personal-chat session accumulated 116 active messages, 32 tool calls, about 250,000 active characters, 766 compacted historical messages, and 755,000 cumulative input tokens. A document/tool turn then failed in `/v1/responses` with HTTP 500.

A tentative fix rotated `X-Hermes-Session-Key` daily. Research showed this inverted Hermes' documented contract: it bounded the transcript indirectly but disconnected cross-day memory.

## Native capabilities

Hermes' native gateway platforms provide:

- daily and idle session reset through `session_reset`;
- per-user group isolation;
- automatic context compression at a configurable context-window threshold;
- protected recent-message tails;
- `/new`, `/reset`, and `/compress`;
- persistent Personal Memory, Work History, and optional stable-key memory plugins.

Hermes' API-server routes do not evaluate the native gateway reset policy. API clients must create, rotate, end, and delete transcript sessions through `/api/sessions`.

## Options considered

### One transcript for the lifetime of a Teams conversation

Rejected because mixed topics and tool results grow without a platform-provided boundary. Compression alone did not prevent the observed provider failure.

### Rotate the Hermes memory key daily

Rejected because `X-Hermes-Session-Key` is the stable cross-transcript memory identity. Rotating it breaks the exact continuity mechanism it is meant to preserve.

### Add a custom persistent bridge continuity journal

Deferred. A bounded visible-message journal could preserve exact recent wording across cold starts, but it duplicates Hermes state and creates another privacy, retention, and cleanup contract. Native Hermes memory and Work History must be evaluated first.

### Model-generated rolling summaries

Deferred. Summaries are useful for continuous long tasks but add model cost, latency, interpretation risk, and privacy handling. Hermes already has native compression and durable memory layers.

### Rotate transcript IDs and keep memory keys stable

Selected because it follows Hermes' API contract and the standard four-layer design used by Agent Framework, Semantic Kernel, LangGraph, OpenClaw, and production messaging agents: bounded short-term transcript, reducer/compaction, durable memory, and a stable platform segmentation key.

## Decision

1. Derive a stable Hermes memory key from Worker, source, authenticated user, and a hash of the Teams conversation/thread key.
2. Never include a date or transcript generation in `X-Hermes-Session-Key`.
3. Derive `X-Hermes-Session-Id` from source, a conversation/thread hash, and a bounded time bucket.
4. Rotate API transcripts hourly by default through `HERMES_API_SESSION_ROTATION_HOURS`. Align buckets relative to `HERMES_API_SESSION_RESET_HOUR_UTC`; values from 1 to 24 hours are supported.
5. Create missing transcript sessions through `POST /api/sessions` before using `/api/sessions/{id}/chat`.
6. Use the native stateful session chat endpoint as the primary path. `/v1/responses` and chat completions remain compatibility fallbacks only for unsupported endpoint responses.
7. Keep follow-ups within the current bucket in the same transcript. Rely on Hermes Personal Memory, stable-key memory, and Work History across rotations; exact raw transcript continuity is intentionally bounded.
9. Keep attachment-extracted content out of durable memory, skills, provenance, and cross-session summaries. A user can reference the shared file again through its Microsoft 365 URL when exact document context is needed later.
10. Keep group/channel memory isolated by authenticated user and platform conversation/thread boundary. Do not share one user's private continuity with another participant.
11. Add explicit `/new` or `/reset` mapping and an idle-based rotation only when implemented through the same native `/api/sessions` lifecycle; do not add a parallel store.
12. Preserve Hermes' native compression defaults and protected recent tail. Retry does not substitute for context bounding.
13. Pass `/new`, `/reset`, and `/learn` to the runtime as raw commands rather than embedding them in the formatted Teams event prompt.
14. A reset creates a unique native generation (`<time-bucket>:new:<id>`) instead of deleting and recreating the same session ID. Later turns query native session inventory and select the newest generation, so reset continuity survives bridge restarts without a parallel bridge store.

## Why hourly rotation

Teams users do not normally create AI sessions. A predictable automatic boundary is therefore necessary. The initial daily policy was insufficient for document-heavy use: one same-day transcript reached 61 messages, 26 tool calls, and about 499,000 cumulative input tokens before the model failed after structure inspection. Hourly rotation:

- prevents unlimited mixed-topic growth;
- keeps ordinary short follow-ups coherent while sharply bounding tool-heavy context;
- follows Hermes' native transcript and stable-memory separation;
- does not require bridge-side summarization;
- allows cross-day durable facts to flow through the stable memory key rather than raw replay.

## Consequences

- The provider no longer receives a lifetime Teams transcript.
- Direct `/invoke` smoke calls with different conversation IDs no longer collapse into one transcript.
- Same Teams conversation and user retain one stable memory identity across hourly transcript generations.
- The first turn in a new bucket starts a clean transcript but can still use durable memory and search prior Work History.
- Exact wording from the previous bucket is not automatically replayed. This is deliberate; a bounded continuity summary can be added later only if real use demonstrates a gap.
- Session rows become hourly artifacts on the Worker Data Disk and remain subject to Hermes maintenance/archive policies.
- The bridge must keep Session ID and Session Key semantics separate in every endpoint and test.
- A successful `/new` acknowledgement now proves that a distinct native session generation exists; conversationally replying to the command without changing the transcript is a failure.

## References

- [Hermes session lifecycle](https://github.com/NousResearch/hermes-agent/blob/main/docs/session-lifecycle.md)
- [Hermes context compression](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/context-compression-and-caching.md)
- [Microsoft bot state concepts](https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-concept-state)
- [Microsoft Agent Framework conversations](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/)
- [Teams channel and group conversations](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-and-group-conversations)
- [Slack agent development](https://docs.slack.dev/ai/developing-agents/)
- [OpenClaw session concepts](https://github.com/openclaw/openclaw/blob/main/docs/concepts/session.md)
- [LangGraph memory concepts](https://docs.langchain.com/oss/python/concepts/memory)
