# Foundry Showcase Main Agent

The user-facing Microsoft Agent Framework agent for the Foundry Showcase.

Implemented and deployed:

- Foundry Hosted Agent version 9;
- Responses `2.0.0` and Invocations `1.0.0`;
- user-scoped Foundry Memory;
- Foundry Toolbox version 2 with governed case tools;
- three published immutable Agent Skills;
- client-visible approval and successful continuation for case writes;
- local profile, conversation, summary, and audit APIs;
- OpenTelemetry and Application Insights integration;
- evaluation and Agent Optimizer baseline configuration;
- unit coverage for approval continuation behavior.

The approval client override preserves only the current approval response during a service-managed continuation. It compensates for the current upstream client dropping that input and should be removed when the dependency includes the fix.

The MAF workflow, LangGraph A2A helper, Routines, AG-UI BFF, Agent 365, and Teams surfaces remain later milestones described in [..\PLAN.md](..\PLAN.md).
