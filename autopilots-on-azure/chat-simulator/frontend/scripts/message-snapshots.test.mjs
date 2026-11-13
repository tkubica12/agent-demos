import assert from "node:assert/strict";
import { test } from "node:test";
import { mergeMessageSnapshots, newestMessageSnapshot } from "../src/message-snapshots.ts";

const message = (id, snapshotRevision, changes = {}) => ({
  id, snapshotRevision, createdAt: "2026-11-08T10:00:00Z", updatedAt: null,
  reactions: [], deleted: false, ...changes,
});

test("a stale create response cannot remove a newer realtime native reaction", () => {
  const created = message("a", 1);
  const reacted = message("a", 2, { reactions: [{ emoji: "👍", userIds: ["agent"] }] });
  assert.equal(newestMessageSnapshot(reacted, created), reacted);
  assert.deepEqual(mergeMessageSnapshots([reacted], [created]), [reacted]);
  assert.equal(reacted.updatedAt, null);
});

test("fetched history and Activity history preserve the highest revision", () => {
  const current = message("a", 3);
  const stale = message("a", 2);
  const fresh = message("b", 4);
  assert.deepEqual(mergeMessageSnapshots([current], [stale, fresh]), [current, fresh]);
  assert.deepEqual(mergeMessageSnapshots([stale], [current, fresh]), [current, fresh]);
});

test("card refresh, suggestion consumption and deletion do not require edit timestamps", () => {
  const original = message("a", 0, { activityAttachments: [{ content: { version: "1.5" } }] });
  const refreshed = message("a", 1, { suggestedActions: null });
  const deleted = message("a", 2, { deleted: true });
  assert.equal(newestMessageSnapshot(original, refreshed), refreshed);
  assert.equal(newestMessageSnapshot(deleted, refreshed), deleted);
});

test("equal snapshots are idempotent and incoming-only messages are retained", () => {
  const current = message("a", 5);
  assert.equal(newestMessageSnapshot(current, { ...current }), current);
  assert.equal(newestMessageSnapshot(undefined, current), current);
  assert.deepEqual(mergeMessageSnapshots([current], [{ ...current }]), [current]);
});

test("revisions are compared only within one message", () => {
  assert.throws(() => newestMessageSnapshot(message("a", 99), message("b", 1)), /different messages/);
  assert.deepEqual(mergeMessageSnapshots([message("b", 1)], [message("a", 99)]).map(item => item.id), ["a", "b"]);
});
