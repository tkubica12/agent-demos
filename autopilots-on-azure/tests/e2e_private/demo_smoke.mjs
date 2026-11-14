// Bounded private DEMO acceptance: login, preserved personal chat, widget, and uploaded-file transformation.
// Env: DEMO_URL, DEMO_ACCESS_CODE, DEMO_OWNER_NAME, DEMO_PERSONAL_CHAT_ID, DEMO_OUT, optional PRIVATE_PLAYWRIGHT_MODULE, DEMO_CHROMIUM_PATH, DEMO_CHROMIUM_ARGS.
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
const fileProcessingTimeout = 480_000;
const steps = new Set((process.env.DEMO_STEPS ?? "login,reply,widget,files").split(","));
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
report.steps = [...steps];
let browser;
try {
  browser = await chromium.launch({
    ...(process.env.DEMO_CHROMIUM_PATH ? { executablePath: process.env.DEMO_CHROMIUM_PATH } : {}),
    ...(launchArgs ? { args: launchArgs } : {}),
  });
  report.browserLaunch = { status: "pass", version: browser.version() };
} catch (error) {
  report.browserLaunch = {
    status: "fail",
    error: String(error.message).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]"),
  };
  report.uiAcceptance = "not_run";
  report.fileProcessing = { status: "not_run", reason: "Browser launch failed." };
  report.codeExecution = { status: "not_run", nativeTrace: { status: "not_checked", path: null } };
  report.overallStatus = "fail";
  await writeFile(path.join(out, "demo-smoke.json"), JSON.stringify(report, null, 2));
  throw error;
}
const check = async (name, action) => {
  try { await action(); report.checks.push({ name, status: "pass" }); }
  catch (error) {
    const text = String(error.message).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
    report.checks.push({ name, status: "fail", failure: text });
    console.error(`${name}: ${text}`);
  }
};
async function eventually(action, waitMs = timeout) {
  const end = Date.now() + waitMs;
  while (Date.now() < end) { const v = await action(); if (v) return v; await new Promise((r) => setTimeout(r, 2000)); }
  throw new Error("Timed out waiting for condition.");
}
const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 720 } });
const page = await context.newPage();
page.setDefaultTimeout(30_000);
let prerequisitesFailed = false;
let token;
page.on("response", async (response) => {
  if (new URL(response.url()).pathname === "/api/demo/login" && response.ok()) token = (await response.json()).accessToken;
});
const fill = async (code) => {
  await page.getByLabel("Access code", { exact: true }).fill(code);
  await page.getByLabel("Display name", { exact: true }).fill(process.env.DEMO_OWNER_NAME);
  await page.getByRole("button", { name: "Enter demo", exact: true }).click();
};
const shot = (name) => page.screenshot({ path: path.join(out, `${name}.png`) });
const navigation = await page.goto(url);
const security = await navigation.securityDetails();
report.tls = security ? { protocol: security.protocol, issuer: security.issuer } : null;
let modelFailed = false;
if (["reply", "widget", "files"].some((step) => steps.has(step)) && !steps.has("login")) {
  prerequisitesFailed = true;
  report.checks.push({ name: "runner-configuration", status: "fail",
    failure: "Model-triggering steps require the login prerequisite." });
}
if (steps.has("files") && steps.has("reply") !== steps.has("widget")) {
  prerequisitesFailed = true;
  report.checks.push({ name: "runner-configuration", status: "fail",
    failure: "Select both personal-chat and widget checks, or neither when using a prior UI handoff." });
}
const prerequisiteCheck = async (name, action) => {
  await check(name, action);
  if (report.checks.at(-1).status !== "pass") prerequisitesFailed = true;
};
const modelCheck = async (name, action) => {
  if (prerequisitesFailed || modelFailed) {
    report.checks.push({ name, status: "skipped", failure: prerequisitesFailed
      ? "a prerequisite check failed" : "an earlier model-triggering step failed" });
    return;
  }
  const started = Date.now();
  await check(name, action);
  report.checks.at(-1).seconds = Math.round((Date.now() - started) / 1000);
  if (report.checks.at(-1).status !== "pass") {
    modelFailed = true;
    await shot(`failure-${name}`);
  }
};
await page.getByTestId("demo-warning").waitFor();
report.banner = (await page.getByTestId("demo-warning").innerText()).trim();
await shot("login-page");
const sendFromUi = async (text) => {
  await page.getByTestId("composer").locator("textarea").fill(text);
  const isSendRequest = (request) => request.method() === "POST" &&
    new URL(request.url()).pathname === `/api/conversations/${personal}/messages`;
  const isSendResponse = (response) => response.request().method() === "POST" &&
    new URL(response.url()).pathname === `/api/conversations/${personal}/messages`;
  const record = { clickedAt: new Date().toISOString(), conversationId: personal };
  report.sends = [...(report.sends ?? []), record];
  const requested = page.waitForRequest(isSendRequest).then((request) => {
    record.requestAt = new Date().toISOString();
    record.requestBody = request.postData();
    record.requestUrl = request.url();
    return request;
  });
  const posted = page.waitForResponse(isSendResponse).then((response) => {
    record.responseAt = new Date().toISOString();
    record.status = response.status();
    return response;
  });
  await page.getByRole("button", { name: "Send message" }).click();
  try {
    const [, response] = await Promise.all([requested, posted]);
    const responseText = await response.text();
    let body;
    try { body = JSON.parse(responseText); } catch { body = null; }
    record.responseBody = responseText.replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
    record.messageId = body?.id ?? body?.messageId ?? null;
    record.correlationHeaders = Object.fromEntries(Object.entries(await response.allHeaders())
      .filter(([name]) => /request-id|correlation|traceparent/i.test(name)));
    assert.ok([201, 202].includes(response.status()),
      `Send was not accepted (expected HTTP 201 or 202, got ${response.status()}); body=${record.responseBody}`);
    assert.ok(record.messageId, `Send succeeded without a message ID; body=${record.responseBody}`);
    return body;
  } catch (error) {
    record.failure = String(error.message).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
    throw error;
  }
};
if (steps.has("anonymous")) await prerequisiteCheck("anonymous-api-rejected", async () => {
  const response = await fetch(`${url}/api/users`, { redirect: "error" });
  assert.ok([401, 403].includes(response.status));
});
if (steps.has("wrong-code")) await prerequisiteCheck("wrong-code-rejected", async () => {
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
if (steps.has("login")) await prerequisiteCheck("demo-login", async () => {
  await fill(process.env.DEMO_ACCESS_CODE);
  await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
  await eventually(() => token);
  personal = process.env.DEMO_PERSONAL_CHAT_ID;
  assert.ok(personal, "DEMO_PERSONAL_CHAT_ID must identify the preserved personal chat.");
  const chat = (await api("/chats")).find((item) =>
    item.conversationId === personal && item.conversationType === "personal");
  assert.ok(chat, `The preserved personal chat ${personal} is not available to the logged-in owner.`);
  const members = await api(`/chats/${chat.id}/members`);
  agent = members.find((member) => member.isAgent);
  assert.ok(agent, "The preserved personal chat must contain its registered agent.");
  await page.locator(`[data-conversation-id="${personal}"]`).click();
  report.conversationId = personal;
  report.chatId = chat.id;
  report.agent = { id: agent.id, displayName: agent.displayName };
  await shot("after-login");
});
const reply = async (afterId, predicate, waitMs = timeout) => {
  let last = [];
  try {
    return await eventually(async () => {
      const messages = await api(`/conversations/${personal}/messages`);
      const index = messages.findIndex((message) => message.id === afterId);
      last = index >= 0 ? messages.slice(index + 1) : [];
      return index >= 0 && last.find((m) => m.author.id === agent.id && predicate(m));
    }, waitMs);
  } catch (error) {
    const seen = last.map((m) => ({
      id: m.id, authorId: m.author.id, createdAt: m.createdAt, textFormat: m.textFormat,
      text: String(m.text).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]").slice(0, 500),
    }));
    const enriched = new Error(`${error.message} Messages after request: ${JSON.stringify(seen)}`);
    enriched.observedMessages = seen;
    throw enriched;
  }
};
const downloadAttachment = async (message, file, name, waitMs = fileProcessingTimeout) => {
  const fileCard = page.locator(`[data-message-id="${message.id}"] [data-testid="file-card"]`)
    .filter({ hasText: name });
  await expect(fileCard).toBeVisible();
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: waitMs }),
    fileCard.locator(".file-card-name").click(),
  ]);
  assert.equal(await download.failure(), null);
  const destination = path.join(out, name);
  await download.saveAs(destination);
  const bytes = await readFile(destination);
  const apiBytes = await api(`/files/${file.id}/content`, "GET", undefined, true);
  assert.deepEqual(apiBytes, bytes, "The browser download differs from the authenticated file content.");
  return { bytes, destination };
};
report.nativeEvidenceTargets = [];
const saveNativeTarget = async (step, nonce, target) => {
  const targetPath = path.join(out, `native-target-${step}-${nonce}.json`);
  await writeFile(targetPath, JSON.stringify(target, null, 2));
  report.nativeEvidenceTargets.push({ step, path: targetPath, target });
  return targetPath;
};
const blockedPattern = /\b(?:tool|tools|terminal|python|local file|input file)\b.{0,100}\b(?:blocked|disabled|unavailable|not accessible|not available|not allowed|not permitted|denied|cannot|can't|couldn't|could not|unable|not able|requires? approval|approval (?:required|denied|not granted|expired|timed? out)|permission denied|timed? out|expired)\b|\b(?:blocked|disabled|unavailable|not accessible|not available|not allowed|not permitted|denied|cannot|can't|couldn't|could not|unable|not able|requires? approval|approval (?:required|denied|not granted|expired|timed? out)|permission denied|timed? out|expired)\b.{0,100}\b(?:tool|tools|terminal|python|local file|input file)\b/i;
const routeRefusalPattern = /(?:no local (?:input )?(?:file )?path|no (?:supported )?attachment route|original bytes? (?:are )?not (?:available|accessible)|not (?:exposed|provided) to .*terminal|only (?:the )?(?:extracted )?text|cannot access|can't access|could not access|unable to access|not available to .*terminal|terminal .*unavailable|file tools? (?:are )?(?:blocked|disabled)|script (?:is )?(?:blocked|disabled)|requires? approval|approval (?:denied|expired|timed? out)|permission denied|timed? out|expired)/i;
if (steps.has("reply")) await modelCheck("real-agent-reply", async () => {
  const nonce = randomUUID().replaceAll("-", ""), a = randomInt(10, 30), b = randomInt(10, 30);
  const sent = await sendFromUi(
    `Reply with the code ${nonce} and the numeric sum of ${a} + ${b}. Compute the sum directly without using any tools.`);
  const received = await reply(sent.id, (m) => m.text.includes(nonce));
  await expect(page.locator(`[data-message-id="${received.id}"]`)).toContainText(nonce);
  const responseText = String(received.text).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
  const sumVerified = new RegExp(`(?<!\\d)${a + b}(?!\\d)`).test(received.text);
  report.messaging = {
    status: sumVerified ? "pass" : "fail",
    requestMessageId: sent.id,
    responseMessageId: received.id,
    responseAt: received.createdAt,
    nonce,
    expectedSum: a + b,
    sumVerified,
    responseText,
  };
  report.reply = {
    request: sent.id, response: received.id, responseAt: received.createdAt,
    responseText,
    expectedSum: a + b, verified: sumVerified,
  };
  await page.screenshot({ path: path.join(out, "reply.png") });
  assert.ok(sumVerified, `Agent replied with the nonce but not the expected sum: ${responseText}`);
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
const nativeTrace = { status: "pending", path: null, requiredPath: "terminal", approvalMode: "smart" };
report.fileProcessing = { status: "not_run", reason: "Uploaded-file transformation step not selected." };
report.codeExecution = { status: "not_run", nativeTrace };
if (steps.has("files")) {
  if (prerequisitesFailed || modelFailed) {
    report.fileProcessing = { status: "not_run", reason: "An earlier prerequisite or model-triggering step failed." };
  } else {
    const nonce = randomUUID().replaceAll("-", "");
    const verificationCode = `${nonce}-${randomUUID().replaceAll("-", "")}`;
    const sourceName = `acceptance-source-${nonce.slice(0, 8)}.txt`;
    const outputName = `acceptance-uppercase-${nonce.slice(0, 8)}.txt`;
    const sourceText = `Private acceptance document. Verification code: ${verificationCode}.`;
    const sourceBytes = Buffer.from(sourceText, "utf8");
    const expectedBytes = Buffer.from(sourceText.toUpperCase(), "utf8");
    let sent;
    let received;
    let uploadedFile;
    let downloaded;
    let responseText = "";
    let sourceHashVerified = false;
    let target;
    let targetPath;
    try {
      await page.getByLabel("Upload attachment").setInputFiles({
        name: sourceName, mimeType: "text/plain", buffer: sourceBytes,
      });
      await expect(page.locator(".pending-attachments")).toContainText(sourceName);
      const requestedAt = Date.now() / 1000;
      sent = await sendFromUi(
        `Use the actual uploaded file ${sourceName} as the input. Resolve its original bytes/path only through the real production attachment or file-tool route available to this Hermes turn; do not assume a path. Run a Python script with your real terminal tool to read the original local file, uppercase its complete UTF-8 text, and write a distinct UTF-8 TXT named ${outputName}. ` +
        `Return the new file as a downloadable chat attachment through the supported MEDIA file-output contract. Do not reconstruct or copy the input from prompt text, return transformed text inline, reuse the source file, or return a link/code block. If the production route does not expose original bytes to the local script, say so plainly and do not fabricate a result. In your reply include exactly "Input SHA-256: <64 lowercase hex characters>" computed by Python reading the original file. The source contains a fresh verification code; preserve it exactly except for uppercasing.`);
      uploadedFile = sent.attachments?.find((item) => item.name === sourceName);
      assert.ok(uploadedFile, `The sent message did not contain the uploaded source file ${sourceName}.`);
      const uploadedBytes = await api(`/files/${uploadedFile.id}/content`, "GET", undefined, true);
      assert.deepEqual(uploadedBytes, sourceBytes, "The UI-uploaded source bytes differ from the fixture.");
      received = await reply(sent.id, (message) =>
        (Array.isArray(message.attachments) && message.attachments.length > 0) ||
        routeRefusalPattern.test(String(message.text)), fileProcessingTimeout);
      responseText = String(received.text).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
      const outputFile = received.attachments?.find((item) => item.name === outputName);
      if (!outputFile) {
        const blocked = blockedPattern.test(responseText) || routeRefusalPattern.test(responseText);
        report.fileProcessing = {
          status: "fail",
          reason: blocked ? "The agent reported that the uploaded file is unavailable to the local script."
            : "The agent reply did not contain the required distinct transformed TXT attachment.",
          requestMessageId: sent.id, responseMessageId: received.id, responseText,
          sourceName, outputName, sourceSha256: createHash("sha256").update(sourceBytes).digest("hex"),
        };
        report.codeExecution = {
          status: blocked ? "fail" : "not_proven",
          reason: blocked ? "Local script execution was blocked or the uploaded file was not locally accessible."
            : "Native execution evidence is required to prove the transformation path.",
          nativeTrace,
        };
        throw new Error(report.fileProcessing.reason);
      }
      const sourceShaMatch = responseText.match(/\bInput SHA-256:\s*([0-9a-f]{64})\b/);
      const sourceHash = createHash("sha256").update(sourceBytes).digest("hex");
      sourceHashVerified = sourceShaMatch?.[1] === sourceHash;
      assert.notEqual(outputFile.id, uploadedFile.id, "The transformed artifact must be distinct from the uploaded source.");
      const { bytes } = await downloadAttachment(received, outputFile, outputName);
      downloaded = bytes;
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(downloaded);
      target = {
        conversationId: personal, requestActivityId: sent.id, replyActivityId: received.id,
        filename: outputFile.name, size: downloaded.length,
        sha256: createHash("sha256").update(downloaded).digest("hex"), requestedAt,
      };
      targetPath = await saveNativeTarget("transformed", nonce, target);
      assert.ok(sourceHashVerified,
        "The agent did not report the exact lowercase SHA-256 computed by Python from the original uploaded file.");
      assert.equal(decoded, expectedBytes.toString("utf8"),
        "The downloaded artifact does not exactly match the locally computed uppercase fixture.");
      report.fileProcessing = {
        status: "pass",
        sourceName, sourceFileId: uploadedFile.id, sourceSha256: sourceHash,
        outputName: outputFile.name, outputFileId: outputFile.id,
        outputSha256: target.sha256,
        requestMessageId: sent.id, responseMessageId: received.id,
        responseAt: received.createdAt, nonce, sourceHashVerified,
        nativeEvidenceStatus: "pending",
        nativeEvidenceTarget: target, nativeEvidenceTargetPath: targetPath,
      };
      report.codeExecution = {
        status: "not_proven",
        reason: "Both file outputs match their byte contracts; exact correlated native terminal and Foundry evidence is still required.",
        nativeTrace,
      };
      await shot("uploaded-transform");
    } catch (error) {
      const failure = String(error.message).replaceAll(process.env.DEMO_ACCESS_CODE, "[REDACTED]");
      report.fileProcessing ??= {
        status: "fail", reason: failure, sourceName, outputName,
        requestMessageId: sent?.id ?? null, responseMessageId: received?.id ?? null,
        responseText, sourceSha256: createHash("sha256").update(sourceBytes).digest("hex"),
        sourceHashVerified, outputSha256: downloaded ? createHash("sha256").update(downloaded).digest("hex") : null,
        nativeEvidenceTarget: target ?? null, nativeEvidenceTargetPath: targetPath ?? null,
        observedMessages: error.observedMessages ?? [],
      };
      const codeFailed = blockedPattern.test(failure) || routeRefusalPattern.test(responseText) ||
        failure.includes("Timed out waiting for condition.") ||
        failure.includes("exact lowercase SHA-256 computed by Python") ||
        failure.includes("does not exactly match the locally computed uppercase fixture");
      report.codeExecution = {
        status: codeFailed ? "fail" : "not_proven",
        reason: failure, nativeTrace,
      };
      modelFailed = true;
      await shot("failure-uploaded-transform");
    }
  }
} else {
  report.fileProcessing = { status: "not_run", reason: "Uploaded-file transformation step not selected." };
}
if (!steps.has("files")) report.fileProcessing = {
  status: "not_run",
  reason: process.env.DEMO_FILE_PROCESSING_SKIP_REASON ?? "Uploaded-file transformation step not selected.",
};
if (!steps.has("files")) report.codeExecution = { status: "not_run", nativeTrace: { status: "not_checked", path: null } };
report.uiAcceptance = !steps.has("reply") || !steps.has("widget") ? "not_run"
  : report.checks.every((item) => item.status === "pass") ? "pass" : "fail";
report.overallStatus = [report.uiAcceptance, report.fileProcessing.status, report.codeExecution.status]
  .includes("fail") ? "fail"
  : [report.uiAcceptance, report.fileProcessing.status, report.codeExecution.status]
    .every((status) => status === "pass") ? "pass" : "not_proven";
await writeFile(path.join(out, "demo-smoke.json"), JSON.stringify(report, null, 2));
await browser.close();
console.log(JSON.stringify({
  uiAcceptance: report.uiAcceptance,
  fileProcessing: report.fileProcessing.status,
  codeExecution: report.codeExecution.status,
  overallStatus: report.overallStatus,
  checks: report.checks,
}));
process.exit(report.overallStatus === "pass" ? 0 : 1);
