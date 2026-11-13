import assert from "node:assert/strict";
import test from "node:test";
import { createWidgetProtocol, widgetParts } from "../src/widget-policy.ts";

const widget = { type: "widget/mcp-ui", name: "Planner", html: "<!doctype html><p>Planner</p>",
  domain: "https://teams.microsoft.com", securityPolicy: {} };
const fence = (value = widget) => "```html-widget\n" + JSON.stringify(value) + "\n```\n";
test("only agent extendedmarkdown widgets execute; surrounding markdown is retained", () => {
  const text = "Before **bold**\n\n" + fence() + "\nAfter";
  assert.deepEqual(widgetParts(text, "extendedmarkdown", true),
    [{ text: "Before **bold**\n\n" }, { widget, index: 0 }, { text: "\nAfter" }]);
  for (const [format, agent] of [["markdown", true], ["extendedmarkdown", false], ["plain", true]]) {
    assert.deepEqual(widgetParts(text, format, agent), [{ text }]);
  }
});
test("invalid, oversized, incomplete and nested widget fences remain code", () => {
  for (const text of ["```html-widget\nnot json\n```", fence({ ...widget, domain: "javascript:alert(1)" }),
    fence({ ...widget, name: "" }), fence({ ...widget, securityPolicy: { connectDomains: ["'self'"] } }),
    fence({ ...widget, html: "x".repeat(100_001) }), "````text\n" + fence() + "````",
    "~~~text\n" + fence() + "~~~", fence().slice(0, -5)]) {
    assert.deepEqual(widgetParts(text, "extendedmarkdown", true), [{ text }]);
  }
});
test("invalid fences do not shift the stored widget index; larger closing markers work", () => {
  const text = fence({ ...widget, name: "" }) + fence().replace(/\n```\n$/, "\n````\n");
  assert.equal(widgetParts(text, "extendedmarkdown", true)[1].index, 1);
});
test("Unicode JSON separators, duplicate keys and invalid ports match the backend fence rules", () => {
  const unicode = { ...widget, name: "Planner\u2028next\u0085line" };
  assert.deepEqual(widgetParts(fence(unicode), "extendedmarkdown", true), [{ widget: unicode, index: 0 }]);
  for (const source of [
    "```html-widget\n" + JSON.stringify(widget).replace('"name":"Planner"', '"name":"Wrong","name":"Planner"') + "\n```",
    fence({ ...widget, domain: "https://example.invalid:0" }),
    "\u2028" + fence(),
  ]) assert.deepEqual(widgetParts(source, "extendedmarkdown", true), [{ text: source }]);
});

function fixture(call = async () => ({ isError: false })) {
  const replies = [], heights = [];
  let initialized = 0;
  const receive = createWidgetProtocol({
    reply: (reply) => replies.push(reply), resize: (height) => heights.push(height), call,
    initialized: () => initialized++, context: () => ({ theme: "light", locale: "cs-CZ" }),
  });
  const rpc = (method, params = {}, id) => receive({ jsonrpc: "2.0", method, params, ...(id === undefined ? {} : { id }) });
  const init = async () => {
    await rpc("ui/initialize", { protocolVersion: "2026-01-26", appInfo: {}, appCapabilities: {} }, "init");
    await rpc("ui/notifications/initialized");
  };
  return { receive, rpc, init, replies, heights, initialized: () => initialized };
}
test("host initializes once with a realistic context, handles fractional height and rejects unknown methods", async () => {
  const f = fixture();
  await f.init();
  assert.equal(f.initialized(), 1);
  assert.equal(f.replies[0].result.hostContext.displayMode, "inline");
  assert.equal(f.replies[0].result.hostContext.locale, "cs-CZ");
  await f.rpc("ui/notifications/size-changed", { height: 222.25 });
  await f.rpc("ui/notifications/size-changed", { height: 100_000 });
  assert.deepEqual(f.heights, [223, 4096]);
  await f.rpc("ui/open-link", {}, "unknown");
  assert.equal(f.replies.at(-1).error.code, -32601);
  await f.rpc("ui/initialize", {}, "again");
  assert.ok(f.replies.at(-1).error);
});
test("unknown or repeated notifications never receive JSON-RPC responses", async () => {
  const f = fixture();
  await f.init();
  const count = f.replies.length;
  await f.rpc("ui/notifications/initialized");
  await f.rpc("notifications/message", { message: "diagnostic" });
  assert.equal(f.replies.length, count);
});
test("malformed JSON-RPC, giant arguments and non-finite heights get explicit errors", async () => {
  const f = fixture();
  await f.init();
  for (const data of [null, {}, { jsonrpc: "1.0", id: "x", method: "tools/call", params: {} },
    { jsonrpc: "2.0", id: [], method: "tools/call", params: {} },
    { jsonrpc: "2.0", id: "x", method: "tools/call", params: { text: "a".repeat(20_000) } }]) {
    await f.receive(data);
    assert.ok(f.replies.at(-1).error);
  }
  await f.rpc("ui/notifications/size-changed", { height: Infinity });
  assert.deepEqual(f.heights, []);
});
test("one in-flight call, bounded flood, same-id result, isError passthrough and transport errors", async () => {
  let resolve;
  const f = fixture(() => new Promise((done) => { resolve = done; }));
  await f.init();
  const params = { name: "autopilot.widget.submit", arguments: { token: "opaque", values: {} } };
  const first = f.rpc("tools/call", params, "selection-1");
  await f.rpc("tools/call", params, "selection-2");
  assert.ok(f.replies.at(-1).error);
  resolve({ isError: true, content: [{ type: "text", text: "Expired" }] });
  await first;
  assert.equal(f.replies.at(-1).id, "selection-1");
  assert.equal(f.replies.at(-1).result.isError, true);
  const failed = fixture(async () => { throw new Error("Agent returned HTTP 500"); });
  await failed.init();
  for (let index = 0; index < 7; index++) await failed.rpc("tools/call", params, index);
  assert.match(failed.replies.at(-1).error.message, /rate limited/);
  assert.match(failed.replies[1].error.message, /500/);
});
