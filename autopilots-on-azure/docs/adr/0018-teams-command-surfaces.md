# ADR 0018: Small, implemented Teams command surface

## Decision

Document personal-chat text commands `/learn <instruction>` and `/new`; `/reset` is also accepted. `/learn` selects one constrained native learning turn. `/new` creates a distinct native session generation without deleting durable memory.

Reject `/new` and `/reset` in group/channel contexts. Do not advertise native gateway commands such as `/compress`, `/model`, or `/usage` without a corresponding implemented API operation.

The Agent User package remains `agenticUserTemplates`-only. No companion `bots[]` capability, prompt-starter workaround, or private-to-public response fallback is added.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Advertise every Hermes gateway command | The API integration does not implement that entire command set. |
| Reset shared group context from `/new` | One participant would alter other participants' active context. |
| Add a second bot for targeted commands | Creates another identity and packaging lifecycle. |
| Treat targeted UI as sufficient privacy | Requires real recipient-targeted delivery and isolated runtime context too. |

Targeted private learning requires a supported Agent User receive/send contract and a private per-user transcript before it can be exposed. Package/UI observations and current implementation limits are recorded once in [SPEC.md](../../SPEC.md).

## References

- [Teams command surfaces](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-slash-commands)
- [Targeted messages](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/targeted-messages)
- [Session lifecycle](0017-messaging-session-lifecycle.md)
