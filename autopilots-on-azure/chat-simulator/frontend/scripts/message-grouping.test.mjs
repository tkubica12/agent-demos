import assert from "node:assert/strict";
import { test } from "node:test";
import { groupsWithPrevious, shouldShowAvatar } from "../src/message-grouping.ts";

const message = (id, createdAt, authorId = "agent") => ({
  id, createdAt, author: { id: authorId },
});

test("channel root posts always retain independent author headers", () => {
  const previous = message("a", "2026-11-10T10:00:00Z");
  const current = message("b", "2026-11-10T10:00:10Z");
  assert.equal(groupsWithPrevious(previous, current, { channel: true }), false);
});

test("same-author replies and direct-chat messages group within one minute", () => {
  const previous = message("a", "2026-11-10T10:00:00Z");
  const current = message("b", "2026-11-10T10:00:10Z");
  assert.equal(groupsWithPrevious(previous, current, { channel: true, thread: true }), true);
  assert.equal(groupsWithPrevious(previous, current, {}), true);
});

test("different authors, dates, and intervals do not group", () => {
  const previous = message("a", "2026-11-10T10:00:00Z");
  assert.equal(groupsWithPrevious(previous, message("b", "2026-11-10T10:00:10Z", "other"), {}), false);
  assert.equal(groupsWithPrevious(previous, message("b", "2026-11-11T10:00:10Z"), {}), false);
  assert.equal(groupsWithPrevious(previous, message("b", "2026-11-10T10:01:00Z"), {}), false);
  assert.equal(groupsWithPrevious(undefined, message("b", "2026-11-10T10:00:10Z"), {}), false);
});

test("avatars remain for people and groups but are omitted for one-to-one agents", () => {
  const agent = { author: { id: "agent", isAgent: true } };
  const person = { author: { id: "person", isAgent: false } };
  assert.equal(shouldShowAvatar(agent, {}), false);
  assert.equal(shouldShowAvatar(person, {}), true);
  assert.equal(shouldShowAvatar(agent, { groupChat: true }), true);
  assert.equal(shouldShowAvatar(agent, { channel: true }), true);
});
