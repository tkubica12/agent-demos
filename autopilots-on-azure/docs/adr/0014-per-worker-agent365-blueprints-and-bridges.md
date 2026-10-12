# ADR 0014: Isolate each Worker's deployment envelope

## Decision

Each Worker owns its Agent 365 platform blueprint, messaging endpoint/gateway, Agent Identity, Agent User, Terraform workspace, Data Disk, API/approval keys, and local operator state. Its five service-role Sandbox Groups each have a distinct user-assigned identity.

Workers may share the Git Role Blueprint, Role Release, images, Foundry deployment, networking, and MCP resource applications—not profile state or workload identities. Collective review groups evidence by role/release/commit, not platform blueprint.

On 2026-10-09, retain this mapping for the current demo/hackathon and defer a
shared Gateway. Sharing is not
rejected as a long-term architecture. Blueprint, channel registration, Gateway,
Worker and learning-cohort mappings remain a decision point to revisit, not an
approved migration.

## Alternatives and rationale

| Alternative | Trade-off |
| --- | --- |
| One blueprint and shared Worker router | Fewer endpoints/consents, but requires a security-critical identity-to-profile registry and introduces shared failures. |
| Shared Gateway with independent Worker blueprints | Pools idle compute without merging registrations, but one process authorized for many Workers still has a larger compromise and failure boundary. |
| Blueprint, channel registration and Gateway per role; separate Worker instances | Pools transport for roles such as project manager and aligns with reusable role behavior, but role membership alone does not establish a business security boundary. Deferred. |
| Pool per business/credential boundary, potentially role plus domain | Separates retail, wholesale or sales authority where needed; may split a learning cohort across several blueprints and Gateways. Deferred. |
| Indirection in front of per-Worker gateways | Retains duplicated services while adding routing and authorization complexity. |
| Per-Worker blueprint and gateway | Selected: direct, inspectable Agent User → endpoint → compute → state mapping. |

Isolation costs repeated gateway/MCP services, identities, and consent
administration. An always-running Gateway per inactive Worker leaves a compute
floor despite Hermes scaling to zero. This is acceptable for rapid hackathon
changes and troubleshooting, not a settled large-scale economic choice.

Endpoint changes are owner-sensitive and potentially destructive; [DEPLOYMENT.md](../../DEPLOYMENT.md) defines the preflight and isolated-login procedure.

## Deferred mapping decision

A candidate is one project-manager Agent 365 blueprint, channel registration
and Gateway serving several project-manager instances for different teams or
projects. Each instance would remain a separate Hermes Worker with its own
Agent Identity, Agent User, profile and Data Disk. This is a proposal, not an
instruction to merge the existing deployment envelopes.

Do not conflate these groupings:

| Grouping | Purpose |
| --- | --- |
| Role and release | Reusable behavior, skills and compatible learning evidence |
| Learning cohort | Which generalized changes may be reviewed and promoted together |
| Business/security domain | Which data, permissions and compromise risks may be shared |
| Agent 365 blueprint | Credential and inherited-permission boundary |
| Gateway pool | Shared transport capacity, deployment and failure boundary |
| Worker instance | Individual agent authority, assignment, private state and lifecycle |

Project managers in retail and wholesale may share useful role skills without
being allowed to share credentials, documents or private memory. Conversely, a
business domain may contain several roles. Neither a role label nor a blueprint
automatically authorizes learning exchange. [ADR 0010](0010-candidate-improvements-and-collective-learning-review.md)
continues to govern private adaptation, signed evidence and human promotion;
this discussion does not authorize automatic skill synchronization or a new
swarm runtime.

Before selecting a future mapping, revisit:

- Whether blueprints should follow role, business domain, credential boundary
  or a combination; keep individual Agent Identities and Agent Users distinct.
- Which notification registrations and authenticated routing bindings the
  platform supports. Agent 365 configures the messaging endpoint at blueprint
  level; a separate classic Azure Bot Service resource per Worker must not be
  assumed.
- Which authority a shared Gateway actually needs, including federation,
  runtime access and learning-packet signing; separate blueprints cannot
  isolate credentials accessible to the same compromised process.
- How replicas recover acknowledged work and isolate caches, diagnostics and
  state. The current Gateway performs no normal-turn model inference but holds
  recent Teams context and in-process work; it is not yet a stateless router.
- How Gateway versions, canary routing, draining and rollback avoid affecting
  every Worker during development, and what pooled idle compute actually costs.
- How learning eligibility, privacy and promotion remain independent of shared
  hosting or role membership.

Microsoft documents [one blueprint to many agent identities](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/identity),
[blueprint-level messaging configuration](https://learn.microsoft.com/en-us/microsoft-agent-365/developer/create-instance)
and [blueprint selection by trust boundary](https://learn.microsoft.com/en-us/entra/agent-id/how-to-plan-agent-identity-architecture).
These contracts motivate revisiting the mapping; they do not prove our current
Gateway can multiplex those instances unchanged.
