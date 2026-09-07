# ADR 0020: Separate chat cards from generated applications

## Decision

Use native Teams cards for bounded interactions and child Sandboxes for full generated web applications.

| Surface | Contract |
| --- | --- |
| Consequential card | Reviewed typed actions; model supplies bounded visible content, never operation IDs/tokens/callback payloads. |
| Informational card | Display-only DSL compiled and validated by reviewed code. |
| Generated app | Hermes writes/tests source; governed deployment creates a child Sandbox with native Entra participant access. |
| MCP Apps | No additional host or parallel declarative-agent identity is implemented. |

The gateway owns Teams rendering, authenticated action handling, idempotency, and text fallback. A card selection supplies only its visible label to the continuation.

Generated apps have their own per-Worker Group/identity, deny-default egress, explicit participants, artifact/app quotas, five-minute idle suspension, and native retention. The ADC proxy authenticates users, wakes OnDemand compute, and serves traffic directly. Gateway ownership covers deployment/inventory/lifecycle only.

Owner-bound retention/delete actions call native lifecycle APIs without model execution, Worker wake, or Service Bus. Inventory is scoped to the requesting user. Updates retain logical app identity; failure retains the previous working deployment.

## Alternatives and rationale

| Alternative | Reason not selected |
| --- | --- |
| Arbitrary Adaptive Card JSON | Schema validity does not make consequential action data safe. |
| Reviewed fixed layouts only | Unnecessarily restricts harmless informational presentation. |
| Serve generated apps from the Hermes process | Mixes arbitrary user code, private agent state, availability, and lifetime. |
| Use one UI technology for every host | Teams cards, MCP Apps widgets, and shared web apps have different identity and runtime contracts. |

Applications intended for continued operation require reviewed source and a maintained deployment, not indefinite demo-Sandbox retention. Current rendering/client limitations are in [SPEC.md](../../SPEC.md).

## References

- [Teams cards](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/cards-reference)
- [Universal Actions](https://learn.microsoft.com/en-us/microsoftteams/platform/task-modules-and-cards/cards/universal-actions-for-adaptive-cards/overview)
- [ACA Sandboxes](https://github.com/microsoft/azure-container-apps/tree/main/plugin/skills/aca-sandboxes)
