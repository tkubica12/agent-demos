import assert from "node:assert/strict";
import test from "node:test";
import { isFileReply } from "./file_acceptance.mjs";

test("accepts MEDIA in a separate message without requiring digest prose on it", () => {
  const refusal = /original bytes not available/i;
  const messages = [
    { text: "Input SHA-256: abc", attachments: [] },
    { text: "", attachments: [{ name: "result.txt" }] },
  ];
  assert.equal(isFileReply(messages[0], "result.txt", refusal), false);
  assert.equal(isFileReply(messages[1], "result.txt", refusal), true);
  assert.equal(isFileReply({ text: "", attachments: [{ name: "source.txt" }] }, "result.txt", refusal), false);
  assert.equal(isFileReply({ text: "Original bytes not available", attachments: [] }, "result.txt", refusal), true);
});
