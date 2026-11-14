---
name: autopilots-deploy
description: Deploy and operate independent private Agent Chat Simulator, gateway and Hermes stacks on Azure Container Apps Sandboxes, with digest-pinned releases, Entra or temporary demo authentication, and canary-first rollout.
---

# Autopilots deployment

Use this skill when a customer's engineer wants to deploy the public universal
system. Invoke it as `autopilots-deploy` in a skill-capable coding agent, or ask
the agent to read this file. Install the complete folder at
`.agents/skills/autopilots-deploy/`; do not install only SKILL.md.

## Establish the deployment contract

Before Azure writes, establish the tenant, explicit subscription, original
operator, private network/DNS access, isolated stack inventory, role release,
image/source release and authentication mode. Keep customer inputs and state
outside source control. Never change the shared Azure CLI default subscription.

Explain both modes and obtain the customer's choice:

| Mode | User experience | Appropriate use |
| --- | --- | --- |
| `entra` (recommended) | Actual tenant login; assigned users/groups receive User or Admin access | Customer deployment with accountable identities |
| `demo` | Users choose any display name; access code is not proof of identity | Temporary private trial with test data only, explicit acknowledgement and expiry |

Azure Owner is not an Entra application administrator. If directory rights are
missing, use the generated admin request; do not substitute client secrets or
silently downgrade to demo. See [authentication](references/authentication.md).

## Prepare and deploy

1. Verify the immutable package/source and image release. The
   [release procedure](references/releases.md) refuses mismatched commits and
   mutable tags. The supplied release example is deliberately not deployable.
2. Run [prerequisite checks](scripts/Test-DeployPrerequisites.ps1), initialize
   [the workspace](scripts/Initialize-Workspace.ps1), and fill
   [private stack inputs](assets/stacks.example.toml) and
   [DNS IDs](assets/dns-zones.example.json). Add N explicit stacks, each with
   its own resource group and delegated subnet. Do not confuse multiple agents
   in a single environment with independent stacks.
3. Review `plan` before writes. Deploy one canary with `--stack <name>` first;
   run staged commands from [operations](references/operations.md).
4. Verify health, lifecycle and private networking, then actual customer
   browser/chat behavior. `all` is not full acceptance.
   Follow [verification](references/verification.md); stop rollout on a failure.
5. Roll out remaining selected stacks only after canary acceptance. Preserve
   state, identities, data disks and image/source pins.

See [channel modes](references/channel-modes.md) for the simulator versus real
Agent 365 boundary and [Foundry](references/foundry.md) for shared/per-stack
quota and isolation tradeoffs.

## Authority and stopping rules

Private Endpoint is the customer path: no public fallback, TLS bypass, protected
state import, live-resource adoption, mutable image tags or image relabelling.
Request approval for Azure writes, destructive teardown and customer identity
changes unless already explicitly delegated. Never print secrets or export
access tokens. Use device-code authentication when interactive login is needed.

Do not change simulator, gateway or runtime source to work around a failed
deployment. Capture the failing stage and redacted evidence; repair its actual
cause. Never present a health receipt or a manually written acceptance JSON as
proof of browser behavior.

## Known limitations

This path uses ordinary single-tenant app registrations, not Entra Agent ID
blueprints or Agent 365. Simulator stacks disable agentic M365/collaboration
tools and scheduling. No Teams licence is required. Shared model capacity
does not eliminate regional quota or throttling. Image upgrades can replace
Sandboxes and change service URLs; stable URLs are conditional on retaining
the same Sandbox. Browser widgets, upload/download, normal-TLS private access
and real model replies require separate evidence against the actual release.
