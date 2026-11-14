// Minimal DEMO smoke: wrong-code rejection, login, real agent reply, upload/download.
// Env: DEMO_URL, DEMO_ACCESS_CODE, DEMO_OWNER_NAME, DEMO_OUT (dir), optional PRIVATE_PLAYWRIGHT_MODULE, DEMO_CHROMIUM_PATH, DEMO_CHROMIUM_ARGS (JSON array).
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createHash, randomUUID, randomInt } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const require = createRequire(import.meta.url);
const { chromium, expect } = process.env.PRIVATE_PLAYWRIGHT_MODULE
  ? require(process.env.PRIVATE_PLAYWRIGHT_MODULE) : require("@playwright/test");
const url = process.env.DEMO_URL.replace(/\/$/, "");
const out = process.env.DEMO_OUT;
const timeout = 180_000;
await mkdir(out, { recursive: true });
const report = { url, at: new Date().toISOString(), checks: [] };
const FORBIDDEN_ARG = /^--(ignore-certificate-errors|disable-web-security|allow-running-insecure-content|disable-site-isolation-trials|single-process|no-zygote)/;
const DROPPED_FEATURES = new Set(["IsolateOrigins", "site-per-process"]);
function sanitizeArgs(args) {
  return args.filter((arg) => !FORBIDDEN_ARG.test(arg)).flatMap((arg) => {
    if (!arg.startsWith("--disable-features=")) return [arg];
    const kept = arg.slice("--disable-features=".length).split(",").filter((f) => f && !DROPPED_FEATURES.has(f));
    return kept.length ? [`--disable-features=${kept.join(",")}`] : [];
  });
}
const launchArgs = process.env.DEMO_CHROMIUM_ARGS ? sanitizeArgs(JSON.parse(process.env.DEMO_CHROMIUM_ARGS)) : undefined;
report.browserArgs = launchArgs ?? "playwright-default";
const browser = await chromium.launch({
  ...(process.env.DEMO_CHROMIUM_PATH ? { executablePath: process.env.DEMO_CHROMIUM_PATH } : {}),
  ...(launchArgs ? { args: launchArgs } : {}),
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
const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 720 } });
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
const steps = new Set((process.env.DEMO_STEPS ?? "anonymous,wrong-code,login,reply,widget,files").split(","));
const shot = (name) => page.screenshot({ path: path.join(out, `${name}.png`) });
const navigation = await page.goto(url);
const security = await navigation.securityDetails();
report.tls = security ? { protocol: security.protocol, issuer: security.issuer } : null;
report.steps = [...steps];
let modelFailed = false;
const modelCheck = async (name, action) => {
  if (modelFailed) { report.checks.push({ name, status: "skipped", failure: "an earlier model-triggering step failed" }); return; }
  const started = Date.now();
  await check(name, action);
  report.checks.at(-1).seconds = Math.round((Date.now() - started) / 1000);
  if (report.checks.at(-1).status !== "pass") modelFailed = true;
};
await page.getByTestId("demo-warning").waitFor();
report.banner = (await page.getByTestId("demo-warning").innerText()).trim();
await shot("login-page");
const sendFromUi = async (text) => {
  await page.getByTestId("composer").locator("textarea").fill(text);
  const posted = page.waitForResponse((r) => r.request().method() === "POST" &&
    new URL(r.url()).pathname === `/api/conversations/${personal}/messages`);
  await page.getByRole("button", { name: "Send message" }).click();
  const response = await posted;
  const body = await response.json().catch(() => ({}));
  report.sends = [...(report.sends ?? []), { at: new Date().toISOString(), status: response.status(), id: body.id }];
  assert.equal(response.status(), 201, `Send failed HTTP ${response.status()}`);
  return body;
};
if (steps.has("anonymous")) await check("anonymous-api-rejected", async () => {
  const response = await fetch(`${url}/api/users`, { redirect: "error" });
  assert.ok([401, 403].includes(response.status));
});
if (steps.has("wrong-code")) await check("wrong-code-rejected", async () => {
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
  report.conversationId = personal;
  await shot("after-login");
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
if (steps.has("reply")) await modelCheck("real-agent-reply", async () => {
  const nonce = randomUUID().replaceAll("-", ""), a = randomInt(100, 999), b = randomInt(100, 999);
  const sent = await sendFromUi(`Reply with the code ${nonce} and the numeric sum of ${a} + ${b}.`);
  const received = await reply(sent.id, (m) => m.text.includes(nonce) && m.text.includes(String(a + b)));
  await expect(page.locator(`[data-message-id="${received.id}"]`)).toContainText(nonce);
  report.reply = { request: sent.id, response: received.id };
  await page.screenshot({ path: path.join(out, "reply.png") });
});
if (steps.has("widget")) await modelCheck("widget-input-submit-followup", async () => {
  const nonce = randomUUID().replaceAll("-", "");
  const sent = await sendFromUi(
    `Use your installed UNCHANGED teams-html-widget skill to generate a real interactive budget planner, not an Adaptive Card, hosted link or code example. ` +
      `Put ${nonce} in the title and fallback. Include one number input visibly labelled Budget, initially 5, with data-widget-field="budget"; ` +
      `show twice its value in output id="total" and update it locally on input. ` +
      `Include a user-click action labelled "Discuss planner ${nonce}". When I submit, acknowledge the selected budget and ${nonce} in this same chat. ` +
      `These numbers are illustrative; no external action, preview check or approval is requested.`);
  const received = await reply(sent.id, (m) => m.textFormat === "extendedmarkdown" &&
    m.text.includes("```html-widget") && m.text.includes(nonce));
  const iframe = page.locator(`[data-message-id="${received.id}"] iframe`);
  await expect(iframe).toBeVisible();
  await shot("widget-rendered");
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
if (steps.has("files")) await modelCheck("upload-and-download", async () => {
  const code = randomUUID().replaceAll("-", "");
  const bytes = Buffer.from(`Verification code: ${code}\n`);
  const name = `demo-${code.slice(0, 8)}.txt`;
  await page.getByLabel("Upload attachment").setInputFiles({ name, mimeType: "text/plain", buffer: bytes });
  await expect(page.locator(".pending-attachments")).toContainText(name);
  const sent = await sendFromUi("Read the attached document and tell me its verification code.");
  const received = await reply(sent.id, (m) => m.text.includes(code));
  const card = page.locator(`[data-message-id="${sent.id}"] [data-testid="file-card"]`).filter({ hasText: name });
  await expect(card).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), card.locator(".file-card-name").click()]);
  const hash = (b) => createHash("sha256").update(b).digest("hex");
  const downloaded = await readFile(await download.path());
  report.files = { name, uploadedSha256: hash(bytes), downloadedSha256: hash(downloaded), reply: received.id };
  assert.equal(hash(downloaded), hash(bytes));
  await shot("files");
});
await writeFile(path.join(out, "demo-smoke.json"), JSON.stringify(report, null, 2));
await browser.close();
console.log(JSON.stringify(report.checks));
process.exit(report.checks.every((c) => c.status === "pass") ? 0 : 1);
