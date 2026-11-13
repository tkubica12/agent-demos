import assert from "node:assert/strict";
import { createHash, randomInt, randomUUID } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { promisify } from "node:util";

const require = createRequire(new URL("../../chat-simulator/frontend/package.json", import.meta.url));
const { chromium, expect } = process.env.PRIVATE_PLAYWRIGHT_MODULE
  ? require(process.env.PRIVATE_PLAYWRIGHT_MODULE) : require("@playwright/test");
const config = JSON.parse(await readFile(process.env.PRIVATE_E2E_CONFIG, "utf8"));
const execute = promisify(execFile);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha = (bytes) => createHash("sha256").update(bytes).digest("hex");
const summary = { started: new Date().toISOString(), stacks: [] };
const browser = await chromium.launch({ headless: true });

async function eventually(action, timeout = config.timeout_ms) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await action();
    if (value) return value;
    await sleep(1000);
  }
  throw new Error("Timed out waiting for the real deployed workload.");
}

async function runStack(stack) {
  const directory = path.join(config.output, stack.name);
  await mkdir(directory, { recursive: true });
  const report = { name: stack.name, url: stack.url, checks: [], status: "running" };
  summary.stacks.push(report);
  const contexts = [];
  const check = async (name, action) => {
    try {
      await action();
      report.checks.push({ name, status: "pass", at: new Date().toISOString() });
    } catch (error) {
      const failure = String(error.message).replaceAll(process.env[stack.access_code_env], "[REDACTED]");
      report.checks.push({ name, status: "fail", failure, at: new Date().toISOString() });
      console.error(`${stack.name}/${name}: ${failure}`);
    }
    await writeFile(path.join(directory, "result.json"), JSON.stringify(report, null, 2));
  };
  try {
    const anonymous = await fetch(`${stack.url}/api/users`, { redirect: "error" });
    assert.ok([401, 403].includes(anonymous.status), "Anonymous API access must fail.");
    report.checks.push({ name: "anonymous-api-rejected", status: "pass", http: anonymous.status });
    async function login(displayName) {
      const context = await browser.newContext({ acceptDownloads: true });
      contexts.push(context);
      const page = await context.newPage();
      page.setDefaultTimeout(30_000);
      let token;
      page.on("response", async (response) => {
        if (new URL(response.url()).pathname === "/api/demo/login" && response.ok()) {
          token = (await response.json()).accessToken;
        }
      });
      await page.goto(stack.url);
      await expect(page.getByTestId("demo-warning")).toBeVisible();
      await page.getByLabel("Access code", { exact: true }).fill(process.env[stack.access_code_env]);
      await page.getByLabel("Display name", { exact: true }).fill(displayName);
      await page.getByRole("button", { name: "Enter demo", exact: true }).click();
      await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
      await eventually(() => token);
      async function api(resource, method = "GET", data, bytes = false) {
        const headers = { Authorization: `Bearer ${token}` };
        if (data && !(data instanceof FormData)) headers["Content-Type"] = "application/json";
        const response = await fetch(`${stack.url}/api${resource}`, {
          method, headers, body: data instanceof FormData ? data : data ? JSON.stringify(data) : undefined,
          redirect: "error", signal: AbortSignal.timeout(config.timeout_ms),
        });
        assert.ok(response.ok, `${method} ${resource} failed HTTP ${response.status}`);
        return bytes ? Buffer.from(await response.arrayBuffer()) : response.json();
      }
      return { page, api, me: await api("/me") };
    }
    const first = await login(stack.owner_display_name);
    const second = await login(`Private visitor B ${randomUUID().slice(0, 6)}`);
    assert.equal(first.me.authSource, "demo");
    assert.equal(first.me.upn.toLowerCase(), `${stack.worker_owner_id.toLowerCase()}@demo.invalid`,
      "DEMO owner identity must match the deployed WORKER_OWNER_ID, not the simulator database user ID.");
    assert.notEqual(first.me.id, second.me.id, "Two independent human sessions are required.");
    report.checks.push({ name: "two-real-demo-browser-logins-and-websockets", status: "pass" });
    const agents = (await first.api("/users")).filter((user) => user.isAgent);
    assert.equal(agents.length, 1, "Exactly one registered agent is required.");
    const agent = agents[0];
    async function chat(memberIds, name) {
      const value = await first.api("/chats", "POST", { memberIds, ...(name ? { name } : {}) });
      const selector = `[data-conversation-id="${value.conversationId}"]`;
      await expect(first.page.locator(selector)).toBeVisible();
      await first.page.locator(selector).click();
      return value.conversationId;
    }
    async function send(cid, text) {
      const pending = first.page.waitForResponse((response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/conversations/${cid}/messages`);
      await first.page.getByTestId("composer").getByRole("textbox").fill(text);
      await first.page.getByRole("button", { name: "Send message", exact: true }).click();
      const response = await pending;
      assert.equal(response.status(), 201);
      return response.json();
    }
    async function reply(cid, afterId, predicate) {
      return eventually(async () => {
        const messages = await first.api(`/conversations/${cid}/messages`);
        const index = messages.findIndex((message) => message.id === afterId);
        if (index < 0) return false;
        return messages.slice(index + 1).find((message) => message.author.id === agent.id && predicate(message));
      });
    }
    const personal = await chat([agent.id]);
    let personalReply;
    await check("personal-unique-model-challenge-and-typing", async () => {
      const nonce = randomUUID().replaceAll("-", "");
      const a = randomInt(100, 999), b = randomInt(100, 999);
      const typing = first.page.locator(".typing-indicator").waitFor({ state: "visible", timeout: config.timeout_ms });
      // Attach rejection handling immediately while the send awaits the real channel acknowledgment.
      const typingResult = typing.then(() => true, () => false);
      const sent = await send(personal, `Reply with the code ${nonce} and the numeric sum of ${a} + ${b}.`);
      personalReply = await reply(personal, sent.id, (message) =>
        message.text.includes(nonce) && message.text.includes(String(a + b)));
      assert.ok(await typingResult, "A genuine agent typing indicator must render.");
      await expect(first.page.locator(`[data-message-id="${personalReply.id}"]`)).toContainText(nonce);
      report.challenge = { nonce, expectedSum: a + b, requestMessage: sent.id, responseMessage: personalReply.id };
    });
    await check("reaction-render", async () => {
      const row = first.page.locator(`[data-message-id="${personalReply.id}"]`);
      await row.locator(".message-content").hover();
      await row.getByRole("button", { name: `React ${String.fromCodePoint(0x1f44d)}`, exact: true }).click();
      await expect(row.getByTestId("reaction-pills")).toBeVisible();
    });
    await check("group-two-humans-and-agent-mention", async () => {
      const cid = await chat([second.me.id, agent.id], `Private group ${randomUUID().slice(0, 6)}`);
      const nonce = randomUUID().replaceAll("-", "");
      const memberIds = (await first.api(`/chats`)).find((item) => item.conversationId === cid).memberIds;
      assert.equal(memberIds.length, 3);
      await second.page.locator(`[data-conversation-id="${cid}"]`).click();
      const composer = first.page.getByTestId("composer").getByRole("textbox");
      await composer.fill("@");
      await first.page.getByRole("option").filter({ hasText: agent.displayName }).click();
      await composer.press("End");
      await composer.pressSequentially(` Reply with ${nonce}.`);
      const pending = first.page.waitForResponse((response) => response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/conversations/${cid}/messages`);
      await first.page.getByRole("button", { name: "Send message", exact: true }).click();
      const sent = await (await pending).json();
      assert.ok(sent.mentions.some((mention) => mention.userId === agent.id));
      const received = await reply(cid, sent.id, (message) => message.text.includes(nonce));
      await expect(second.page.locator(`[data-message-id="${received.id}"]`)).toContainText(nonce);
    });
    await first.page.locator(`[data-conversation-id="${personal}"]`).click();
    await check("real-agent-adaptive-card-render", async () => {
      const sent = await send(personal, "/learning");
      const received = await reply(personal, sent.id, () => true);
      const card = received.activityAttachments?.find(
        (attachment) => attachment.contentType === "application/vnd.microsoft.card.adaptive");
      assert.ok(card, `Native /learning returned no Adaptive Card: ${received.text}`);
      assert.equal(card.content.type, "AdaptiveCard");
      assert.equal(card.content.version, "1.4");
      await expect(first.page.locator(`[data-message-id="${received.id}"]`).getByTestId("adaptive-card"))
        .toContainText("What I learned");
      report.learningCard = {
        trigger: "/learning", requestMessage: sent.id, responseMessage: received.id,
        nativeRuntimeIntegration: true, modelInferenceRequired: false,
      };
    });
    const persistedFiles = [];
    for (const filename of ["acceptance.txt", "acceptance.docx"]) {
      await check(`human-to-agent-${filename}`, async () => {
        const bytes = await readFile(path.join(config.output, "fixtures", filename));
        const form = new FormData();
        form.set("file", new Blob([bytes]), filename);
        const file = await first.api(`/conversations/${personal}/files`, "POST", form);
        const sent = await first.api(`/conversations/${personal}/messages`, "POST", {
          text: "Read the attached document and tell me its verification code.", attachmentIds: [file.id],
        });
        await reply(personal, sent.id, (message) => message.text.includes(config.fixture_codes[filename]));
        assert.equal(sha(await first.api(`/files/${file.id}/content`, "GET", undefined, true)), sha(bytes));
        persistedFiles.push({ id: file.id, sha256: sha(bytes) });
      });
    }
    await check("agent-document-download", async () => {
      const nonce = randomUUID().replaceAll("-", "");
      const sent = await send(personal,
        `Create a real downloadable TXT document containing exactly ${nonce}; attach the file using your channel file capability, not a markdown-only link or code block.`);
      const received = await reply(personal, sent.id, (message) => message.attachments.length > 0);
      const file = received.attachments[0];
      await expect(first.page.locator(`[data-message-id="${received.id}"]`)).toContainText(file.name);
      const downloadEvent = first.page.waitForEvent("download");
      await first.page.locator(`[data-message-id="${received.id}"]`).getByRole("button", { name: file.name, exact: true }).click();
      const download = await downloadEvent;
      assert.equal(await download.failure(), null);
      const bytes = await first.api(`/files/${file.id}/content`, "GET", undefined, true);
      assert.ok(bytes.includes(Buffer.from(nonce)), "Downloaded agent document must contain the unique challenge.");
      persistedFiles.push({ id: file.id, sha256: sha(bytes) });
    });
    await check("restart-chat-and-file-persistence", async () => {
      assert.ok(persistedFiles.length > 0, "File persistence requires an actual uploaded/downloaded file.");
      assert.ok(Array.isArray(stack.restart_command) && stack.restart_command.length,
        "A real restart_command argv is mandatory; page reload is not a restart.");
      const restarted = await execute(stack.restart_command[0], stack.restart_command.slice(1), { timeout: 600_000 });
      const reboot = JSON.parse(restarted.stdout.trim().split("\n").at(-1));
      assert.equal(reboot.verifiedReboot, true, "Restart command must provide real reboot evidence.");
      assert.ok(reboot.beforeBootId && reboot.afterBootId && reboot.beforeBootId !== reboot.afterBootId,
        "An unchanged guest boot identity is not a verified restart.");
      report.reboot = reboot;
      await eventually(async () => {
        try { return (await fetch(`${stack.url}/api/health`, { signal: AbortSignal.timeout(5000) })).ok; }
        catch (error) {
          if (error instanceof TypeError || error.name === "TimeoutError") return false;
          throw error;
        }
      });
      await first.page.reload();
      await expect(first.page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
      const messages = await first.api(`/conversations/${personal}/messages`);
      assert.ok(messages.some((message) => message.id === personalReply.id));
      for (const file of persistedFiles) {
        assert.equal(sha(await first.api(`/files/${file.id}/content`, "GET", undefined, true)), file.sha256);
      }
      report.persistedFiles = persistedFiles;
    });
    await first.page.screenshot({ path: path.join(directory, "private-chat.png"), fullPage: true });
    report.status = report.checks.every((item) => item.status === "pass") ? "pass" : "fail";
  } catch (error) {
    report.status = "fail";
    // Error messages only: stack traces and browser traces can contain access codes or bearer tokens.
    report.failure = String(error.message).replaceAll(process.env[stack.access_code_env], "[REDACTED]");
    console.error(`${stack.name}: ${report.failure}`);
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
    await writeFile(path.join(directory, "result.json"), JSON.stringify(report, null, 2));
  }
}

try {
  for (const stack of config.stacks) await runStack(stack);
} finally {
  await browser.close();
  summary.finished = new Date().toISOString();
  await writeFile(path.join(config.output, "summary.json"), JSON.stringify(summary, null, 2));
}
process.exitCode = summary.stacks.every((stack) => stack.status === "pass") ? 0 : 1;
