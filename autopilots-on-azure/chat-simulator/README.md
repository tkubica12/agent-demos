# Agent Chat Simulator

A standalone, neutral collaboration UI for personal/group chats, workspaces and
channels, threaded replies, typing, reactions, Adaptive Cards and shared files.
Agent-authored `extendedmarkdown` messages also support the same self-contained
HTML widgets used by the Hermes `teams-html-widget` skill in real Teams.
It **emulates the Teams channel protocol for testing agents built with the
Microsoft 365 Agents SDK**; it does not connect to real Teams or Microsoft Graph
and is not a Microsoft product. Protocol identifiers such as `msteams`, `28:`,
Activity schemas and Connector `/v3` routes intentionally remain unchanged.

Python 3.12 / FastAPI / SQLAlchemy / SQLite; React / TypeScript / Vite.
The repository contains no deployed configuration, tenant identity or credentials.
Upstream sample licenses and notices remain in `agents\reference-echo` and
`frontend\THIRD-PARTY-NOTICES.txt`.

For Azure deployment with a real Hermes Worker, see the
[deployment guide and current validation gaps](../docs/simulator-deployment.md#current-validation-status).
Local protocol/widget tests do not establish deployed model or browser acceptance.

## Run and build

[SETUP.md](SETUP.md) covers the default single-tenant **Entra** sign-in, application
roles, managed-identity channel authentication, local development and checks.
Run all commands from this directory. The default empty configuration fails
closed: configure your own Entra registrations before starting.

```powershell
uv sync --locked --python 3.12
uv run --env-file .env --locked python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal:

```powershell
Set-Location frontend
npm ci --no-audit --no-fund
npm run build
```

Restart the backend after building to serve the SPA and API from one origin.
Set the redirect/origin to that actual address, for example
`TSIM_REDIRECT_URI=http://localhost:8000/` and
`TSIM_ALLOWED_ORIGINS=http://localhost:8000`. Health is `/api/health`.
For Vite development use `npm run dev`, port 5173 and the supplied example origins.

Build the single-origin, non-root container with **this directory as context**:

```powershell
docker build --tag agent-chat-simulator:local .
docker run --rm --publish 8000:8000 --env-file .env --mount type=volume,source=agent-chat-data,target=/data agent-chat-simulator:local
```

The multi-stage image builds the SPA on Node 24, restores Python 3.12 packages with
uv and the frozen lock, and listens on port 8000 as UID/GID 10001. Both dependency
feeds are explicitly Microsoft-approved. It defaults to `ENV=production`; HTTPS
must terminate at the hosting ingress, and explicit HTTPS redirect/origins/service
URL are required. For loopback-only container development override
`ENV=development`. Mount `/data` read/write for UID/GID 10001 (including files and
the demo signing key); mount any agent-registration file read-only. Use one
backend replica: local files and SQLite are not a scale-out database.

## Declarative agents

Set **one** of `TSIM_AGENTS_JSON` or `TSIM_AGENTS_FILE`. The latter is a UTF-8 JSON
file. Startup validates the complete array before making transactional upserts.
`name` is the stable deployment key; `clientId` must also be unique. Existing
manually registered agents can be adopted by client ID. Repeated startup preserves
agent/user IDs and conversation membership, updates the display name, endpoint
and key, and enables each declared agent. Omitted agents are not deleted.

```json
[
  {
    "name": "agent-1",
    "displayName": "agent-1",
    "clientId": "20000000-0000-4000-8000-000000000001",
    "messagingEndpoint": "https://bridge.example.test/api/messages"
  }
]
```

Use your own canonical, nonzero client UUIDs, distinct from the browser/channel
apps. Names match `[a-z0-9][a-z0-9-]{0,62}`; display names contain 1-200 characters.
Unknown fields, duplicate names/client IDs/keys, ambiguous sources and insecure
endpoints fail startup. HTTPS endpoints are required; HTTP is allowed only for
explicit loopback development. The six-stack deployment uses six independently
configured simulators, each with exactly one registration (`agent-1` through
`agent-6`) and its own database, access code, client ID and bridge endpoint.
Multiple registrations remain supported for other testing scenarios.

Participants discover agents in the directory and add them to a chat or workspace.
No fake replies or runtime substitute is bundled. The bridge/hosted agent must
implement the real response flow; delivery errors remain visible.

## HTML widgets

Only agent `message` activities with `textFormat: extendedmarkdown` execute valid
`html-widget` fences. Ordinary markdown, human messages and invalid fences remain
text/code. Surrounding markdown is preserved. The Hermes skill, widget bootstrap,
payload and 30-minute Worker/conversation-bound Fernet capabilities are unchanged.
The host implements MCP UI initialization (`2026-01-26`), initialized/size
notifications and `autopilot.widget.submit`. It sends the real
`htmlwidget/calltool` invoke to the message's agent with the current user identity,
same conversation and host-overwritten `__msteams__`. Synchronous
`htmlwidget/calltoolresult` acknowledgments and proactive follow-up messages stay
in that chat. The bridge handles capability expiry and idempotence.

Widgets load on demand near the viewport. Snapshot refreshes and new messages
do not reload mounted frames or discard inputs. Initialization failures offer a
retry; leaving/reopening a conversation creates a fresh widget view.
Messages are bounded to 100,000 UTF-8 bytes (ordinary text keeps its 28,000-character
limit), tool arguments to 16 KiB, results to 64 KiB, height to 32-4096 px, tool
delivery to 15 seconds, and calls to one in flight plus six per minute per widget
and 30 per minute per user. Timeouts explicitly report an unknown gateway outcome;
retry the same selection, not a new capability.

An authenticated API mints a single-use, 60-second HMAC frame capability bound to
the stored message/widget/user/HTML hash. The frame rechecks membership and content;
direct, cross-origin, expired, replayed and tampered URL requests fail closed.
Only that route has its own HTTP CSP/ancestry policy. The iframe has **only**
`sandbox="allow-scripts"`: no same-origin access, top navigation, popups, forms or
downloads. HTTP CSP allows inline scripts/styles and data images but denies
fetch/resources/nested frames even if a payload requests broader `securityPolicy`
domains; DNS prefetch is disabled. The parent CSP allowlists the simulator and
Entra login frame origins; the host closes a widget on subsequent navigation. Window messages
are accepted only from that widget's `contentWindow`.

This is a narrow self-contained host, not a full MCP Apps client: external MCP
URLs are metadata, not network destinations; model-context updates, permissions,
fullscreen and open-link requests are not implemented. Unknown requests get
JSON-RPC errors; unsupported notifications are ignored. Browser CSP/sandbox is
not an OS-level network sandbox (for example WebRTC is not covered by `connect-src`).
Do not use it as a general arbitrary-code execution environment.

No new deployment settings, identities, resources or ports are required. Use the
existing single backend process/replica. Replace the simulator image for host
changes and the gateway/runtime source releases for the widget implementation.
The separately pinned blueprint RoleCommit does not change.

From the parent `autopilots-on-azure` directory, the local real-SDK widget smoke
and Chromium UI/security checks are:

```powershell
uv run python tests\simulator_channel_smoke.py --simulator-root chat-simulator --widgets-browser
uv run python tests\simulator_channel_smoke.py --simulator-root chat-simulator --mode demo --widgets-browser
```

These use the real gateway, Activity model, Connector and SDK HTTP invoke adapter;
only Hermes/model responses, native claim storage and explicit failing transports
are fixtures. They use isolated local identities, never Azure. Add `--media-dir`
with an external artifact directory to record a Playwright screenshot-sequence
demo (GIF/MP4 via installed ffmpeg). Installed Chromium-based Edge is used.
Run simulator API tests with `uv run python -m pytest -q`; from `frontend`,
run `npm test` and `npm run build`. Existing browser runners remain unchanged.

## Explicit, expiring demo mode

**Test data only. Anyone with the code can claim any display name. This is not
authentication and must never protect real business data.** Entra remains the
default. Demo requires all of the following explicit settings:

```text
TSIM_AUTH_MODE=demo
TSIM_DEMO_ACCESS_CODE=<random access code of at least 20 characters>
TSIM_DEMO_EXPIRES_AT=<future RFC3339 UTC timestamp, at most 30 days ahead>
TSIM_DEMO_ACKNOWLEDGE=test-data-only
```

The acknowledgement is additionally mandatory with `ENV=production`. Demo refuses
a test IdP, channel secret or managed-identity channel credentials. Missing,
expired or invalid settings fail startup. On expiry login, collaboration APIs,
file capabilities, Connector calls and new/existing WebSockets refuse access.
`/api/config` also returns **410**, retaining only its normal public configuration
so the expired login screen can still render the warning. Static SPA assets stay
available. Health returns 410 after expiry.

Every screen has a persistent red warning:
**DEMO MODE - no real authentication - test data only - expires &lt;date&gt;**.
The UI asks for access code and display name. Login attempts are limited to
5/minute per direct client address and 100/minute per process overall (including
successful attempts). Behind an ingress, shared source addresses can share that
limit; no untrusted forwarded header is used. Deploy one replica.

Sessions are HS256-signed, expire after at most one hour and never outlive the demo.
The random 32-byte signing key is stored at `TSIM_STORAGE_PATH\..\demo-session.key`;
persist/protect that file and never publish it. Demo users have `User`, not `Admin`,
privileges. There is no demo agent-admin UI: register agents declaratively.

The stable `from.aadObjectId` is UUIDv5 using namespace
`51f8c9cd-dcd4-4c33-9ef6-48b4c5919185` and **trimmed, lower-cased display name**:

```powershell
uv run --locked python -c "from uuid import UUID,uuid5; print(uuid5(UUID('51f8c9cd-dcd4-4c33-9ef6-48b4c5919185'), 'Alice'.strip().lower()))"
```

Use that UUID for a demo agent's owner setting. It is reproducible and spoofable
by anyone with the access code; never treat it as an Entra identity. A dedicated
database/volume is required when switching auth mode or tenants.

Each demo registration additionally requires a distinct `demoKey`: an unpadded,
canonical URL-safe base64 encoding of **at least 32 cryptographically random
bytes** (43-128 characters). Generate keys locally, store them as secrets, and do
not commit the resulting registration JSON:

```powershell
uv run --locked python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Delivery to the agent omits `Authorization`, sends
`X-Demo-Channel-Key: <agent demoKey>`, and sets activity
`serviceUrl={TSIM_SERVICE_URL}/demo/{demoKey}`. The bridge must explicitly opt into
the same demo mode, validate its key and honor expiry. Connector endpoints are
available under that URL's `/v3/...` without a bearer token; constant-time key
comparison resolves an enabled agent and normal membership rules still apply.
Plain `/v3/...` never accepts demo session tokens or anonymous access: it retains
the Entra application-token requirement. Demo routes do not exist in Entra mode.
Treat service URLs as credentials; disable/redact ingress access logs for them.

In either mode, inbound files use single-file 15-minute capability URLs at
`{TSIM_SERVICE_URL}/api/agent-files/{id}/content?capability=...` without bearer.
Capabilities still check agent enablement/membership and are refused after demo
expiry. Do not log capability query strings.

## Environment contract

| Variable | Default / requirement |
| --- | --- |
| `ENV` | `development`; image defaults to `production` |
| `TSIM_AUTH_MODE` | `entra`; only `entra` or explicit `demo` |
| `TSIM_TENANT_ID` | Required canonical tenant UUID in Entra; fixed synthetic namespace if omitted in demo |
| `TSIM_CLIENT_ID` | Required public SPA/API UUID in Entra |
| `TSIM_SCOPE` | `api://<TSIM_CLIENT_ID>/access_as_user` in Entra |
| `TSIM_REDIRECT_URI` | `http://localhost:5173/`; set to actual SPA redirect |
| `TSIM_ALLOWED_ORIGINS` | `http://localhost:5173` in development; required comma-separated HTTPS origins in production |
| `TSIM_CHANNEL_ID` | `msteams`; retain for protocol compatibility |
| `TSIM_CHANNEL_CLIENT_ID` | Separate confidential channel application UUID for Entra agent integration |
| `TSIM_CHANNEL_MANAGED_IDENTITY_CLIENT_ID` | UAMI client UUID for channel app federated assertion; preferred secretless Entra delivery |
| `TSIM_CHANNEL_CLIENT_SECRET` | Legacy optional Entra secret alternative; never combine with managed identity, never use in demo |
| `TSIM_SERVICE_URL` | `http://127.0.0.1:5173`; set to externally reachable simulator origin, HTTPS in production |
| `TSIM_DATABASE_URL` | SQLite only; local `data/simulator.db`, image `sqlite:////data/simulator.db` |
| `TSIM_STORAGE_PATH` | Local `data/files`; image `/data/files` |
| `TSIM_AGENTS_JSON` | Optional registration array; secret in demo |
| `TSIM_AGENTS_FILE` | Alternative registration JSON file path |
| `TSIM_DEMO_ACCESS_CODE` | Required >=20 characters only for demo; generate randomly |
| `TSIM_DEMO_EXPIRES_AT` | Required UTC RFC3339 demo deadline: future, <=30 days |
| `TSIM_DEMO_ACKNOWLEDGE` | Exact `test-data-only` mandatory for production demo |
| `TSIM_TEST_IDP` | `0`; `1` enables synthetic loopback tests only, forbidden in production/demo |
| `TSIM_TEST_ISSUER` | `http://127.0.0.1:8001`; loopback test issuer only |

Entra channel delivery requests `<agent clientId>/.default` using the channel app;
Connector tokens target `TSIM_CHANNEL_CLIENT_ID` and require `azp=agent clientId`.
`from.aadObjectId` is the signed Entra user's object ID in default mode.

## Checks and boundaries

```powershell
uv run --locked python -m pytest -q
Set-Location frontend
npm test
npm run build
Set-Location ..
node tools\run_browser_tests.mjs
node tools\run_demo_browser_tests.mjs
```

The isolated four-persona browser suite exercises collaboration, files, reconnect,
restart/persistence and protocol exports. Backend tests cover demo expiry,
signed sessions, rate limits, deterministic identities, strict/idempotent seeding,
per-agent delivery/Connector auth, typing, cards, reactions and file capabilities.
These synthetic checks are not a deployed Entra/managed-identity or Hermes test.
After deployment verify real login, owner identity, six agent replies, cards/files
and expiry through the actual hosting ingress; see [SETUP.md](SETUP.md).
MCP business integrations and multi-replica hosting are outside this demo's scope.

The demo browser runner starts its own active and expiring loopback instances on
ports 8041/8042, generates a process-only access code, waits for real expiry, and
checks access-code login, the persistent banner and expiry refusal. Override
`TSIM_DEMO_E2E_PORT` / `TSIM_DEMO_E2E_EXPIRING_PORT` if needed. It never bypasses
startup expiry validation. Evidence and isolated test databases remain ignored.
