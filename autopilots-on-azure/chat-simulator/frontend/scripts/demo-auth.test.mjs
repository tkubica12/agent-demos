import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

function moduleUrl(path, imports = {}) {
  let source = ts.transpileModule(readFileSync(new URL(path, import.meta.url), "utf8"), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  }).outputText;
  for (const [name, url] of Object.entries(imports)) source = source.replaceAll(`"${name}"`, JSON.stringify(url));
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}
const apiUrl = moduleUrl("../src/api.ts");
const demoUrl = moduleUrl("../src/auth/demo.ts");
const sessionUrl = moduleUrl("../src/auth/session.ts", {
  "../api": apiUrl, "./demo": demoUrl, "./accounts": moduleUrl("../src/auth/accounts.ts"),
  "@azure/msal-browser": "data:text/javascript,throw new Error('MSAL must never load in demo mode')",
});
const { initializeAuth } = await import(sessionUrl);
const { DemoSession, demoSocketRefusal, expiryTime, DEMO_EXPIRED, DEMO_SESSION_EXPIRED, DEMO_REFUSED } = await import(demoUrl);
const user = { id: "demo-user", displayName: "Demo visitor", upn: "", roles: ["User"] };
const future = (ms = 60_000) => new Date(Date.now() + ms).toISOString();
const config = (expiresAt = future()) => ({
  authMode: "demo", channelId: "msteams",
  demo: { expiresAt, banner: `DEMO MODE – no real authentication – test data only – expires ${expiresAt}` },
});
const json = (value, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { "Content-Type": "application/json" },
});
function backend(t, settings = {}) {
  const calls = [];
  const settingsWithDefaults = {
    config: config(), token: { accessToken: "test-only-demo-token", expiresAt: Math.floor(Date.now() / 1000) + 60 },
    ...settings,
  };
  t.mock.method(globalThis, "fetch", async (path, options) => {
    calls.push({ path, options });
    if (path === "/api/config") return json(settingsWithDefaults.config, settings.configStatus ?? 200);
    if (path === "/api/demo/login") return json(settingsWithDefaults.token, settings.loginStatus ?? 200);
    if (path === "/api/me") return json(user);
    return json(settings.detail ?? { detail: "Not available" }, settings.status ?? 410);
  });
  return { calls, ...settingsWithDefaults };
}

test("demo initializes without tenant configuration, MSAL, cached identities or account login", async (t) => {
  const { calls, config: configured } = backend(t);
  let received;
  const auth = await initializeAuth((value) => { received = value; });
  assert.equal(auth.mode, "demo");
  assert.equal(auth.session, null);
  assert.equal("signIn" in auth, false);
  assert.equal("testSignIn" in auth, false);
  assert.deepEqual(received, configured);
  assert.deepEqual(calls.map((call) => call.path), ["/api/config"]);
});

test("demo sends only the login contract and uses the returned token via existing Bearer transport", async (t) => {
  const { calls, token } = backend(t);
  const session = await (await initializeAuth()).demoSignIn("test access code", " Demo visitor ");
  assert.equal(session.mode, "demo");
  assert.deepEqual(session.user, user);
  const login = calls.find((call) => call.path.endsWith("/login"));
  assert.equal(login.options.method, "POST");
  assert.deepEqual(JSON.parse(login.options.body), { accessCode: "test access code", displayName: "Demo visitor" });
  assert.deepEqual(login.options.headers, { "Content-Type": "application/json" });
  assert.equal(calls.find((call) => call.path.endsWith("/me")).options.headers.Authorization,
    `Bearer ${token.accessToken}`);
  assert.equal(await session.api.getToken(), token.accessToken);
});

test("a full HTTP 410 demo config retains the warning and refuses login even if the client clock lags", async (t) => {
  const { calls, config: configured } = backend(t, { configStatus: 410 });
  const auth = await initializeAuth();
  assert.deepEqual(auth.config.demo, configured.demo);
  assert.equal(auth.expired, true);
  await assert.rejects(auth.demoSignIn("fixture", "Visitor"), { message: DEMO_EXPIRED });
  assert.equal(calls.length, 1);
});

test("an expired HTTP 200 config also refuses login before making a request", async (t) => {
  const { calls } = backend(t, { config: config(future(-1000)) });
  await assert.rejects((await initializeAuth()).demoSignIn("fixture", "Visitor"), { message: DEMO_EXPIRED });
  assert.equal(calls.length, 1);
});

test("only HTTP 410 with demo metadata is decoded as an expired configuration", async (t) => {
  for (const [status, value] of [[500, config()], [410, { authMode: "entra" }]]) {
    await t.test(String(status), async (t) => {
      backend(t, { configStatus: status, config: value });
      await assert.rejects(initializeAuth(), new RegExp(`Backend configuration unavailable \\(${status}\\)`));
    });
  }
});

