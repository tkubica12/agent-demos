# Step 10 deployment evidence

Captured 2026-07-17. IDs below are operational identifiers, not credentials.
Tokens and authorization headers are omitted.

## Resources

| Item | Value |
| --- | --- |
| Resource group | `rg-step10-toolbox-673af34` |
| MCP Container App | `ca-step10-policy-673af34-eastus2` |
| MCP image | `acrstep10673af34.azurecr.io/step10-support-policy:jwt-20260716234931` |
| MCP API client | `7aca035b-d2bc-42b8-912c-afa1b6eb9d00` |
| MCP API role | `Mcp.Tools.ReadWrite.All` / `471715d9-3e5b-4756-89d2-2e2bf58aed7e` |
| Hosted Agent | `step-10-foundry-toolbox` v24, active |
| Agent instance | `cd6dfe0f-8f90-4bff-a1b3-55934d8e6e7d` |
| Agent blueprint client | `384b4cc3-e7d6-4164-a361-6544826588f0` |
| RemoteTool connection | `step-10-support-policy-mcp` / `AgenticIdentityToken` |
| Toolbox | `step-10-contoso-support`, default v2 |
| Toolbox v1 skill | `step-10-escalation-policy@1` / `ESC-CANARY-7742` |
| Toolbox v2 skill | `step-10-escalation-policy@2` / `ESC-CANARY-9921` |

Only the agent instance retains the MCP API app-role assignment. Blueprint
authorization was tested, found unnecessary, and removed.

## Authentication evidence

- HTTPS-only ingress; `/health` returns 200.
- ACA Easy Auth is disabled after exhaustive incompatible-token isolation.
- FastMCP verifies Entra tokens from tenant JWKS with a one-hour rotating-key
  cache and RS256 allowlist.
- Exact issuer:
  `https://login.microsoftonline.com/6ce4f237-667f-43f5-aafd-cbef954adf97/v2.0`.
- Exact audience: `7aca035b-d2bc-42b8-912c-afa1b6eb9d00`.
- Required role: `Mcp.Tools.ReadWrite.All`.
- Allowed caller: instance azp/oid
  `cd6dfe0f-8f90-4bff-a1b3-55934d8e6e7d`.
- No token: 401.
- Generated-key negative smokes cover expiry, wrong audience, wrong role, and
  wrong caller.
- Real Hosted Agent token succeeds through Toolbox and the upstream MCP.

The rejected native Easy Auth token had matching `aud`, `azp`, issuer, v2 token
version, and role. Removing `allowedApplications`, configuring both audience
forms, matching live OIDC metadata, and calling `/.auth/me` all still produced
401. Application JWT validation is the smallest compatible real workaround.

## Toolbox evidence

- Candidate endpoint:
  `/toolboxes/step-10-contoso-support/versions/2/mcp?api-version=v1`.
- Consumer endpoint:
  `/toolboxes/step-10-contoso-support/mcp?api-version=v1`.
- `tool_search("lookup_support_policy")` returned the opaque name
  `contoso_support_policy___lookup_support_policy`.
- The agent invoked that exact returned name and received:
  `window_days=30`, `policy_id=RET-CANARY-9031`.
- `resources/list` returned the pinned skill index and `SKILL.md`.
- `resources/read` of the index selected the v2 immutable local package by exact
  description; v2 behavior returned
  `specialist-review ESC-CANARY-9921`.
- Neutral prompt returned `4` without loading unrelated skills.
- No write business tool is present.

## Promotion and rollback

1. V2 candidate accepted before publication.
2. V2 explicitly published.
3. Final v24 consumer accepted v2 without redeploy.
4. Full deployed smoke passed Responses, skills, policy tool, unrelated-skill
   exclusion, and cross-session memory.
5. V1 explicitly republished.
6. Versioned v1 resource returned only `ESC-CANARY-7742`.
7. V2 explicitly republished and the consumer returned `ESC-CANARY-9921`.

Final default is v2. Failed earlier publication attempts automatically restored
v1, demonstrating the failure path as well.

## Evaluation

```text
Eval:    eval_351861a960254ee898a684417f3d6133
Run:     evalrun_ebe9ebe95298458db1958edf58d5cc41
Agent:   step-10-foundry-toolbox v24
Results: 15 total, 15 passed, 0 failed, 0 errored
```

Report:
`https://ai.azure.com/nextgen/r/ZzrzTWsoQdy8e_UHQYBF5g,ai-services,,tomaskubica-foundry-resource,tomaskubica-foundry-project/build/evaluations/eval_351861a960254ee898a684417f3d6133/run/evalrun_ebe9ebe95298458db1958edf58d5cc41`

## Cleanup

Superseded Hosted Agent revisions 1-23 and the superseded API application were
deleted. V24 is the only retained Hosted Agent revision.
