# Foundry Showcase Main Agent

The user-facing Microsoft Agent Framework agent for the Foundry Showcase.

Implemented and deployed:

- Foundry Hosted Agent version 16;
- Responses `2.0.0` and Invocations `1.0.0`;
- user-scoped Foundry Memory;
- Foundry Toolbox version 2 with governed case tools;
- three published immutable Agent Skills;
- client-visible approval and successful continuation for case writes;
- local profile, conversation, summary, and audit APIs;
- OpenTelemetry and Application Insights integration;
- evaluation and Agent Optimizer baseline configuration;
- unit coverage for approval continuation behavior.
- checkpointed MAF case-resolution workflow with deterministic risk branching;
- authenticated A2A policy delegation through a dedicated Foundry Toolbox;
- exact structured policy invocation for workflow and Invocations callers;
- policy-denial blocking before proposal creation;
- correlated main-agent, Toolbox, and LangGraph helper traces.

The approval client override preserves only the current approval response during a service-managed continuation. It compensates for the current upstream client dropping that input and should be removed when the dependency includes the fix.

The MAF workflow and bounded LangGraph A2A helper are deployed. Routines, the AG-UI BFF, Agent 365, and Teams surfaces remain later milestones described in [..\PLAN.md](..\PLAN.md).
