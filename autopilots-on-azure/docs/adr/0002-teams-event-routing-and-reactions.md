# ADR 0002: Agent 365 event routing and reactions

## Decision

Use Agent 365 Agent Users, not a second installed Teams bot. The gateway authenticates and routes delivered activities; Hermes decides whether to answer, remain silent, or request a semantic reaction.

- Personal messages and explicit mentions invoke the Worker.
- With observation enabled, added reactions to remembered agent-authored messages provide feedback; removed reactions do not invoke Hermes.
- Delivered message updates record diagnostics only; there is no delete-event handler.
- The gateway owns temporary processing `eyes`, including removal after completion/failure, and typing in personal/group chats.
- Hermes requests `TEAMS_REACTION: eyes|like|heart|smile|surprised|check`; the gateway strips the control line and calls the authenticated connector. `NO_RESPONSE` suppresses text.
- Undirected thanks after an agent response can receive a direct `like` acknowledgement.
- Email and Office comment notifications use their originating workload response channel.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Companion bot with all-message RSC | Adds a second identity, package, installation, and consent lifecycle. |
| Poll every conversation through Graph | Adds permissions, cursors, privacy retention, deduplication, and cost; not equivalent to push routing. |
| Gateway decides every semantic reply/reaction | Moves agent behavior into transport code. |

An Activity Protocol handler is not a subscription. The gateway can process only events delivered to it; a mention does not subscribe it to all subsequent channel traffic. Bridge-local context is bounded and non-durable.

Current targeted-message and routing boundaries are consolidated in [SPEC.md](../../SPEC.md); command handling is in [ADR 0018](0018-teams-command-surfaces.md).

## References

- [Agent 365 message handling](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/message-handling)
- [Agent 365 notifications](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/notification)
- [Teams reactions](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-reactions)
