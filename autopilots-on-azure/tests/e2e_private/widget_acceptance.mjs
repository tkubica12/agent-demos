import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

export async function runWidgetAcceptance({ first, agent, chat, send, reply, directory, expect, prove, media, sanitize }) {
  const nonce = randomUUID().replaceAll("-", "");
  const report = { status: "fail", nonce, scope: "live-native-widget-only", checks: [] };
  const frames = path.join(directory, `widget-frames-${nonce}`);
  await mkdir(frames, { recursive: true });
  let recording = false;
  let capture;
  let captureError;
  let frameCount = 0;
  const page = first.page;
  try {
    const cid = await chat([agent.id], `Live widget ${nonce.slice(0, 6)}`);
    report.conversationId = cid;
    const actualChat = (await first.api("/chats")).find(item => item.conversationId === cid);
    assert.equal(actualChat?.conversationType, "personal");
    assert.deepEqual([...actualChat.memberIds].sort(), [first.me.id, agent.id].sort(),
      "The fresh widget chat must contain only this human and the real agent.");
    report.freshPersonalChat = true;
    await page.evaluate(() => {
      window.privateWidgetMethods = [];
      window.addEventListener("message", event => {
        const method = event.data?.method;
        if (["ui/initialize", "ui/notifications/initialized", "ui/notifications/size-changed"].includes(method)) {
          window.privateWidgetMethods.push(method);
        }
      });
    });
    const requestedAt = Date.now() / 1000;
    const sent = await send(cid,
      `Use your installed UNCHANGED teams-html-widget skill to generate a real interactive budget planner, not an Adaptive Card, hosted link or code example. ` +
      `Put ${nonce} in the title and fallback. Include one number input visibly labelled Budget, initially 5, with data-widget-field="budget"; ` +
      `show twice its value in output id="total" and update it locally on input. ` +
      `Include a user-click action labelled "Discuss planner ${nonce}". When I submit, acknowledge the selected budget and ${nonce} in this same chat. ` +
      `These numbers are illustrative; no external action or approval is requested.`);
    report.requestActivityId = sent.id;
    const received = await reply(cid, sent.id, message =>
      message.textFormat === "extendedmarkdown" && message.text.includes("```html-widget") && message.text.includes(nonce));
    report.widgetActivityId = received.id;
    const iframe = page.locator(`[data-message-id="${received.id}"] iframe`);
    await expect(iframe).toBeVisible();
    await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
    const frame = await (await iframe.elementHandle()).contentFrame();
    assert.ok(frame, "Actual sandboxed widget frame is unavailable.");
    await expect(frame.locator("#autopilot-status")).toContainText("Interactive preview");
    const methods = await page.evaluate(() => window.privateWidgetMethods);
    assert.ok(methods.includes("ui/initialize") && methods.includes("ui/notifications/initialized"),
      "Actual widget initialization handshake was not observed.");
    report.checks.push("real-widget-sandbox-and-initialize");
    recording = true;
    capture = (async () => {
      try {
        while (recording && frameCount < 79) {
          await page.screenshot({ path: path.join(frames, `frame-${String(frameCount++).padStart(4, "0")}.png`) });
          await new Promise(resolve => setTimeout(resolve, 200));
        }
      } catch (error) {
        captureError = error;
      }
    })();
    await expect(frame.getByLabel("Budget", { exact: true })).toHaveValue("5");
    await expect(frame.locator("#total")).toHaveText("10");
    await frame.getByLabel("Budget", { exact: true }).fill("9");
    await expect(frame.locator("#total")).toHaveText("18");
    report.checks.push("local-input-recompute");
    await frame.locator("#autopilot-root").evaluate(root => root.append(document.createElement("hr")));
    await expect.poll(async () => {
      const actual = (await iframe.boundingBox()).height;
      const content = await frame.locator("#autopilot-root").evaluate(root => root.getBoundingClientRect().height);
      return Math.abs(actual - content);
    }).toBeLessThanOrEqual(1);
    assert.ok((await page.evaluate(() => window.privateWidgetMethods)).includes("ui/notifications/size-changed"));
    report.checks.push("actual-resize-within-one-pixel");
    const toolsPath = `/api/messages/${received.id}/widgets/0/tools`;
    const submittedAt = Date.now() / 1000;
    const responsePromise = page.waitForResponse(response =>
      response.request().method() === "POST" && new URL(response.url()).pathname === toolsPath);
    const button = frame.getByRole("button", { name: `Discuss planner ${nonce}`, exact: true });
    await button.click();
    const response = await responsePromise;
    assert.equal(response.status(), 200, "Real widget invoke failed.");
    const result = await response.json();
    assert.equal(result.responseType, "htmlwidget/calltoolresult");
    assert.equal(result.callToolResult.isError, false);
    assert.equal(result.callToolResult.structuredContent.status, "accepted");
    const submission = response.request().postDataJSON();
    assert.equal(submission.name, "autopilot.widget.submit");
    assert.ok(typeof submission.arguments?.token === "string" && submission.arguments.token,
      "Actual click supplied no bounded widget token.");
    await expect(frame.locator("#autopilot-status")).toContainText("Selection received");
    await expect(button).toBeDisabled();
    report.checks.push("real-calltool-token-accepted-and-button-acknowledged");
    const followup = await reply(cid, received.id, message =>
      message.text.includes(nonce) && message.text.includes("9"));
    report.followupActivityId = followup.id;
    await expect(page.locator(`[data-message-id="${followup.id}"]`)).toContainText(nonce);
    report.checks.push("real-native-followup-in-same-chat");
    const replay = await first.api(`${toolsPath.slice(4)}`, "POST", submission);
    assert.equal(replay.responseType, "htmlwidget/calltoolresult");
    assert.equal(replay.callToolResult.structuredContent.status, "already_submitted",
      "The same real token must not authorize another model turn.");
    report.replayRejected = true;
    report.checks.push("same-token-replay-rejected-as-no-effect");
    await page.screenshot({ path: path.join(directory, "live-widget.png") });
    recording = false;
    await capture;
    if (captureError) throw captureError;
    await page.screenshot({ path: path.join(frames, `frame-${String(frameCount++).padStart(4, "0")}.png`) });
    assert.ok(frameCount >= 2, "Actual interaction recording has fewer than two frames.");
    report.producing = await prove({
      turn: "producing", conversationId: cid, requestActivityId: sent.id,
      replyActivityId: received.id, widgetActivityId: received.id, requestedAt,
    });
    report.followup = await prove({
      turn: "followup", conversationId: cid, replyActivityId: followup.id,
      widgetActivityId: received.id, requestedAt: submittedAt,
    });
    assert.notEqual(report.producing.turn.nativeRunId, report.followup.turn.nativeRunId,
      "Producing and follow-up turns must have their own exact native runs.");
    report.media = await media(frames, path.join(directory, "live-widget.gif"));
    report.status = "pass";
    return report;
  } catch (error) {
    report.failure = sanitize(error.message);
    throw error;
  } finally {
    recording = false;
    if (capture) await capture;
    await writeFile(path.join(directory, "widget-result.json"), JSON.stringify(report, null, 2));
  }
}
