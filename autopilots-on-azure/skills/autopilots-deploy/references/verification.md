# Evidence and acceptance

Keep evidence private, redacted and attached to the actual stack, UTC time,
source commit, images and URL. Do not ship it as reusable skill content.

| Check | What it proves | What it does not prove |
| --- | --- | --- |
| Offline tests / `plan` | Input/schema consistency and expected deployment shape | Azure provisioning, DNS, login or product quality |
| `all` readiness | Authenticated Sandbox-management loopback health and explicit never-suspend/delete policies | Private client TLS/DNS, real user login, browser chat or files |
| `verify` basic checks | Simulator/gateway health over client HTTPS and `/api/me` rejects anonymous access with 401 | Complete application authentication or browser behavior |
| Demo `verify` chat smoke | Actual scripted demo chat checks reported by the smoke against deployed services | Actual identity, browser widget/upload/download acceptance |
| Entra `verify` receipt | Fresh manually recorded assigned-user browser checks are present | Automated execution of those checks or independently trustworthy operator assertions |

For Entra, the assigned user must use a browser on the private network with
normal TLS validation. Exercise login, real reply, typing, reactions, cards,
group chat and file upload/download; also check unassigned-user denial, logout,
widget behavior and persistence across service restart. Never export tokens.
Only after observed success create
`<state>\simulator-<environment>-<stack>\browser-acceptance.json`:

```json
{
  "schemaVersion": 1,
  "environment": "<environment>-<stack>",
  "url": "https://<actual-private-simulator-host>",
  "authMode": "entra",
  "verifiedAt": "<actual-UTC-time>",
  "checks": {
    "login": true, "messageReply": true, "typing": true,
    "reactions": true, "cards": true, "groupChat": true, "files": true
  }
}
```

The deployer requires the correct stack/URL/auth mode and a timestamp within
seven days. Clear stale current receipts after mode/release changes. Record
widget/TLS/denied-user/persistence evidence separately because the receipt's
fixed fields do not independently assert them. Demo browser acceptance also
remains a separate manual check even if the scripted smoke passes.

HTTP 409, missing real reply, inconclusive upload/download or failed normal-TLS
private access are rollout holds, not "known good with warnings". Source tests
do not clear these gates for deployed images.
