---
name: generated-web-app
description: Build, test, publish, update, list, and delete rich team web applications in isolated Entra-gated child ACA Sandboxes.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    category: development
    tags: [web app, ACA Sandbox, Entra, deployment]
---

# Generated team web applications

Use this skill when the user wants a rich application, dashboard,
presentation, visualization, or multi-step experience that is too flexible for
an Adaptive Card.

This policy is adapted to Autopilots on Azure from Microsoft's MIT
`aca-sandboxes` skill. The fixed deployment tools own Azure credentials,
Sandbox policy, Entra sharing, quotas, and cleanup. Hermes owns requirements,
source code, tests, and iteration.

## Required information

Before deployment, obtain:

- the app purpose and acceptance criteria;
- explicit participant email addresses;
- whether outbound network access is needed and the exact host allowlist.

Never infer participants from a group conversation. Never deploy anonymously.
Resolve the invoking user's profile with `get_user_profile`, pass their mail or
UPN as `requesting_user_email`, include it in `participant_emails`, and pass the
authenticated `invokingUserId` as `requesting_user_id` to every generated-app
tool. Never substitute another user identifier.

## Build boundary

Create each app under:

```text
/data/hermes/workspace/generated-apps/<app-name>/
```

Keep source inspectable and self-contained. Do not put credentials, tokens,
tenant IDs, private document content, or environment-specific secrets in source.
Do not open arbitrary callback URLs or proxy user credentials.

Use Python or Node.js. Include:

- one clear start command;
- a `GET /health` endpoint that returns HTTP 200 only when the app is ready;
- useful error handling;
- responsive and accessible HTML;
- tests for meaningful behavior;
- dependency locks when dependencies are introduced.

Prefer no dependencies for small presentations and dashboards. When
dependencies are needed, use a deterministic install command.
The server must bind to `0.0.0.0`, not loopback, so the authenticated Sandbox
proxy can reach it.

## Validate before deployment

Before work likely to exceed one minute, send one concise progress message to
the invoking user through Work IQ Teams: state that the app is being built,
tested, and deployed, and that a final link will follow. Send at most two later
milestone updates and no more than one per minute. Never expose code, tool
arguments, credentials, or chain-of-thought in progress updates.

Run the smallest useful tests locally in the Worker Sandbox. Inspect generated
HTML and JavaScript for:

- unsafe string interpolation or unescaped user data;
- external scripts or network calls not present in the requested egress list;
- embedded secrets;
- inaccessible controls or missing labels;
- missing mobile layout;
- hidden consequential actions.

Do not report success until tests pass.

## Deploy

Call `deploy_generated_web_app` with:

- the local directory;
- a human-readable name;
- every participant email;
- runtime `python` or `node`;
- one HTTP port;
- argument arrays for install, test, and start commands;
- a lifetime only when the user explicitly overrides the 24-hour default;
- only the required egress hosts.

The tool creates a child Sandbox, applies deny-default egress, runs install and
tests, starts the app, enables suspension after five idle minutes and native
automatic deletion 24 hours after suspension by default, and exposes an
Entra-authenticated URL to the selected participants.

After a successful deploy or update, return concise visible text followed by:

```text
<GENERATED_APP_CARD_REQUEST>{"mode":"app","appId":"<returned-app-id>"}</GENERATED_APP_CARD_REQUEST>
```

The bridge replaces this marker with the governed site card containing the URL,
retention choices, and delete action. Do not author those actions yourself.

## Update and cleanup

Keep the returned `appId` in the current Work History only while the app is
active. To update, modify and retest the same source, then deploy with that
`appId`; the old child Sandbox is replaced.

Use `list_generated_web_apps` with `invokingUserId` to inspect only that user's
active apps. When the list is non-empty, return concise visible text followed by:

```text
<GENERATED_APP_CARD_REQUEST>{"mode":"list"}</GENERATED_APP_CARD_REQUEST>
```

The bridge resolves the current authenticated user's live inventory and renders
the governed cards. For an empty list, answer plainly. Use
`delete_generated_web_app` immediately when the user is finished. Never claim
that deleting source files deletes the deployed Sandbox.

For a natural-language retention change, use `renew_generated_web_app` with one
of 3600, 21600, 86400, or 259200 seconds. Card buttons call the same native ACA
lifecycle operation directly through the bridge; they do not start a model turn
or create a Service Bus schedule.

An ephemeral generated app is not a maintained production service. Promotion
requires human source review and deployment to standard ACA with durable
identity, operations, observability, and ownership.
