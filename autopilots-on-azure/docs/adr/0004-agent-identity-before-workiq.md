# ADR 0004: Establish Agent 365 identity before broad Work IQ MCP integration

## Status

Accepted.

## Context

An autopilot runtime can already participate in Teams through the bridge. The next product milestone is Agent 365 identity, followed by Work IQ MCP access to Microsoft 365 data and actions.

In collaborative Teams contexts, one conversation contains many people, but one incoming activity has one sender. OBO access is for the invoking or consenting human user, not for every participant. Agent-owned actions should use the agent identity, not a random user's OBO token.

Adding Work IQ MCP before resolving identity would risk confusing:

- agent-owned actions versus user-owned actions;
- public channel context versus targeted/private context;
- what data can be safely shown in a shared thread;
- where consent is required.

## Decision

Implement Agent 365 identity and explicit auth-boundary metadata before broad Work IQ MCP integration.

Milestone order:

1. Register/create the Agent 365 blueprint and instance for each deployed bridge endpoint.
2. Capture agent instance/user identifiers and lifecycle ownership.
3. Add identity context to every Teams/Agent prompt envelope:
   - agent instance/user ID when known;
   - incoming human sender ID and display name;
   - available auth mode: `agent_identity`, `obo_user`, `none`, or `unknown`;
   - privacy boundary: public channel/group, targeted private message, or 1:1.
4. Add diagnostics for chosen auth mode without logging tokens or private data.
5. Add Work IQ MCP scenarios only after identity selection is explicit.

## Consequences

- The selected runtime can make safer decisions about whether to answer publicly, answer privately, or ask a user to authenticate.
- Work IQ tool calls must carry an explicit identity mode.
- If a tool requires OBO and no user token/consent exists, the selected runtime must ask that user to authenticate rather than silently using agent identity.
- If a tool result contains private user data in a public thread, the selected runtime should prefer targeted private response or ask before sharing publicly.
- The existing Teams bot package remains a fallback/demo path until the Agent 365 instance is validated.
