# ADR 0002: Route Agent 365 Teams activities and reactions through the bridge

## Status

Accepted. Last reviewed 2026-07-22.

## Context

The Agent 365 AI teammate path and the installed Teams app/bot path share the Microsoft 365 Agents SDK Activity Protocol and connector concepts, but they have different identity, packaging, installation, and consent lifecycles:

- An Agent 365 AI teammate is created from an Agent 365 blueprint, provisioned as an Entra-backed Agent User, and added to Teams as a user or team member.
- A Teams app/bot is installed into a chat or team from a Teams app manifest with a `bots` capability.
- Teams Resource-specific Consent (RSC) is granted when a Teams app is installed or upgraded in a specific chat or team.

Our `agenticUserTemplates` package does not install a Teams app in the target Team. Live inspection confirmed that it creates no Team app installation and no RSC grant. Adding `authorization.permissions.resourceSpecific` to that package therefore did not enable delivery of all channel messages.

Activity Protocol defines possible activity shapes. It does not require every channel or identity model to emit every activity. Generic SDK support for `message`, `messageReaction`, `messageUpdate`, or `messageDelete` is not evidence that Agent 365 Teams routing will deliver every corresponding event.

The Agent 365 Notifications SDK is also not a subscription system for Teams conversation traffic. Version 1.0.0 handles email, Word/Excel/PowerPoint comment, and Agent User lifecycle notifications. It has no Teams-message subchannel or thread-follow subscription.

## Current capability matrix

| Capability | Agent 365 AI teammate status | Evidence |
| --- | --- | --- |
| Teams 1:1 message | Verified | Hermes and OpenClaw live tests |
| Explicit channel mention | Verified | Hermes and OpenClaw live tests |
| Targeted private message in a group conversation | Public developer preview; bridge detection exists, package opt-in and live delivery pending | Teams targeted-messaging documentation requires `supportsTargetedMessages` |
| Unmentioned channel message | Not delivered | Live bridge logs and Team/RSC inspection |
| Unmentioned reply in a thread where the agent already replied | Not delivered | Live test showed no bridge activity |
| Full thread history | Not pushed | Requires a separate Graph/MCP read; bridge currently keeps only delivered activities in local memory |
| Add/remove reaction | Public developer preview; verified outbound | Temporary `eyes` and semantic `heart` live tests |
| Receive reaction event | Public developer preview; not yet live-verified here | Teams documents reactions to messages sent by the agent |
| Typing indicator | Documented for 1:1 and small group chats; not channels | Implemented by the bridge for `personal` and `groupchat` conversations |
| Agent 365 Notifications package | Email, Office comments, lifecycle only | Package 1.0.0 source and Agent 365 notification documentation |

No public Microsoft documentation, SDK changelog, or roadmap item found in the 2026-07-09 review commits to an Agent User mode that receives all unmentioned Teams messages, follows a thread after one mention, or creates RSC grants from `agenticUserTemplates`.

## Decision

Keep the Agent 365-only architecture. Do not add a separate installed Teams app/bot merely to obtain all-message RSC delivery.

Use a split model:

- The bridge is an event router and transport/status UX layer.
- The bridge forwards eligible activities that Agent 365 actually delivers.
- The bridge adds and removes temporary `eyes` for forwarded public requests.
- The bridge sends typing indicators in 1:1 and small group chats, where Agent 365 documentation says Teams displays them.
- The selected runtime decides whether to answer, return `NO_RESPONSE`, or request a semantic reaction.
- The runtime requests semantic reactions with `TEAMS_REACTION: heart|smile|surprised|check|like`.
- The bridge strips control lines and executes reactions through the Agent 365-authenticated connector client.
- Do not reintroduce Bot Framework app-only token acquisition for Agent 365.

Eligible delivered events:

| Event | Forward? | Reason |
| --- | --- | --- |
| 1:1 message | Yes | Direct conversation |
| Explicit mention | Yes | Direct invocation |
| Targeted private message | Yes | Direct private invocation |
| Delivered reply in an active thread | Yes | Preserve continuity if the platform delivers it |
| Public unmentioned message or reply | Not currently delivered | Agent User packages create no Teams app installation or RSC grant |
| Reaction to an agent-authored message | Yes | Feedback/context when delivered |
| Reaction to an arbitrary human message | No by default | Teams documents reaction events for agent-authored messages |
| Agent's own messages/reactions | No | Avoid loops |
| System/member events | No by default | Enable only for a defined onboarding scenario |

## Consequences

- An active thread does not become subscribed after the agent is mentioned once. Follow-up replies must mention the agent unless Microsoft changes Agent User routing.
- The bridge cannot react to or answer an activity it never receives.
- Pulling channel history through Microsoft Graph or Work IQ MCP is a separate design. It needs permissions, polling or scheduling, deduplication, durable cursors, and an explicit privacy/cost policy; it is not a replacement for push delivery.
- Temporary `eyes` are removed after the runtime finishes, including `NO_RESPONSE`.
- Reactions remain preview functionality and require dedicated error/rate-limit handling.
- Bridge-local memory is sufficient for the demo but not durable shared conversation state.
- Targeted requests are private one-user/one-agent turns inside a group conversation. They expire from Teams clients after 24 hours and don't support reactions, replies, or forwarding.
- Targeted request content must not be copied into public group context. Publishing a private result requires an explicit user approval flow and a new public message.

## Review triggers

Review this ADR when any of these occur:

- Agent 365 documentation explicitly adds unmentioned/all-message Teams delivery for Agent Users.
- Agent 365 adds a conversation-follow or thread-subscription API.
- `microsoft-agents-a365-notifications` adds a Teams conversation subchannel or change-notification model.
- Agent 365 packages begin creating an installed Teams app or an RSC grant in the target resource.
- Microsoft publishes an Agent User-specific RSC flow.
- Inbound `messageReaction` behavior is live-verified for these instances.
- Agent 365 AI teammate packaging is confirmed to support the targeted-message manifest opt-in and `/AgentName` is live-verified.

## Sources

- [Microsoft Agent 365 SDK overview](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/agent-365-sdk)
- [Notify agents](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/notification)
- [Handle messages with the Agent 365 SDK](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/message-handling)
- [Create agent instance](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/create-instance)
- [Discover, create, and onboard agents with their own identity](https://learn.microsoft.com/en-us/microsoft-agent-365/onboard)
- [Understanding Activity Protocol](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/activity-protocol)
- [Get all channel and chat messages with RSC](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/channel-messages-for-bots-and-agents)
- [Build agents that use emoji reactions in Teams](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-reactions)
- [Send and receive targeted messages](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/targeted-messages)
- [Expose slash commands from agents and apps](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-slash-commands)
- [Agent 365 Python notifications design](https://github.com/microsoft/Agent365-python/blob/main/libraries/microsoft-agents-a365-notifications/docs/design.md)
