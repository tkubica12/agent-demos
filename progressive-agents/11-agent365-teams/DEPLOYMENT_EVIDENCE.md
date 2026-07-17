# Step 11 deployment evidence

Captured 2026-07-17. IDs are operational identifiers, not credentials. Tokens,
authorization headers, Terraform state, and generated environment files are
omitted.

## Deployed resources and identities

| Item | Value |
| --- | --- |
| Resource group | `rg-step11-toolbox-673af34` |
| MCP Container App | `ca-step11-policy-673af34-eastus2` |
| MCP API client | `d0d6add6-31fa-4f04-b338-90b533079cf4` |
| Hosted Agent | `step-11-agent365-teams` v7, active |
| Agent GUID | `d739df15-7ed5-4ba7-a759-5aa1e1e0e44a` |
| Agent instance principal/client | `47a210cd-d4c9-425c-815a-d4cd269d722a` |
| Blueprint principal | `9f53b204-0a38-4ca9-b91d-3783123646db` |
| Blueprint client | `24caa4d2-df05-404b-b0f4-a75ae7322d6a` |
| Blueprint reference | `step-11-agent365-teams-d739d` / `ManagedAgentIdentityBlueprint` |
| Bot Service relay | `bot-step11-agent365-673af34` |
| Teams channel | `MsTeamsChannel` |
| Toolbox | `step-11-contoso-support`, default v2 |
| RemoteTool connection | `step-11-support-policy-mcp` / `AgenticIdentityToken` |
| Foundry memory store | `step-11-agent365-teams` |

The active version advertises `responses@2.0.0`, `invocations@2.0.0`, and
`activity_protocol@v1`. Its endpoint exposes `responses`, `invocations`,
managed incoming `a2a`, and `activity` with `Entra` and `BotServiceRbac`. The
relay endpoint is the native Foundry Activity URL; no separately deployed Bot
Framework application exists.

## Agent 365 publication and registry

- Foundry account `a365LoggingEnabled`: `true`.
- Foundry account `a365Status`: `Enabled`.
- Autopilot publish disposition: submitted.
- Submitted title ID: `T_ae0abc46-8730-8012-81c2-86837ea1df3e`.
- Publish scope: tenant.
- Publication metadata source: `agent365.toml`.
- Admin approval URL:
  `https://admin.cloud.microsoft/?#/agents/all/requested`.

Registry verification was attempted through Azure CLI and Microsoft 365 CLI.
Both returned 403. A direct scoped token request failed with `AADSTS65002`
because Azure CLI's first-party app is not preauthorized by Microsoft Graph for
`CopilotPackages.Read.All`. The Package Management API also requires an Agent
365 license. This is an authorization boundary, not negative evidence that the
submitted title is absent.

Teams Developer Portal backend configuration was attempted with the existing
authenticated tenant session and returned 403 before admin activation.

After blueprint approval:

- `tomas@tomasonline.net` and `admin@tomasonline.net` were verified as
  blueprint application owners.
- `tomas@tomasonline.net` received the Agent ID Developer directory role.
- The blueprint received the least-privilege Messaging Bot API delegated grant
  `AgentData.ReadWrite`:
  `BLJTnzgKqUy5HTeDEjZG2y2xswkqNrVAmA_tG0WigWo`.
- Developer Portal was configured manually as Bot-based with blueprint client
  ID `24caa4d2-df05-404b-b0f4-a75ae7322d6a`.
- The tenant has four consumed `AGENT_365` subscriptions. Tomas has
  `AGENT_365`, Microsoft 365 Copilot, and Teams Enterprise service plans.

Teams-admin assignment and normal **Add** installation were tested four times.
Each Activity reached Hosted Agent v7, but carried
`activity.is_agentic_request=false`. The latest operation
`9d5579869e047c1191a18aed673bd062` failed outbound Connector authorization.
This proves the normal organization app is not an Agent 365 instance and must
not be used as the Teams completion test.

## Identity and observability

The Hosted Agent instance has:

```text
resource app: 9b975845-388f-4429-889e-eab1ef63949c
resource SP:  041fba88-754d-4774-87c5-ccba60b7ecd9
role:         Agent365.Observability.OtelWrite
role ID:      8f71190c-00c8-461d-a63b-f74abde9ba52
assignment:   zRCiR8nUXEKBWtTNJp1yKkgv8XskBhdIvTTKOoq60Fs
```

Application Insights captured the reviewed v7 pre-approval deployment:

```text
2026-07-17T05:20:49.931520Z telemetry.agent365_configured
2026-07-17T05:20:50.311570Z hosted session
  s-d6310eda-8228-4b6d-b978-7d91ac199967
2026-07-17T05:20:53.089957Z toolbox validation operation
  76fd8168358152851c902fb8bf5fd0a4
```

This proves SDK configuration and role wiring. The corrected runtime enables the
exporter and uses the documented agentic S2S exchange. No Agent 365 Activity
trace is claimed because a Teams instance cannot invoke Activity before
approval.

## Behavior and evaluation

- Final deployed Responses smoke: passed.
- Toolbox v2 policy and skill canaries: passed.
- Deployed A2A: `deployed a2a ok`.
- Deployed Invocations: `deployed invocations ok`; trace
  `f141d93448b7d2453c27dcec1abb913e`.
- Cross-session memory: `silver-prism-523e39e2` with a 75-second propagation
  window.
- Anonymous Activity endpoint request: 401 `PermissionDenied`, as required.
- Model-judged evaluation:

```text
Eval:    eval_ac49b4deb3cf4573b8d49027e9de34f1
Run:     evalrun_22b33060148d4f55935d21822fc44142
Agent:   step-11-agent365-teams v7
Results: 15 total, 15 passed, 0 failed, 0 errored
```

Report:
`https://ai.azure.com/nextgen/r/ZzrzTWsoQdy8e_UHQYBF5g,ai-services,,tomaskubica-foundry-resource,tomaskubica-foundry-project/build/evaluations/eval_ac49b4deb3cf4573b8d49027e9de34f1/run/evalrun_22b33060148d4f55935d21822fc44142`

## Cleanup

Bootstrap/evaluation versions 1-6 were deleted after their sessions were
deleted through the Hosted Agent session API. V7 is the only retained agent
version. ACR retains only:

```text
step11-support-policy:build-20260717021734
step-11-agent365-teams/...:azd-deploy-1784265095
```

## Exact remaining gate

Title `T_ae0abc46-8730-8012-81c2-86837ea1df3e` is approved and active. The
user must next select **Request instance** from the **Agents for your team**
category, an admin must approve that separate instance request, and the user
must select **Create instance**. No supported Agent 365 CLI or Graph operation
performs these Teams onboarding actions. Run real 1:1 and channel-mention
acceptance only against the resulting agent user. Until those actions
complete, step 11 is intentionally not marked complete and the root progressive
plan/readme are unchanged.
