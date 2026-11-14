import { test, expect } from "@playwright/test";

const expiredUrl = process.env.TSIM_DEMO_E2E_EXPIRED_URL;
const code = process.env.TSIM_DEMO_E2E_ACCESS_CODE;
const warning = (page) => page.getByTestId("demo-warning");

async function expectWarning(page) {
  await expect(warning(page)).toBeVisible();
  await expect(warning(page)).toContainText("DEMO MODE");
  await expect(warning(page)).toContainText("no real authentication");
  await expect(warning(page)).toContainText("test data only");
  await expect(warning(page)).toContainText("expires");
  await expect(warning(page)).toHaveCSS("background-color", "rgb(177, 14, 30)");
  await expect(page.getByRole("button", { name: "Přihlásit přes Microsoft", exact: true })).toHaveCount(0);
  await expect(page.locator('[data-testid^="test-login-"]')).toHaveCount(0);
}

async function login(page) {
  await page.goto("/");
  await expectWarning(page);
  await page.getByLabel("Access code", { exact: true }).fill(code);
  await page.getByLabel("Display name", { exact: true }).fill("Browser demo visitor");
  const response = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/demo/login" && response.request().method() === "POST");
  await page.getByRole("button", { name: "Enter demo", exact: true }).click();
  const result = await response;
  expect(result.ok(), "Real demo login succeeds").toBe(true);
  const token = await result.json();
  expect(typeof token.accessToken === "string" && token.accessToken.length > 0).toBe(true);
  expect(typeof token.expiresAt).toBe("number");
  await expect(page.getByTestId("authenticated")).toHaveAttribute("data-auth-source", "demo");
  await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
  return token;
}

test("D01 real demo login keeps its red warning across navigation, theme, profile and logout", async ({ page }) => {
  const externalAuth = [];
  const socketPaths = [];
  let tokenFrameSeen = false;
  page.on("request", (request) => {
    if (/login\.microsoftonline\.com|\/authorize(?:\?|$)/i.test(request.url())) externalAuth.push("unexpected account login");
  });
  page.on("websocket", (socket) => {
    const url = new URL(socket.url());
    if (url.pathname !== "/api/ws") return;
    socketPaths.push({ path: url.pathname, hasQuery: !!url.search });
    socket.on("framesent", ({ payload }) => {
      try { tokenFrameSeen ||= typeof JSON.parse(String(payload)).token === "string"; } catch {}
    });
  });
  await login(page);
  await expect(page).toHaveTitle("Agent Chat Simulator");
  await expectWarning(page);
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your activity", exact: true })).toBeVisible();
  await expectWarning(page);
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await page.locator('button[aria-label="New message"]').click();
  await expect(page.getByRole("textbox", { name: "To: participants" })).toBeVisible();
  await expectWarning(page);
  await page.getByRole("button", { name: "See all your teams", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Your teams and channels" })).toBeVisible();
  await expectWarning(page);
  await page.getByRole("button", { name: "Use dark theme", exact: true }).click();
  await expectWarning(page);
  await page.getByRole("button", { name: "Your profile", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: "Manage AI agents" })).toHaveCount(0);
  await expectWarning(page);
  await page.getByRole("menuitem", { name: "Switch account", exact: true }).click();
  await expect(page.getByLabel("Access code", { exact: true })).toHaveValue("");
  await expectWarning(page);
  expect(externalAuth).toEqual([]);
  expect(socketPaths.length).toBeGreaterThan(0);
  expect(socketPaths.every((socket) => !socket.hasQuery)).toBe(true);
  expect(tokenFrameSeen).toBe(true);
});

test("D02 expired real server preserves HTTP 410 config warning and explicitly refuses login, API and socket", async ({ page, request }) => {
  const configResponse = await request.get(`${expiredUrl}/api/config`);
  expect(configResponse.status()).toBe(410);
  const config = await configResponse.json();
  expect(config.authMode).toBe("demo");
  let attemptedLogin = false;
  page.on("request", (request) => { attemptedLogin ||= new URL(request.url()).pathname === "/api/demo/login"; });
  await page.goto(expiredUrl);
  await expectWarning(page);
  await expect(warning(page)).toHaveText(config.demo.banner);
  await expect(page.getByTestId("auth-error")).toContainText(/demo expired/i);
  await expect(page.getByRole("button", { name: "Enter demo", exact: true })).toBeDisabled();
  expect(attemptedLogin).toBe(false);
  expect((await request.post(`${expiredUrl}/api/demo/login`, {
    data: { accessCode: code, displayName: "Expired demo visitor" },
  })).status()).toBe(410);
  expect((await request.get(`${expiredUrl}/api/users`)).status()).toBe(410);
  const result = await page.evaluate((base) => new Promise((resolve, reject) => {
    const url = new URL("/api/ws", base);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    let ready = false;
    const timer = setTimeout(() => { socket.close(); reject(new Error("Expired socket did not refuse access.")); }, 5000);
    socket.onmessage = (event) => {
      try { ready ||= JSON.parse(event.data).type === "ready"; } catch {}
    };
    socket.onclose = () => { clearTimeout(timer); resolve({ closed: true, ready }); };
    socket.onerror = () => {};
  }), expiredUrl);
  expect(result).toEqual({ closed: true, ready: false });
  await expectWarning(page);
});

test("D03 an open real demo session is removed at its advertised deadline without reauthentication", async ({ page }) => {
  await page.clock.install();
  const token = await login(page);
  const response = await page.request.get("/api/config");
  const config = await response.json();
  const deadline = Math.min(token.expiresAt * 1000, Date.parse(config.demo.expiresAt));
  const now = await page.evaluate(() => Date.now());
  await page.clock.fastForward(Math.max(0, deadline - now) + 1000);
  await expect(page.getByTestId("authenticated")).toHaveCount(0);
  await expect(page.getByTestId("auth-error")).toContainText(/demo.*expired/i);
  await expectWarning(page);
});
