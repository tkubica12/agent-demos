# ADR 0001: Direct Graph instance provisioning workaround

## Status

Accepted as a temporary workaround on 2026-07-20.

## Context

The preferred Agent 365 flow is:

1. publish the Foundry Hosted Agent as an Agent 365 template;
2. request an instance in Teams;
3. approve the instance request in Microsoft 365 admin center; and
4. create the agent user through Teams.

The tenant repeatedly returned generic publication and license failures from
that UI flow even after the following were verified:

- Agent 365 Frontier enrollment and available Agent 365 licenses;
- available Microsoft 365 E3 and Teams Enterprise licenses;
- an approved, uniquely named Foundry title;
- a fresh Foundry-managed blueprint and Bot Service relay;
- blueprint ownership, `AgentData.ReadWrite`, and inheritable scopes; and
- successful Hosted Agent Responses, Invocations, A2A, Toolbox, memory, and
  evaluation behavior.

The existing Hermes demo succeeds because it does not use the Teams instance
wizard. It creates the agent identity, agent user, licenses, and Agent 365
registration directly through Microsoft Graph.

## Options

1. **Wait for the official UI flow to be fixed.**
   - Keeps the architecture entirely on the documented onboarding path.
   - Leaves Teams acceptance blocked with no deterministic completion time.
2. **Add a classic Bot Framework compatibility application.**
   - Could make a normal Teams app reply.
   - Violates this progressive demo's native Agent 365 architecture and creates
     a second identity and publication contract.
3. **Provision only the Agent 365 instance through Microsoft Graph.**
   - Keeps Foundry as the runtime and retains the Foundry-managed blueprint.
   - Bypasses only the failing Teams instance wizard.

## Decision

Use option 3 temporarily.

`scripts/provision_agent365_instance.py` creates:

- an `agentIdentity` from the Foundry-managed blueprint;
- a linked `agentUser`;
- Agent 365, Microsoft 365 E3, and Teams Enterprise license assignments; and
- a beta Agent 365 registration.

`scripts/Connect-Agent365Instance.ps1` then grants the direct identity the
read-only policy MCP app role, adds it to the MCP caller allowlist, and grants
the Agent 365 observability role.

The registration permission uses an ephemeral app-only client. Its secret
exists only in process memory and the application is deleted in `finally`; no
credential is stored.

The released `microsoft-agents-authentication-msal` 1.1.0 package doesn't pass
the agent instance as `identity_config.fmi_path` when Foundry obtains the
agentic application token. `agent365_identity_patch.py` applies the same
secretless `DefaultAzureCredential` workaround previously carried by
Microsoft's Foundry Python autopilot sample. Remove the patch when the released
SDK includes the FMI behavior.

## Consequences

- Teams and Agent 365 can validate the real agent user without depending on the
  broken request-instance UI.
- The workaround uses a beta Graph registration API and therefore remains
  preview-dependent.
- This does not add a classic Azure Bot or separate runtime path.
- The official Teams flow remains the completion target.

## Revisit trigger

At each Agent 365 or Teams onboarding release:

1. retry **Request instance** and admin approval using a disposable instance;
2. verify the UI creates the identity, user, licenses, and registration;
3. compare the resulting objects with the scripted state; and
4. delete this workaround when the official flow is reliable.
