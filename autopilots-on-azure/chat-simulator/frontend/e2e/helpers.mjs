import { test as base, expect } from "@playwright/test";
import { appendFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";

export { expect };
export const hostedRun = false;
if (process.env.TSIM_E2E_HOSTED_RUN) throw new Error("This suite supports isolated local test identities only.");
export async function restartBackend() {
  const response = await fetch(`${process.env.TSIM_E2E_CONTROL}/restart`, {
    method: "POST", headers: { "x-e2e-key": process.env.TSIM_E2E_CONTROL_KEY },
  });
  if (!response.ok) throw new Error(`Local backend restart failed (${response.status}).`);
  return response.json();
}
export const url = process.env.TSIM_E2E_URL;
export const evidence = process.env.TSIM_E2E_EVIDENCE;
export const enc = encodeURIComponent;
export const unique = (prefix) => `${hostedRun ? `${hostedRun}-c03-` : ""}${prefix}-${randomUUID().slice(0, 8)}`;
const tokens = new Map();
const identities = new Map();

export async function identity(persona) {
  if (!tokens.has(persona)) {
    const response = await fetch(`${process.env.TSIM_E2E_IDP}/token`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ userId: persona }),
    });
    if (!response.ok) throw new Error(`Synthetic identity failed (${response.status}).`);
    tokens.set(persona, (await response.json()).access_token);
  }
  if (!identities.has(persona)) {
    const result = await api(persona, "/me");
    if (result.status !== 200) throw new Error(`Synthetic identity verification failed (${result.status}).`);
    identities.set(persona, result.body);
  }
  return identities.get(persona);
}

