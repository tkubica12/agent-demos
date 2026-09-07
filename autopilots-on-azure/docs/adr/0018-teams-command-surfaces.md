# ADR 0018: Teams command discovery and scope-specific semantics

- Status: Accepted
- Date: 2026-07-26

September 6, 2026, 21:46 CEST recheck: the user found Hermes in public group `@` mention discovery but not under `/`. No private content was sent. Targeted Agent User inbound availability remains unobserved for this deployment; this does not establish universal platform incompatibility or a pure UI bug. Shared group transcripts remain unchanged; the conditional private-transcript requirement below is not approval to switch the current group model. See ADR 0017 for the memory-isolation boundary. Never fall back from private input to a public reply.

## Context

Hermes has native gateway slash commands, but Autopilots invokes Hermes through its API-server surface. The API server does not execute `gateway/slash_commands.py`; unhandled slash text is sent to the model as ordinary conversation content.

The bridge currently implements `/learn` itself. `/new`, `/reset`, `/compress`, `/status`, and other native gateway commands previously had no real effect through Teams and could elicit a misleading model-generated acknowledgement.

Teams also has multiple command-discovery surfaces:

- in personal chats, prompt starters are always available through the **View Prompts** flyout; only their initial cards disappear after the first message;
- custom agent slash autocomplete uses targeted messaging in channels, group chats, and meeting chats;
- manifest configuration only inserts command text into the compose box; the application must still parse and implement the command;
- command changes require manifest versioning, republishing, and package update.

## Options considered

### Advertise every Hermes gateway command

Rejected because most native gateway commands have no API-server equivalent. Advertising them would create success-shaped UI for functionality that is not implemented.

### Add personal prompt starters for `/learn` and `/new`

Deferred. Prompt starters remain available through View Prompts after the first message, but they do not participate in `/` autocomplete and may not materially improve discoverability. The commands remain documented text commands until user testing justifies the extra manifest surface.

### Expose `/new` in group conversations

Rejected. A public `/new` ambiguously resets shared context for every participant. A targeted-private `/new` would require a second per-user reset model inside a shared thread with little user value.

### Expose targeted-private `/learn` in groups

Selected for A15, but only after the bridge provides a private per-user transcript and a true targeted response. Teams UI privacy alone is insufficient if the runtime transcript later feeds a public response.

## Decision

1. Support exactly two explicit personal-chat text commands for now:
   - `/learn <instruction>`;
   - `/new`, with `/reset` as an undocumented compatibility alias.
2. Implement personal `/new` in the bridge with Hermes native session CRUD. It deletes and recreates the active transcript while preserving the stable Hermes memory key and durable memory.
3. Reject `/new` and `/reset` outside `teams_personal`; never pass them to the model.
4. Do not implement or advertise `/compress`, `/status`, `/model`, `/usage`, or other Hermes gateway commands until a real API-server operation exists.
5. Do not add personal prompt starters now. Document `/learn` and `/new` in the Teams welcome/help experience and demo guide.
6. Defer A15 until a supported targeted receive/send path is verified for this Agent User package and SDK; do not add a companion bot package.
7. In A15, expose `/learn` as a named targeted-private command only after:
   - inbound `recipient.isTargeted` is verified;
   - the targeted transcript is isolated by authenticated user and group conversation;
   - responses use Teams targeted-message APIs;
   - targeted content is excluded from public context, summaries, Work History, and public replies.
8. Do not expose `/new` in group, channel, or meeting scopes.
9. Do not inject `bots[]` into an `agenticUserTemplates` package. Keep Agent User packages on the supported Microsoft 365 Agent Registry lifecycle.

## Deferral trigger and recheck

The current generated package is version **1.1.7**, schema **devPreview**, with **`agenticUserTemplates` only and no `bots[]`**. Refreshed official Learn still says receive eligibility requires `bots[].supportsTargetedMessages=true`; it separately describes explicit recipient targeting for outbound Teams SDK/REST messages. Those receive/send contracts must not be conflated.

Installed **`microsoft-agents-hosting-core` 1.1.0** `TurnContext` has no `send_targeted_activity`, and plain `send_activity` does not set targeting. This is the installed SDK's observed surface, not proof that Teams universally cannot send targeted messages. The actual UI observation is narrower: public `@` discovery works, `/` discovery for Hermes was absent. No private payload was sent or fallback attempted.

In the July 28 test, the `agenticUserTemplates` package was accepted through Microsoft 365 Agent Registry but `/WorkerName` was not observed. Teams app-store upload returned `Agentic apps are not supported for uploading from Teams/Teams Admin Center`. The account's preview policy was `Global / Forced`. These historical package/policy observations did not establish a universal cause; the September UI recheck remains scoped to the current account, client, and package.

Re-evaluate A15 only when Microsoft documents or ships a supported Agent 365 Agent User path that:

1. declares targeted-message receive capability in the standard `a365 publish --aiteammate` package;
2. is uploaded through the supported Microsoft 365 Agent Registry lifecycle without a companion bot app;
3. exposes `/WorkerName` in channels, group chats, and meeting chats;
4. delivers inbound `recipient.isTargeted` and accepts a targeted response to the invoking user.

To check availability, review the Agent 365 CLI release notes and generated AI teammate manifest, then upload an unmodified package through Microsoft 365 admin center. With Teams Public preview enabled, verify `/WorkerName` appears and capture one inbound/outbound Activity Protocol exchange before implementing transcript isolation, private `/learn`, cards, attachments, or proactive targeted delivery.

## Consequences

- Personal users must know `/learn` and `/new`; Teams does not provide custom personal `/` autocomplete.
- The command set remains intentionally small and truthful.
- Targeted group command discovery was absent in this deployment's UI recheck. Do not generalize the observation to every Agent User or diagnose a UI-only defect without more evidence.
- Targeted messages appear in the group flow but are visible only to one user and the Worker, expire from clients after 24 hours, and do not support reactions, replies, or forwarding.
- Agent 365 remains the identity and Activity Protocol layer; the Teams app manifest owns command discoverability.

## References

- [Expose slash commands from agents and apps](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-slash-commands)
- [Send and receive targeted messages](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/targeted-messages)
- [Highlight agent capabilities with prompt starters](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/prompt-starters)
- [Teams bot command lists schema](https://learn.microsoft.com/en-us/microsoft-365/extensibility/schema/root-bots-command-lists)
- [ADR 0017: Messaging session lifecycle](0017-messaging-session-lifecycle.md)
