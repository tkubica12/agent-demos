# ADR 0003: Keep targeted private messages in a separate preview Teams package

## Status

Accepted.

## Context

Teams targeted private messages let a user and agent communicate privately inside a shared channel or group chat context. The feature is public developer preview.

The runtime bridge can detect targeted messages through `recipient.is_targeted` and can send targeted replies with `MessageActivityInput(...).with_recipient(..., is_targeted=True)`.

However, Teams upload validation rejected early attempts to add `supportsTargetedMessages` to the normal manifest because:

- the normal manifest used schema 1.21, where the property is not defined;
- manifest 1.25+ apps with `team` scope also require root `supportsChannelFeatures`;
- tenants/clients must have Teams Public Preview and custom app upload enabled to use the preview behavior.

## Decision

Keep the normal Teams package validator-safe and create a separate preview package for targeted private messages.

Normal package:

- Uses the stable manifest version.
- Omits `supportsTargetedMessages`.
- Supports 1:1, group chat, channel/team scopes, RSC observation, quoted replies, and reactions.

Preview package:

- Is generated explicitly with `scripts.package_teams_app --preview-targeted-messages`.
- Uses manifest version 1.29.
- Adds `bots[0].supportsTargetedMessages = true`.
- Adds root `supportsChannelFeatures = "tier1"` because schema 1.25+ requires it for apps with `team` scope.

## Consequences

- The default demo remains easy to upload in normal tenants.
- Targeted private messages can be tested only where Teams Public Preview and custom app upload are enabled.
- Documentation must tell users to type `/` in a channel/group compose box to discover the targeted private agent command.
- The bridge runtime can support targeted private responses even when the installed package does not expose the receive-targeted UX.
- Targeted messages remain preview-limited: they expire after 24 hours and do not support replies, forwarding, or reactions.
