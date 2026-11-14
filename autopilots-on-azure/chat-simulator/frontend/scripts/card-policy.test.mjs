import assert from "node:assert/strict";
import test from "node:test";
import { isRepresentedFileAttachment, prepareCard, safeCardUrl } from "../src/card-policy.ts";

const origin = "http://127.0.0.1:8030";
const fileMetadataType = "application/vnd.microsoft.teams.file.download.info";
test("native file metadata does not render a second fallback for its existing file card", () => {
  const attachment = { contentType: fileMetadataType, name: "Checklist.docx",
    content: { uniqueId: "file-1" } };
  assert.equal(isRepresentedFileAttachment(attachment, [{ id: "file-1" }]), true);
});
test("same filename cannot hide metadata for a different or missing file", () => {
  assert.equal(isRepresentedFileAttachment({ contentType: fileMetadataType,
    name: "Checklist.docx", content: { uniqueId: "file-2" } }, [{ id: "file-1" }]), false);
  assert.equal(isRepresentedFileAttachment({ contentType: fileMetadataType,
    content: { uniqueId: "file-1" } }, []), false);
});
test("unsupported attachments and Adaptive Cards retain their own renderer or fallback", () => {
  for (const contentType of ["application/pdf", "application/vnd.microsoft.card.adaptive"]) {
    assert.equal(isRepresentedFileAttachment({ contentType,
      content: { uniqueId: "file-1" } }, [{ id: "file-1" }]), false);
  }
});
test("malformed native metadata cannot silently disappear", () => {
  for (const content of [undefined, {}, { uniqueId: 1 }]) {
    assert.equal(isRepresentedFileAttachment({ contentType: fileMetadataType, content },
      [{ id: "file-1" }]), false);
  }
});
for (const unsafe of ["javascript:alert(1)", "java\nscript:alert(1)", "data:text/html,test",
  "file:///C:/private.txt", "vbscript:msgbox(1)", "https://name:password@example.com/", ""]) {
  test(`reject unsafe URL ${JSON.stringify(unsafe)}`, () => {
    assert.equal(safeCardUrl(unsafe, origin), undefined);
  });
}
test("normalize allowed links without changing their query", () => {
  assert.equal(safeCardUrl("/api/files/example/content?x=1", origin),
    `${origin}/api/files/example/content?x=1`);
  assert.equal(safeCardUrl("https://example.com/path", origin), "https://example.com/path");
});
test("only raster data images are allowed, never active SVG or HTML", () => {
  assert.equal(safeCardUrl("data:image/png;base64,AAAA", origin, true), "data:image/png;base64,AAAA");
  assert.equal(safeCardUrl("data:image/svg+xml;base64,AAAA", origin, true), undefined);
  assert.equal(safeCardUrl("data:image/png;base64,AAAA", origin), undefined);
});
test("sanitize nested card surfaces without modifying Submit data or the original", () => {
  const data = { url: "javascript:fixture", type: "Image", value: "kept exactly" };
  const content = { type: "AdaptiveCard", version: "1.5", body: [
    { type: "Image", url: "javascript:fixture" },
    { type: "Container", backgroundImage: { url: "data:text/html,fixture" }, items: [
      { type: "TextBlock", text: "unchanged" },
    ] },
  ], actions: [
    { type: "Action.ShowCard", card: { type: "AdaptiveCard", body: [
      { type: "Image", url: "file:///C:/fixture" },
    ] } },
    { type: "Action.Submit", data },
  ] };
  const prepared = prepareCard(content, origin);
  assert.equal(prepared.blockedUrls, 3);
  assert.equal(prepared.content.body[0].url, undefined);
  assert.deepEqual(prepared.content.actions[1].data, data);
  assert.equal(prepared.originalNodes.get(prepared.content.actions[1]), content.actions[1]);
  assert.equal(content.body[0].url, "javascript:fixture");
});
test("preserve entered values across theme changes, including numeric inputs", () => {
  const content = { type: "AdaptiveCard", body: [
    { type: "Input.Text", id: "name", value: "initial" },
    { type: "Input.Number", id: "count", value: 1 },
    { type: "Input.Number", id: "empty", value: 1 },
  ] };
  const prepared = prepareCard(content, origin, new Map([["name", "edited"], ["count", "42"], ["empty", ""]]));
  assert.equal(prepared.content.body[0].value, "edited");
  assert.equal(prepared.content.body[1].value, 42);
  assert.equal(prepared.content.body[2].value, undefined);
});
test("deep malformed cards fail explicitly before native renderer recursion", () => {
  const content = { type: "AdaptiveCard", body: [] };
  let current = content;
  for (let index = 0; index < 45; index++) {
    const child = { type: "Container", items: [] };
    (current.body ?? current.items).push(child);
    current = child;
  }
  assert.throws(() => prepareCard(content, origin), /nesting/);
});
test("wrong card root fails explicitly", () => {
  assert.throws(() => prepareCard({ type: "Unknown" }, origin), /root/);
});
test("newer root and nested card versions render only fallback, without changing originals", () => {
  for (const version of ["1.6", "2.0", "10.0"]) {
    const content = { type: "AdaptiveCard", version, fallbackText: "Version fallback",
      body: [{ type: "TextBlock", text: "Unsupported body" }],
      actions: [{ type: "Action.Submit", title: "Unsupported action" }] };
    const prepared = prepareCard(content, origin).content;
    assert.equal(prepared.version, "1.5");
    assert.equal(prepared.body[0].text, "Version fallback");
    assert.equal(prepared.actions, undefined);
    assert.equal(content.version, version);
    assert.equal(content.body[0].text, "Unsupported body");
    assert.equal(content.actions.length, 1);
    const nested = prepareCard({ type: "AdaptiveCard", version: "1.5", actions: [
      { type: "Action.ShowCard", card: content },
    ] }, origin).content.actions[0].card;
    assert.equal(nested.body[0].text, "Version fallback");
    assert.equal(nested.actions, undefined);
  }
});
test("missing version remains valid, malformed declared versions fail explicitly", () => {
  assert.equal(prepareCard({ type: "AdaptiveCard", body: [] }, origin).content.version, undefined);
  for (const version of ["invalid", "1.6.0", 1.6])
    assert.throws(() => prepareCard({ type: "AdaptiveCard", version }, origin), /version/);
});