test("incorrect access code is an explicit refusal, with no identity API call", async (t) => {
  const { calls } = backend(t, { loginStatus: 401, token: { detail: "Invalid demo access code." } });
  await assert.rejects((await initializeAuth()).demoSignIn("incorrect", "Visitor"), /Invalid demo access code/);
  assert.equal(calls.some((call) => call.path === "/api/me"), false);
});

test("expiry during login is reported explicitly, including structured backend detail", async (t) => {
  backend(t, { loginStatus: 410, token: { detail: { code: "demo_expired", message: "Window closed." } } });
  await assert.rejects((await initializeAuth()).demoSignIn("fixture", "Visitor"), { message: DEMO_EXPIRED });
});

test("token expiration is Unix seconds and cannot extend the deployment deadline", async (t) => {
  const deploymentExpiry = future(30_000);
  backend(t, { config: config(deploymentExpiry) });
  const session = await (await initializeAuth()).demoSignIn("fixture", "Visitor");
  assert.equal(session.demo.expiresAt, deploymentExpiry);
});

test("expired session blocks all authenticated transports locally and notifies once", async (t) => {
  const { calls, token } = backend(t, { config: config(future(120_000)) });
  const session = await (await initializeAuth()).demoSignIn("fixture", "Visitor");
  const failures = [];
  session.demo.subscribe((message) => failures.push(message));
  t.mock.method(Date, "now", () => token.expiresAt * 1000);
  const before = calls.length;
  for (const operation of [
    () => session.api.getToken(),
    () => session.api.request("/users"),
    () => session.api.upload("conversation", new File(["test"], "test.txt")),
    () => session.api.download({ id: "file", name: "test.txt" }),
  ]) await assert.rejects(operation(), { message: DEMO_SESSION_EXPIRED });
  assert.equal(calls.length, before);
  assert.deepEqual(failures, [DEMO_SESSION_EXPIRED]);
});

test("backend expiry on requests, uploads and downloads invalidates the demo session", async (t) => {
  for (const operation of [
    (api) => api.request("/users"),
    (api) => api.upload("conversation", new File(["test"], "test.txt")),
    (api) => api.download({ id: "file", name: "test.txt" }),
  ]) {
    await t.test(operation.toString(), async (t) => {
      backend(t, { detail: { detail: { code: "demo_expired" } } });
      const session = await (await initializeAuth()).demoSignIn("fixture", "Visitor");
      const failures = [];
      session.demo.subscribe((message) => failures.push(message));
      await assert.rejects(operation(session.api), { message: DEMO_EXPIRED });
      await assert.rejects(session.api.getToken(), { message: DEMO_EXPIRED });
      assert.deepEqual(failures, [DEMO_EXPIRED]);
    });
  }
});

test("ordinary authorization denial does not revoke a valid demo session", async (t) => {
  backend(t, { status: 403, detail: { detail: "Owner permission required." } });
  const session = await (await initializeAuth()).demoSignIn("fixture", "Visitor");
  let refused = false;
  session.demo.subscribe(() => { refused = true; });
  await assert.rejects(session.api.request("/agents"), /Owner permission required/);
  assert.equal(refused, false);
  assert.equal(typeof await session.api.getToken(), "string");
});

test("invalid or already-expired login results cannot become sessions", async (t) => {
  for (const token of [
    null, { accessToken: "" }, { accessToken: "fixture", expiresAt: future() },
    { accessToken: "fixture", expiresAt: 0 },
  ]) {
    await t.test(JSON.stringify(token), async (t) => {
      const { calls } = backend(t, { token });
      await assert.rejects((await initializeAuth()).demoSignIn("fixture", "Visitor"), /Invalid demo login response|expired/);
      assert.equal(calls.some((call) => call.path === "/api/me"), false);
    });
  }
});

test("invalid warning and expiry metadata fail closed without loading MSAL", async (t) => {
  for (const value of [
    { ...config(), demo: undefined },
    { ...config(), demo: { expiresAt: future(), banner: "" } },
    config("not-a-date"),
  ]) {
    await t.test(JSON.stringify(value), async (t) => {
      backend(t, { config: value });
      await assert.rejects(initializeAuth(), /Invalid demo/);
    });
  }
  assert.throws(() => expiryTime("2030-01-01T10:00:00+02:00"), /Invalid demo/);
});

test("policy and authentication socket closures refuse instead of reconnecting; network closures can retry", () => {
  for (const code of [1008, 4001, 4003, 4010, 4401, 4403, 4410]) {
    assert.equal(demoSocketRefusal(code, ""), DEMO_REFUSED);
  }
  assert.equal(demoSocketRefusal(1000, "Demo expired"), DEMO_EXPIRED);
  assert.equal(demoSocketRefusal(1006, ""), undefined);
  const session = new DemoSession(future());
  session.refuse(DEMO_REFUSED);
  assert.throws(() => session.assertActive(), { message: DEMO_REFUSED });
  let message;
  const unsubscribe = session.subscribe((value) => { message = value; });
  assert.equal(message, DEMO_REFUSED);
  unsubscribe();
});