export async function api(persona, resource, method = "GET", data, form) {
  if (!tokens.has(persona)) await identity(persona);
  const headers = { Authorization: `Bearer ${tokens.get(persona)}` };
  if (data !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(`${url}/api${resource}`, {
    method, headers, redirect: "error", body: form || (data === undefined ? undefined : JSON.stringify(data)),
  });
  const content = Buffer.from(await response.arrayBuffer());
  const body = response.headers.get("content-type")?.includes("application/json") && content.length
    ? JSON.parse(content.toString()) : content;
  return { status: response.status, headers: Object.fromEntries(response.headers), body };
}
export async function ok(persona, resource, method = "GET", data, form) {
  const result = await api(persona, resource, method, data, form);
  expect(result.status, `${method} ${resource}`).toBeGreaterThanOrEqual(200);
  expect(result.status, `${method} ${resource}`).toBeLessThan(300);
  return result.body;
}
export async function metric(name, value) {
  await mkdir(evidence, { recursive: true });
  await writeFile(path.join(evidence, `${name}.json`), JSON.stringify(value, null, 2));
}
export const message = (page, id) => page.locator(`article[data-message-id="${id}"]`);
export const teamRow = (page, id) => page.locator(`.sidebar-team[data-team-id="${id}"]`);
export async function navigate(page, conversationId) {
  await page.locator(`[data-conversation-id="${conversationId}"]`).click();
  await expect(page.getByTestId("composer")).toBeVisible();
}
export async function send(page, text, label = "Message in channel") {
  const composer = page.getByTestId("composer").filter({ has: page.getByRole("textbox", { name: label, exact: true }) });
  await composer.getByRole("textbox", { name: label, exact: true }).fill(text);
  const response = page.waitForResponse((response) =>
    response.request().method() === "POST" && /\/conversations\/[^/]+\/messages$/.test(new URL(response.url()).pathname));
  await composer.getByRole("button", { name: "Send message", exact: true }).click();
  const result = await response;
  expect(result.status()).toBe(201);
  return result.json();
}
export async function createTeam(page, name = unique("Browser collaboration")) {
  await page.getByRole("button", { name: "Create team", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox", { name: "Name", exact: true }).fill(name);
  const created = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/teams");
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  expect((await created).status()).toBe(201);
  await expect(dialog).toHaveCount(0);
  const teams = await ok("owner", "/teams");
  const team = teams.find((item) => item.name === name);
  expect(team).toBeTruthy();
  await expect(teamRow(page, team.id)).toBeVisible();
  return team;
}
export async function setupTeam(owner, member) {
  const team = await createTeam(owner);
  const user = await identity("member");
  await ok("owner", `/teams/${team.id}/members`, "POST", { userId: user.id });
  await expect(teamRow(member, team.id)).toBeVisible();
  const cid = team.channels[0].conversationId;
  await navigate(owner, cid);
  await navigate(member, cid);
  return { team, cid };
}
export async function teamMenu(page, team, action) {
  await teamRow(page, team.id).getByRole("button", { name: `More options for ${team.name}`, exact: true }).click();
  await page.getByRole("menuitem", { name: action, exact: true }).click();
}
export async function createChat(page, people, name) {
  await page.getByRole("button", { name: "New message", exact: true }).click();
  for (const person of people) {
    await page.getByRole("textbox", { name: "To: participants" }).fill(person.displayName);
    await page.getByRole("option").filter({ hasText: person.displayName }).click();
  }
  if (name) await page.getByRole("textbox", { name: "Group name", exact: true }).fill(name);
  const response = page.waitForResponse((response) =>
    response.request().method() === "POST" && new URL(response.url()).pathname === "/api/chats");
  await page.getByRole("button", { name: "Start conversation", exact: true }).click();
  const result = await response;
  expect(result.status()).toBe(201);
  const chat = await result.json();
  // The new-chat page also has a Composer; its textbox alone no longer proves navigation completed.
  await expect(page.locator(`.sidebar-chat[data-conversation-id="${chat.conversationId}"]`)).toHaveClass(/\bselected\b/);
  await expect(page.getByRole("textbox", { name: "To: participants" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Type a message", exact: true })).toBeVisible();
  return chat;
}
export async function pickEmoji(page, trigger, value, reaction = false) {
  const catalogue = await (await fetch(`${url}/emoji/catalogue.json`)).json();
  const entry = catalogue.entries.find((item) => item.emoji.replaceAll("\ufe0f", "") === value.replaceAll("\ufe0f", ""));
  expect(entry, `Fluent catalogue includes ${value}`).toBeTruthy();
  await trigger.click();
  const picker = page.getByTestId(reaction ? "reaction-picker" : "emoji-picker");
  await picker.getByRole("textbox", { name: "Find emoji" }).fill(entry.emoji);
  await picker.getByRole("button", { name: entry.label, exact: true }).click();
}

export const test = base.extend({
  people: async ({ browser }, use, testInfo) => {
    const contexts = [];
    const people = {};
    const events = [];
    const problems = [];
    try {
    for (const persona of ["owner", "member", "outsider", "admin"]) {
      await identity(persona);
      const context = await browser.newContext({ baseURL: url, viewport: { width: 1366, height: 900 },
        acceptDownloads: true });
      contexts.push(context);
      const page = await context.newPage();
      page.on("dialog", async (dialog) => {
        problems.push({ persona, type: "dialog", message: dialog.message() }); await dialog.dismiss();
      });
      page.on("console", (event) => {
        if (event.text().includes("TSIM_XSS_EXECUTED")) problems.push({ persona, type: "console-marker" });
      });
      page.on("pageerror", (error) => events.push({ persona, at: Date.now(), type: "page-error",
        message: error.message.replace(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, "[REDACTED_JWT]") }));
      page.on("websocket", (socket) => {
        const parsed = new URL(socket.url());
        events.push({ persona, type: "socket-open", path: parsed.pathname, query: parsed.search ? "PRESENT" : "none" });
        socket.on("framereceived", ({ payload }) => {
          try {
            const event = JSON.parse(String(payload));
            events.push({ persona, at: Date.now(), type: event.type, messageId: event.message?.id,
              conversationId: event.conversationId, teamId: event.teamId });
          } catch {}
        });
        // Never subscribe to outgoing frames: the first frame contains a bearer token.
      });
      page.on("response", (response) => {
        const parsed = new URL(response.url());
        if (parsed.origin === url && (parsed.pathname.startsWith("/api/") || response.status() >= 400)) {
          events.push({ persona, at: Date.now(), type: "http", method: response.request().method(),
            path: parsed.pathname, status: response.status() });
        }
      });
      await page.goto("/");
      await expect(page.getByTestId(`test-login-${persona}`)).toBeVisible({ timeout: 10_000 });
      await page.getByTestId(`test-login-${persona}`).click({ timeout: 10_000 });
      await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
      await expect(page.locator("html")).toHaveAttribute("data-emoji-ready", "true");
      people[persona] = page;
    }
    await use({ ...people, events, problems });
    }
    finally {
      try {
        await mkdir(evidence, { recursive: true });
        await appendFile(path.join(evidence, "sanitized-events.ndjson"),
          events.map((event) => JSON.stringify({ test: testInfo.title, ...event })).join("\n") + "\n");
      } finally {
        await Promise.all(contexts.map((context) => context.close()));
      }
      expect(problems, "No executed XSS, console execution marker or browser dialog").toEqual([]);
      expect(events.filter((event) => event.type === "socket-open" && event.query !== "none"),
        "WebSocket authentication never appears in query strings").toEqual([]);
    }
  },
});
