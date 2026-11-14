// Minimal DEMO smoke: wrong-code rejection, login, real agent reply, upload/download.
// Env: DEMO_URL, DEMO_ACCESS_CODE, DEMO_OWNER_NAME, DEMO_OUT (dir), optional PRIVATE_PLAYWRIGHT_MODULE, DEMO_CHROMIUM_PATH, DEMO_CHROMIUM_ARGS (JSON array).
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createHash, randomUUID, randomInt } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium, expect } = process.env.PRIVATE_PLAYWRIGHT_MODULE
  ? require(process.env.PRIVATE_PLAYWRIGHT_MODULE) : require("@playwright/test");
const url = process.env.DEMO_URL.replace(/\/$/, "");
const out = process.env.DEMO_OUT;
const timeout = 180_000;
await mkdir(out, { recursive: true });
const report = { url, at: new Date().toISOString(), checks: [] };
const browser = await chromium.launch({
  ...(process.env.DEMO_CHROMIUM_PATH ? { executablePath: process.env.DEMO_CHROMIUM_PATH } : {}),
  ...(process.env.DEMO_CHROMIUM_ARGS ? { args: JSON.parse(process.env.DEMO_CHROMIUM_ARGS) } : {}),
});
const check = async (name, action) => {
  try { await action(); report.checks.push({ name, status: "pass" }); }
  catch (error) {
    const text = String(error.message).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
    report.checks.push({ name, status: "fail", failure: text });
    console.error(`${name}: ${text}`);
  }
};
async function eventually(action) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const v = await action(); if (v) return v; await new Promise((r) => setTimeout(r, 2000)); }
  throw new Error("Timed out waiting for condition.");
}
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
let token;
page.on("response", async (response) => {
  if (new URL(response.url()).pathname === "/api/demo/login" && response.ok()) token = (await response.json()).accessToken;
});
const fill = async (code) => {
  await page.getByLabel("Access code", { exact: true }).fill(code);
  await page.getByLabel("Display name", { exact: true }).fill(process.env.DEMO_OWNER_NAME);
  await page.getByRole("button", { name: "Enter demo", exact: true }).click();
};
await page.goto(url);
await check("anonymous-api-rejected", async () => {
  const response = await fetch(`${url}/api/users`, { redirect: "error" });
  assert.ok([401, 403].includes(response.status));
});
await check("wrong-code-rejected", async () => {
  await expect(page.getByTestId("demo-warning")).toBeVisible();
  await fill(`wrong-${randomUUID()}`);
  await page.waitForTimeout(3000);
  assert.equal(token, undefined);
  await expect(page.getByTestId("authenticated")).toHaveCount(0);
});
const api = async (resource, method = "GET", data, bytes = false) => {
  const headers = { Authorization: `Bearer ${token}` };
  if (data && !(data instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(`${url}/api${resource}`, {
    method, headers, body: data instanceof FormData ? data : data ? JSON.stringify(data) : undefined,
    redirect: "error", signal: AbortSignal.timeout(timeout),
  });
  assert.ok(response.ok, `${method} ${resource} failed HTTP ${response.status}`);
  return bytes ? Buffer.from(await response.arrayBuffer()) : response.json();
};
let personal, agent;
await check("demo-login", async () => {
  await fill(process.env.DEMO_ACCESS_CODE);
  await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
  await eventually(() => token);
  agent = (await api("/users")).find((user) => user.isAgent);
  assert.ok(agent, "A registered agent is required.");
  personal = (await api("/chats", "POST", { memberIds: [agent.id] })).conversationId;
  await page.locator(`[data-conversation-id="${personal}"]`).click();
});
const reply = async (afterId, predicate) => {
  let last = [];
  try {
    return await eventually(async () => {
      const messages = await api(`/conversations/${personal}/messages`);
      const index = messages.findIndex((message) => message.id === afterId);
      last = index >= 0 ? messages.slice(index + 1) : [];
      return index >= 0 && last.find((m) => m.author.id === agent.id && predicate(m));
    });
  } catch (error) {
    const seen = last.map((m) => `${m.author.id === agent.id ? "agent" : "user"}/${m.textFormat}: ${String(m.text).slice(0, 160)}`);
    throw new Error(`${error.message} Messages after request: ${JSON.stringify(seen)}`);
  }
};
await check("real-agent-reply", async () => {
  const nonce = randomUUID().replaceAll("-", ""), a = randomInt(100, 999), b = randomInt(100, 999);
  const sent = await api(`/conversations/${personal}/messages`, "POST", {
    text: `Reply with the code ${nonce} and the numeric sum of ${a} + ${b}.`,
  });
  const received = await reply(sent.id, (m) => m.text.includes(nonce) && m.text.includes(String(a + b)));
  await expect(page.locator(`[data-message-id="${received.id}"]`)).toContainText(nonce);
  report.reply = { request: sent.id, response: received.id };
  await page.screenshot({ path: path.join(out, "reply.png") });
});
await check("widget-input-submit-followup", async () => {
  const nonce = randomUUID().replaceAll("-", "");
  const sent = await api(`/conversations/${personal}/messages`, "POST", {
    text: `Use your installed UNCHANGED teams-html-widget skill to generate a real interactive budget planner, not an Adaptive Card, hosted link or code example. ` +
      `Put ${nonce} in the title and fallback. Include one number input visibly labelled Budget, initially 5, with data-widget-field="budget"; ` +
      `show twice its value in output id="total" and update it locally on input. ` +
      `Include a user-click action labelled "Discuss planner ${nonce}". When I submit, acknowledge the selected budget and ${nonce} in this same chat. ` +
      `These numbers are illustrative; no external action or approval is requested.`,
  });
  const received = await reply(sent.id, (m) => m.textFormat === "extendedmarkdown" &&
    m.text.includes("```html-widget") && m.text.includes(nonce));
  const iframe = page.locator(`[data-message-id="${received.id}"] iframe`);
  await expect(iframe).toBeVisible();
  const frame = await (await iframe.elementHandle()).contentFrame();
  assert.ok(frame, "Widget frame unavailable.");
  await expect(frame.getByLabel("Budget", { exact: true })).toHaveValue("5");
  await expect(frame.locator("#total")).toHaveText("10");
  await frame.getByLabel("Budget", { exact: true }).fill("9");
  await expect(frame.locator("#total")).toHaveText("18");
  const toolsPath = `/api/messages/${received.id}/widgets/0/tools`;
  const pending = page.waitForResponse((r) => r.request().method() === "POST" && new URL(r.url()).pathname === toolsPath);
  await frame.getByRole("button", { name: `Discuss planner ${nonce}`, exact: true }).click();
  const response = await pending;
  assert.equal(response.status(), 200, "Widget invoke failed.");
  assert.equal((await response.json()).callToolResult.structuredContent.status, "accepted");
  const followup = await reply(received.id, (m) => m.text.includes(nonce) && m.text.includes("9"));
  await expect(page.locator(`[data-message-id="${followup.id}"]`)).toContainText(nonce);
  report.widget = { request: sent.id, widget: received.id, followup: followup.id };
  await page.screenshot({ path: path.join(out, "widget.png") });
});
await check("upload-and-download", async () => {
  const code = randomUUID().replaceAll("-", "");
  const bytes = Buffer.from(`Verification code: ${code}\n`);
  const form = new FormData();
  form.set("file", new Blob([bytes]), "demo.txt");
  const file = await api(`/conversations/${personal}/files`, "POST", form);
  const sent = await api(`/conversations/${personal}/messages`, "POST", {
    text: "Read the attached document and tell me its verification code.", attachmentIds: [file.id],
  });
  await reply(sent.id, (m) => m.text.includes(code));
  const hash = (b) => createHash("sha256").update(b).digest("hex");
  assert.equal(hash(await api(`/files/${file.id}/content`, "GET", undefined, true)), hash(bytes));
});
await writeFile(path.join(out, "demo-smoke.json"), JSON.stringify(report, null, 2));
await browser.close();
console.log(JSON.stringify(report.checks));
process.exit(report.checks.every((c) => c.status === "pass") ? 0 : 1);
