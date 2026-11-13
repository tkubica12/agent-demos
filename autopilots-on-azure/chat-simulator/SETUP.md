# Setup in your own tenant

## Register the browser and API

Use one **new single-tenant app registration** for this public SPA and its API.
This implementation intentionally requires the access-token audience and
authorized client (`azp`) to be the same application ID; do not substitute a
separate SPA client or a Microsoft Graph token.

1. In Microsoft Entra admin center, create an app registration with **Accounts
   in this organizational directory only** (`AzureADMyOrg`). Record its Directory
   (tenant) ID and Application (client) ID. Do not create a browser client secret.
2. Under Authentication, add a **Single-page application** platform and the
   exact redirect `http://localhost:5173/`. Use authorization code flow with PKCE;
   leave implicit grant and public-client fallback flows disabled.
3. Under Expose an API, use `api://<application-client-id>` as the Application
   ID URI. Add the enabled delegated scope `access_as_user`, with administrator
   consent. Add that delegated permission to this SPA for its own API and grant
   tenant administrator consent. Set `api.requestedAccessTokenVersion` to `2` in
   the application manifest so access tokens use the expected v2 issuer/audience.
4. Define enabled app roles with values **User** and **Admin**, both allowing
   **Users/Groups**. Under Enterprise applications, open this app's service
   principal, set **Assignment required? = Yes**, and assign participants to
   User and at least one operator to Admin. Admin is accepted independently of
   User. Application permissions, directory roles and merely having a tenant
   account do not grant access.

The backend validates RSA signatures, issuer, tenant, audience, authorized client,
expiry, delegated `access_as_user` and the User/Admin role. Unassigned or
wrong-tenant users cannot collaborate. Users appear after their first successful
sign-in. Admins can also preregister a display name/UPN through `/api/users`, but
that does not create an Entra user, assign a role or bypass token checks. Team
ownership/membership and chat membership remain separate authorization boundaries.

## Run locally (PowerShell)

Prerequisites: Python 3.12, uv, Node.js 24+ and npm. Run from `chat-simulator`.
The Python index and frontend npm configuration use the approved Microsoft feeds;
keep both lockfiles and restore rather than upgrading dependencies.

```powershell
Copy-Item .env.example .env
# Edit .env: replace tenant/client/scope placeholders with the new registration.
uv sync --locked --python 3.12
uv run --env-file .env --locked python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

In another terminal:

```powershell
Set-Location frontend
npm ci --no-audit --no-fund
npm run dev
```

Open `http://localhost:5173/` and sign in. The Vite proxy forwards API/WebSocket
traffic to port 8000. `.env` is loaded explicitly by uv, not by the backend.
Missing or placeholder authentication IDs cause startup to fail, not a fallback
to another tenant or a synthetic identity. Environment variables override the
optional ignored `frontend\public\auth-config.json` (public SPA metadata only).
There is deliberately no checked-in live auth configuration.

SQLite and uploaded files default to ignored `data\simulator.db` and `data\files`.
To move them, set `TSIM_DATABASE_URL` and `TSIM_STORAGE_PATH`. Do not reuse a
database across tenants. Back up the database and files together.

## Build and hosting boundary

```powershell
Set-Location frontend
npm run build
```

The backend serves the generated `frontend\dist` at `/` when present. For
single-origin use, register that exact origin's SPA redirect, update
`TSIM_REDIRECT_URI` and `TSIM_ALLOWED_ORIGINS`, and restart the backend. A local
built-app example is `http://localhost:8000/` for the redirect and
`http://localhost:8000` for the allowed origin.

No hosting resources or deployment credentials are included or created here.
For a deployment, terminate HTTPS/WebSockets, use `ENV=production`, set an
explicit HTTPS redirect and explicit HTTPS `TSIM_ALLOWED_ORIGINS`, and keep
`TSIM_TEST_IDP=0`. Production rejects a test IdP, loopback/non-HTTPS redirects,
and missing/non-HTTPS allowed origins. Keep database/files on persistent storage;
this SQLite/local-file implementation is intended for a single backend instance,
not independent scaled-out replicas. The health/config endpoints are public
metadata, but collaboration APIs and WebSocket sessions require authentication.

## Optional agents and cards

People collaboration needs no bot registration. Adaptive Card rendering and the
existing Activity/Connector endpoints emulate a channel protocol for testing
Microsoft 365 Agents SDK agents, not a connection to a hosted collaboration service.

For agents, configure a **separate confidential channel application** in the
same tenant using `TSIM_CHANNEL_CLIENT_ID`, a secret provided securely through
`TSIM_CHANNEL_CLIENT_SECRET` **or** a federated managed identity selected by
`TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID`, and the backend origin in
`TSIM_SERVICE_URL`. Never use the public SPA's ID/secret for this channel.
Both channel and agent APIs need v2 application tokens and corresponding
client-credentials permissions/admin consent; outbound delivery requests
`<agent-client-id>/.default`, and inbound Connector calls target the channel ID.
Your agent host must validate the tenant, audience, channel caller and signatures.
The backend validates registered agent callers and conversation membership.

An Admin registers each agent's name, app ID and HTTPS messages endpoint through
the UI or `/api/agents`; participants then add the registered agent to a chat or
team. No outbound delivery is possible without channel credentials; failures
remain visible rather than silently pretending the agent replied. The reference
echo under `agents\reference-echo` is licensed sample code, not a configured
deployment. API details are available at `/docs` after starting the backend.

## Local checks

```powershell
uv run --locked python -m pytest -q
Set-Location frontend
npm test
npm run build
Set-Location ..
node tools\run_browser_tests.mjs
```

The browser runner uses an already-installed Edge by default; set
`TSIM_E2E_BROWSER=chrome` to use an installed Chrome. It requires the built
frontend and restored dependencies. It starts only owned loopback helpers on
ports 8011/8012/8013 (overridable via `TSIM_E2E_BACKEND_PORT`,
`TSIM_E2E_IDP_PORT`, `TSIM_E2E_CONTROL_PORT`), uses a fresh isolated database and
four explicitly synthetic personas, and stops its helpers afterward.
It covers live membership, personal/group chats, replies/reactions, reconnection
and persistence, files, inert input and Activity export validation.
Results stay in ignored `test-results`/`.cache`; an optional local clip needs an
already-installed ffmpeg.

These are synthetic local checks, **not verification of your tenant's sign-in**.
After configuration, verify real sign-in for assigned User/Admin accounts, deny
an unassigned account and a foreign-tenant account, and confirm two assigned
people can chat/share files. Never publish real login caches, bearer tokens,
browser profiles, databases or uploads.
