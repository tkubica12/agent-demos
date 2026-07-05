# ADR 0002: Keep Teams bridge as event router and status UX layer

## Status

Accepted.

## Context

Teams collaborative agents can receive direct mentions, 1:1 messages, targeted private messages, replies in active threads, unmentioned channel/group messages through RSC, and reaction events.

We considered three approaches for deciding when to react or answer:

1. Bridge handles all emoji and response decisions with rules or a small classifier.
2. OpenClaw handles all emoji and response decisions.
3. Split responsibilities between bridge and OpenClaw.

The bridge sees Teams metadata early and can provide fast UX feedback, but it has limited context. OpenClaw has richer model reasoning, SOUL.md/personality, tool results, and conversation context, but responds later.

## Decision

Use a split model:

- The bridge is an event router and transport/status UX layer.
- The bridge should forward eligible Teams events with metadata instead of making semantic importance decisions.
- The bridge may add temporary `eyes` as a status reaction for forwarded messages: "OpenClaw saw this and is looking into it."
- OpenClaw decides whether to answer, whether to return `NO_RESPONSE`, and which semantic reaction to use.
- OpenClaw can request semantic reactions through a control channel such as `TEAMS_REACTION: heart|smile|surprised|check|like`.
- The bridge strips reaction control lines from visible Teams text and executes them through Teams APIs.

Eligible events to forward:

| Event | Forward? | Reason |
| --- | --- | --- |
| 1:1 message | Yes | Direct conversation |
| Explicit `@OpenClaw` mention | Yes | Direct invocation |
| Targeted private message to OpenClaw | Yes | Direct private invocation |
| Reply in a thread where OpenClaw participated | Yes | Conversation continuity |
| Public unmentioned message with RSC consent | Yes | OpenClaw decides whether to jump in |
| Reaction to OpenClaw's own message | Yes | Feedback/context |
| Reaction to arbitrary human message | No by default | Too noisy and not necessarily about OpenClaw |
| Bot's own messages/reactions | No | Avoid loops |
| System/member events | No by default | Mostly noise unless a later onboarding scenario needs them |

## Consequences

- The bridge stays predictable and cheap.
- OpenClaw owns personality and semantic judgment.
- A small classifier model in the bridge is deferred. If added later, it should only throttle forwarding in very high-volume channels, not decide final emoji or answer content.
- The demo implementation can use bridge-local memory to remember OpenClaw sent message IDs and filter reaction events to OpenClaw-owned messages. Production needs durable shared conversation state.
- Temporary eyes should be removed when OpenClaw finishes deciding, whether it answers or returns `NO_RESPONSE`, so status reactions do not stack with semantic reactions.
