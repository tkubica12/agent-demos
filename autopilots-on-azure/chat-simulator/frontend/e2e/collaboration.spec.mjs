import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { captureOwnerClip } from "./capture_clip.mjs";
import {
  test, expect, api, ok, identity, metric, unique, enc, evidence, url,
  message, teamRow, navigate, send, createTeam, setupTeam, teamMenu, createChat, pickEmoji,
  hostedRun, restartBackend,
} from "./helpers.mjs";

test("C02 three live browsers: owner management and <=5s membership visibility/removal", async ({ people }) => {
  const { owner, member, outsider, events } = people;
  const me = await identity("owner");
  const jana = await identity("member");
  const petr = await identity("outsider");
  const team = await createTeam(owner);
  expect(team.ownerId).toBe(me.id);
  expect(team.channels.map((channel) => channel.name)).toEqual(["General"]);
  await expect(teamRow(outsider, team.id)).toHaveCount(0);
  await teamMenu(owner, team, "Add channel");
  const dialog = owner.getByRole("dialog");
  await dialog.getByRole("textbox", { name: "Name", exact: true }).fill("Browser engineering");
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect((await ok("owner", `/teams/${team.id}`)).channels.map((channel) => channel.name))
    .toContain("Browser engineering");
  await teamMenu(owner, team, "Add member");
  await dialog.getByRole("textbox", { name: "Type a name or email" }).fill(jana.displayName);
  await dialog.getByRole("option").filter({ hasText: jana.displayName }).click();
  const addedAt = Date.now();
  await dialog.getByRole("button", { name: "Add", exact: true }).click();
  await expect(teamRow(member, team.id)).toBeVisible({ timeout: 5_000 });
  const addMs = Date.now() - addedAt;
  expect(addMs).toBeLessThanOrEqual(5_000);
  await teamRow(member, team.id).getByRole("button", { name: `More options for ${team.name}`, exact: true }).click();
  for (const name of ["Add member", "Add channel", "Manage team"]) {
    await expect(member.getByRole("menuitem", { name, exact: true })).toBeDisabled();
  }
  await member.keyboard.press("Escape");
  for (const [resource, method, body] of [
    [`/teams/${team.id}/members`, "POST", { userId: petr.id }],
    [`/teams/${team.id}/channels`, "POST", { name: "Forbidden" }],
    [`/teams/${team.id}/members/${jana.id}`, "DELETE"],
  ]) expect((await api("member", resource, method, body)).status).toBe(403);
  const cid = team.channels[0].conversationId;
  await navigate(owner, cid);
  await navigate(member, cid);
  const root = await send(owner, unique("Membership-private"));
  await expect(message(member, root.id)).toBeVisible();
  await teamMenu(owner, team, "Manage team");
  const removedAt = Date.now();
  await dialog.getByRole("button", { name: `Remove ${jana.displayName}`, exact: true }).click();
  await expect(teamRow(member, team.id)).toHaveCount(0, { timeout: 5_000 });
  await expect(message(member, root.id)).toHaveCount(0);
  const removeMs = Date.now() - removedAt;
  expect(removeMs).toBeLessThanOrEqual(5_000);
  for (const persona of ["member", "outsider"]) {
    for (const resource of [`/teams/${team.id}`, `/teams/${team.id}/members`,
      `/conversations/${enc(cid)}/messages`, `/conversations/${enc(cid)}/files`, `/messages/${root.id}`]) {
      expect([403, 404]).toContain((await api(persona, resource)).status);
    }
  }
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  const after = await send(owner, unique("Removed-members-must-not-receive"));
  await owner.waitForTimeout(300);
  expect(events.filter((event) => ["member", "outsider"].includes(event.persona) && event.messageId === after.id))
    .toEqual([]);
  await metric("c02-membership", { addVisibilityMs: addMs, removeVisibilityMs: removeMs, limitMs: 5_000,
    ...(hostedRun ? { isolatedChromiumContexts: 4, browser: "chrome" } : { isolatedEdgeContexts: 4 }),
    requiredSimultaneousPersonas: ["owner", "member", "outsider"],
    ownerOnlyUiAndApi: true, removedUiCleared: true });
});

test("C02 team creation waits for the real API acknowledgment before checking dialog closure", async ({ people }) => {
  const { owner } = people;
  const delayMs = 5_500;
  let acknowledgment;
  const delayedCreate = async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    acknowledgment = (async () => {
      const response = await route.fetch();
      expect(response.status()).toBe(201);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      await route.fulfill({ response });
    })();
    await acknowledgment;
  };
  await owner.route("**/api/teams", delayedCreate);
  try {
    const started = Date.now();
    const team = await createTeam(owner, unique("Delayed acknowledgment"));
    expect(Date.now() - started).toBeGreaterThanOrEqual(delayMs);
    expect(team.channels.map((channel) => channel.name)).toEqual(["General"]);
    await metric("c02-create-acknowledgment", { delayedResponseMs: delayMs,
      realTeamCreated: true, dialogClosedAfterAcknowledgment: true });
  } finally {
    await acknowledgment;
    await owner.unroute("**/api/teams", delayedCreate);
  }
});

test("C04 direct first-send creates/reuses exactly one personal chat and delivers only to its intended member", async ({ people }) => {
  const { owner, member, outsider, admin, events } = people;
  const ownerId = (await identity("owner")).id;
  const recipient = await identity("member");
  const pair = [ownerId, recipient.id].sort();
  const personalChats = async () => (await ok("owner", "/chats")).filter((chat) =>
    chat.conversationType === "personal" && JSON.stringify([...chat.memberIds].sort()) === JSON.stringify(pair));
  expect(await personalChats(), "No personal chat exists before the first direct-send scenario").toHaveLength(0);
  const sent = [];
  let conversationId;
  for (const stage of ["create", "reuse"]) {
    await owner.locator(".sidebar").getByRole("button", { name: "New message", exact: true }).click();
    const composer = owner.getByTestId("composer");
    const text = unique(`Direct first draft ${stage}`);
    await composer.getByRole("textbox", { name: "Type a message", exact: true }).fill(text);
    await expect(composer.getByRole("button", { name: "Send message", exact: true })).toBeDisabled();
    await owner.getByRole("textbox", { name: "To: participants" }).fill(recipient.displayName);
    await owner.getByRole("option").filter({ hasText: recipient.displayName }).click();
    await expect(owner.getByRole("button", { name: "Start conversation", exact: true })).toBeVisible();
    const created = owner.waitForResponse((response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname === "/api/chats");
    // Send through the genuine new-chat Composer, without clicking Start conversation.
    const draft = await send(owner, text, "Type a message");
    const chatResponse = await created;
    expect(chatResponse.status()).toBe(201);
    const chat = await chatResponse.json();
    expect([...chat.memberIds].sort()).toEqual(pair);
    expect(chat.conversationType).toBe("personal");
    expect(draft.conversationId).toBe(chat.conversationId);
    if (conversationId) expect(chat.conversationId).toBe(conversationId);
    conversationId = chat.conversationId;
    await expect(message(owner, draft.id)).toContainText(text);
    await expect(member.locator(`[data-conversation-id="${conversationId}"]`)).toBeVisible();
    if (stage === "create") await navigate(member, conversationId);
    await expect(message(member, draft.id)).toContainText(text);
    await expect(outsider.locator(`[data-conversation-id="${conversationId}"]`)).toHaveCount(0);
    await expect(admin.locator(`[data-conversation-id="${conversationId}"]`)).toHaveCount(0);
    const chats = await personalChats();
    expect(chats).toHaveLength(1);
    expect(chats[0].id).toBe(chat.id);
    const persisted = await ok("member", `/conversations/${enc(conversationId)}/messages`);
    expect(persisted.filter((item) => item.text === text).map((item) => item.id)).toEqual([draft.id]);
    sent.push({ stage, chatId: chat.id, messageId: draft.id, text });
  }
  await owner.waitForTimeout(300);
  expect(events.filter((event) => ["outsider", "admin"].includes(event.persona) &&
    sent.some((item) => item.messageId === event.messageId))).toEqual([]);
  expect((await api("outsider", `/conversations/${enc(conversationId)}/messages`)).status).toBe(403);
  await metric("c04-direct-first-send", { conversationId, sent, personalChatsForPair: 1,
    draftBeforeRecipientSelection: true, requiredRecipientBeforeSend: true,
    path: "New message > type draft > select member > Send message (no Start conversation click)",
    intendedMemberDelivered: true, unintendedUsersReceived: 0 });
});

test("C03 browser sockets: outsider cannot receive private channel/chat events", async ({ people }) => {
  const { owner, member, outsider, events } = people;
  const { team, cid } = await setupTeam(owner, member);
  const chat = await createChat(owner, [await identity("member")]);
  await expect(member.locator(`[data-conversation-id="${chat.conversationId}"]`)).toBeVisible();
  await expect(outsider.locator(`[data-conversation-id="${chat.conversationId}"]`)).toHaveCount(0);
  const sentChat = await send(owner, unique("Private personal socket"), "Type a message");
  await navigate(owner, cid);
  const sentChannel = await send(owner, unique("Private channel socket"));
  await expect(message(member, sentChannel.id)).toBeVisible();
  await owner.waitForTimeout(300);
  expect(events.filter((event) => event.persona === "outsider" &&
    [sentChat.id, sentChannel.id].includes(event.messageId))).toEqual([]);
  for (const resource of [`/teams/${team.id}`, `/chats/${chat.id}`, `/messages/${sentChat.id}`,
    `/messages/${sentChannel.id}`, `/conversations/${enc(chat.conversationId)}/messages`]) {
    expect([403, 404]).toContain((await api("outsider", resource)).status);
  }
  await metric("c03-browser-isolation", { outsiderPrivateEvents: 0, channelMessage: sentChannel.id,
    chatMessage: sentChat.id, openApiMatrix: hostedRun
      ? "Actual hosted API and genuine Entra actors; no local pytest result claimed."
      : "Also executed by tests/test_collaboration.py in the same runner" });
});

test("C04 channel/thread, 1:1/group>=3, formatting, edit/delete, 20 UI deliveries and real restart", async ({ people }) => {
  const { owner, member, admin } = people;
  const { cid } = await setupTeam(owner, member);
  const root = await send(owner, "**Browser bold** _Browser italic_ `browser-code` [Browser link](https://example.com)\n\n- Browser bullet");
  const row = message(member, root.id);
  await expect(row.locator("strong")).toHaveText("Browser bold");
  await expect(row.locator("em")).toHaveText("Browser italic");
  await expect(row.locator("code")).toHaveText("browser-code");
  await expect(row.getByRole("link", { name: "Browser link" })).toHaveAttribute("href", "https://example.com");
  await expect(row.locator("li")).toHaveText("Browser bullet");
  await message(owner, root.id).locator(".message-content").hover();
  await message(owner, root.id).getByRole("button", { name: "Reply in thread", exact: true }).click();
  const reply = await send(owner, "Browser thread reply", "Reply in thread");
  await expect(message(owner, reply.id)).toBeVisible();
  await expect(row.getByRole("button", { name: "Open 1 replies" })).toBeVisible();
  await row.getByRole("button", { name: "Open 1 replies" }).click();
  await expect(message(member, reply.id)).toBeVisible();
  const editedRow = message(owner, reply.id);
  await editedRow.locator(".message-content").hover();
  await editedRow.getByRole("button", { name: "Message more options" }).click();
  await owner.getByRole("menuitem", { name: "Edit", exact: true }).click();
  await editedRow.getByRole("textbox", { name: "Edit message" }).fill("Browser thread reply edited");
  await editedRow.getByRole("button", { name: "Save edit", exact: true }).click();
  await expect(message(member, reply.id)).toContainText("Browser thread reply edited");
  expect((await api("member", `/messages/${reply.id}`, "PATCH", { text: "forbidden edit" })).status).toBe(403);
  expect((await api("member", `/messages/${reply.id}`, "DELETE")).status).toBe(403);
  await owner.getByRole("button", { name: "Close thread", exact: true }).click();
  await member.getByRole("button", { name: "Close thread", exact: true }).click();
  const deleted = await send(owner, "Delete this own browser message");
  await message(owner, deleted.id).locator(".message-content").hover();
  await message(owner, deleted.id).getByRole("button", { name: "Message more options" }).click();
  await owner.getByRole("menuitem", { name: "Delete", exact: true }).click();
  await expect(message(member, deleted.id)).toContainText("This message has been deleted.");
  const chat = await createChat(owner, [await identity("member")]);
  expect(chat.conversationType).toBe("personal");
  await expect(member.locator(`[data-conversation-id="${chat.conversationId}"]`)).toBeVisible();
  await navigate(member, chat.conversationId);
  const personal = await send(owner, "Persistent personal browser chat", "Type a message");
  await expect(message(member, personal.id)).toBeVisible();
  const group = await createChat(owner, [await identity("member"), await identity("admin")], unique("Browser group"));
  expect(group.conversationType).toBe("groupChat");
  expect(group.memberIds).toHaveLength(3);
  await expect(member.locator(`[data-conversation-id="${group.conversationId}"]`)).toBeVisible();
  await expect(admin.locator(`[data-conversation-id="${group.conversationId}"]`)).toBeVisible();
  await navigate(member, group.conversationId);
  await navigate(admin, group.conversationId);
  const groupMessage = await send(owner, "Three participant browser group", "Type a message");
  await expect(message(member, groupMessage.id)).toBeVisible();
  await expect(message(admin, groupMessage.id)).toBeVisible();
  const samples = [];
  const prefix = unique("UI-order");
  for (let index = 0; index < 20; index++) {
    const text = `${prefix}-${String(index).padStart(2, "0")}`;
    const started = Date.now();
    const sent = await send(owner, text, "Type a message");
    await expect(message(member, sent.id)).toContainText(text, { timeout: 2_000 });
    samples.push({ index, messageId: sent.id, elapsedMs: Date.now() - started, text });
  }
  const received = await member.locator("article[data-message-id]").evaluateAll((rows, prefix) =>
    rows.filter((row) => row.textContent.includes(prefix)).map((row) => row.dataset.messageId), prefix);
  expect(received).toEqual(samples.map((sample) => sample.messageId));
  const p95Ms = samples.map((sample) => sample.elapsedMs).sort((a, b) => a - b)[Math.ceil(samples.length * .95) - 1];
  await metric("c04-realtime", { samples, p95Ms, limitMs: 2_000, sent: 20, received: received.length,
    lost: 20 - received.length, inOrder: true, measurement: "UI fill/click through DOM rendering in already-open B" });
  expect(p95Ms).toBeLessThanOrEqual(2_000);
  await restartBackend();
  for (const page of [owner, member, admin]) {
    await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready", { timeout: 15_000 });
    await page.reload();
    await expect(page.getByTestId("authenticated")).toHaveAttribute("data-connection", "ready");
    await expect(message(page, groupMessage.id)).toBeVisible();
  }
  await navigate(member, cid);
  await message(member, root.id).getByRole("button", { name: "Open 1 replies" }).click();
  await expect(message(member, reply.id)).toContainText("Browser thread reply edited");
  await expect(message(member, deleted.id)).toContainText("This message has been deleted.");
  await member.getByRole("button", { name: "Close thread", exact: true }).click();
  await navigate(member, chat.conversationId);
  await expect(message(member, personal.id)).toBeVisible();
  await navigate(member, group.conversationId);
  for (const sample of samples) await expect(message(member, sample.messageId)).toContainText(sample.text);
  await metric("c04-persistence", { ownedBackendProcessRestarted: true,
    ...(hostedRun ? { realHostedGuestRestarted: true, reloadsWithRealEntra: 3 } : { reloadsWithSyntheticPersona: 3 }),
    checked: ["channel", "thread reply", "edit", "delete tombstone", "personal", "group", "all 20 ordered messages"] });
});

test("C05 Unicode picker, six required/arbitrary reactions, live names/counts/toggle and current-member mentions", async ({ people }) => {
  const { owner, member, outsider } = people;
  const ownerName = (await identity("owner")).displayName;
  const memberName = (await identity("member")).displayName;
  const outsiderName = (await identity("outsider")).displayName;
  const { cid, team } = await setupTeam(owner, member);
  const clip = captureOwnerClip(owner);
  const composer = owner.getByTestId("composer");
  await pickEmoji(owner, composer.getByRole("button", { name: "Emoji", exact: true }), "🔥");
  await expect(composer.getByRole("textbox")).toHaveValue("🔥");
  const response = owner.waitForResponse((response) =>
    response.request().method() === "POST" && /\/messages$/.test(new URL(response.url()).pathname));
  await composer.getByRole("button", { name: "Send message", exact: true }).click();
  const emojiMessage = await (await response).json();
  expect(emojiMessage.text).toBe("🔥");
  await expect(message(member, emojiMessage.id).locator('img[alt="🔥"]')).toBeVisible();
  const reactions = ["👍", "❤️", "😆", "😮", "😢", "😠", "🦊"];
  for (const emoji of reactions) {
    const a = message(owner, emojiMessage.id);
    const b = message(member, emojiMessage.id);
    await a.locator(".message-content").hover();
    await pickEmoji(owner, a.getByRole("button", { name: "More reactions", exact: true }), emoji, true);
    await expect(b.getByTestId("reaction-pills").getByRole("button", { name: `${emoji} 1: ${ownerName}`, exact: true }))
      .toBeVisible();
    await b.getByTestId("reaction-pills").getByRole("button", { name: `${emoji} 1: ${ownerName}`, exact: true }).click();
    const both = new RegExp(`^${emoji.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} 2: `);
    const ownerPill = a.getByTestId("reaction-pills").getByRole("button", { name: both });
    await expect(ownerPill).toBeVisible();
    const reactorTitle = await ownerPill.getAttribute("title");
    expect(reactorTitle).toContain(ownerName);
    expect(reactorTitle).toContain(memberName);
    await ownerPill.click();
    const remaining = b.getByTestId("reaction-pills").getByRole("button", { name: `${emoji} 1: ${memberName}`, exact: true });
    await expect(remaining).toBeVisible();
    await remaining.click();
    await expect(a.getByTestId("reaction-pills")).toHaveCount(0);
  }
  await composer.getByRole("textbox").fill("@");
  const suggestions = owner.getByRole("listbox", { name: "Mention a member" });
  await expect(suggestions.getByRole("option", { name: memberName, exact: true })).toBeVisible();
  await expect(suggestions.getByRole("option", { name: outsiderName, exact: true })).toHaveCount(0);
  await suggestions.getByRole("option", { name: memberName, exact: true }).click();
  await expect(composer.getByRole("textbox")).toHaveValue(`<at>${memberName}</at> `);
  const mentionResponse = owner.waitForResponse((response) =>
    response.request().method() === "POST" && /\/messages$/.test(new URL(response.url()).pathname));
  await composer.getByRole("button", { name: "Send message", exact: true }).click();
  const mentionMessage = await (await mentionResponse).json();
  expect(mentionMessage.mentions).toEqual([{ userId: (await identity("member")).id, displayName: memberName }]);
  await expect(message(member, mentionMessage.id).locator(".mention")).toHaveText(memberName);
  await expect(member.getByRole("button", { name: "Activity", exact: true }).locator(".notification-dot")).toBeVisible();
  await member.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(member.getByRole("button").filter({ hasText: `${ownerName} mentioned you` })).toBeVisible();
  await expect(outsider.getByRole("button", { name: "Activity", exact: true }).locator(".notification-dot")).toHaveCount(0);
  await ok("owner", `/teams/${team.id}/members/${(await identity("member")).id}`, "DELETE");
  await expect(teamRow(member, team.id)).toHaveCount(0);
  await composer.getByRole("textbox").fill("@");
  await expect(owner.getByRole("listbox", { name: "Mention a member" })
    .getByRole("option", { name: memberName, exact: true })).toHaveCount(0);
  await metric("c05-emoji-mentions", { conversationId: cid, unicodeInserted: "🔥", reactions,
    addedAndRemovedInTwoBrowsers: true, countsAndReactorNames: true, recipientIndicator: true,
    autocompleteExcludesOutsiderAndRemovedMember: true });
  await clip;
});

test("C06 channel/chat 25MB boundary, oversized rejection, cards/Shared, downloaded hashes and outsider denial", async ({ people }) => {
  const { owner, member } = people;
  const { cid } = await setupTeam(owner, member);
  const chat = await createChat(owner, [await identity("member")]);
  await expect(member.locator(`[data-conversation-id="${chat.conversationId}"]`)).toBeVisible();
  const results = [];
  const bytes = Buffer.alloc(25 * 1024 * 1024);
  for (let offset = 0; offset < bytes.length; offset++) bytes[offset] = offset % 251;
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  for (const [kind, conversationId] of [["channel", cid], ["chat", chat.conversationId]]) {
    await navigate(owner, conversationId);
    await navigate(member, conversationId);
    const name = unique(`boundary-${kind}`) + ".bin";
    const composer = owner.getByTestId("composer");
    const uploaded = owner.waitForResponse((response) => response.request().method() === "POST" &&
      /\/conversations\/[^/]+\/files$/.test(new URL(response.url()).pathname));
    await composer.getByLabel("Upload attachment").setInputFiles({ name, mimeType: "application/octet-stream", buffer: bytes });
    const upload = await uploaded;
    expect(upload.status()).toBe(201);
    // Edge evicts CDP response bodies when the preceding upload is 25 MB.
    // Obtain persisted metadata through the real authenticated API, not an inspector cache.
    const file = (await ok("owner", `/conversations/${enc(conversationId)}/files`)).find((item) => item.name === name);
    expect(file).toBeTruthy();
    expect(file.size).toBe(bytes.length);
    expect(file.sha256).toBe(sha256);
    await expect(composer.locator(".pending-attachments")).toContainText(name);
    const sentResponse = owner.waitForResponse((response) => response.request().method() === "POST" &&
      /\/conversations\/[^/]+\/messages$/.test(new URL(response.url()).pathname));
    await composer.getByRole("button", { name: "Send message", exact: true }).click();
    const sent = await (await sentResponse).json();
    const card = message(member, sent.id).getByTestId("file-card");
    await expect(card).toContainText(name);
    const downloaded = member.waitForEvent("download");
    await card.getByRole("button", { name: `Download attachment ${name}`, exact: true }).click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe(name);
    const actual = await readFile(await download.path());
    expect(createHash("sha256").update(actual).digest("hex")).toBe(sha256);
    const content = await api("member", `/files/${file.id}/content`);
    expect(content.status).toBe(200);
    expect(content.headers["x-content-type-options"]).toBe("nosniff");
    expect(content.headers["content-disposition"]).toMatch(/^attachment;/);
    expect(createHash("sha256").update(content.body).digest("hex")).toBe(sha256);
    expect((await api("outsider", `/files/${file.id}/content`)).status).toBe(403);
    expect((await api("outsider", `/files/${file.id}`)).status).toBe(403);
    await member.getByRole("button", { name: "Shared", exact: true }).click();
    const sharedRow = member.locator(".file-list-row").filter({ hasText: name });
    // A Shared defect must still fail C06, without suppressing the second upload and size-limit checks.
    await expect.soft(sharedRow, `${kind}: already-open member sees uploaded file in Shared`).toBeVisible();
    const sharedVisible = await sharedRow.isVisible();
    expect((await ok("member", `/conversations/${enc(conversationId)}/files`)).some((item) => item.id === file.id)).toBe(true);
    results.push({ kind, bytes: bytes.length, sha256, fileId: file.id, uiDownload: true,
      sharedFiles: sharedVisible, nosniff: true, attachment: true, outsiderStatus: 403 });
  }
  const tooLarge = Buffer.alloc(25 * 1024 * 1024 + 1, 120);
  await owner.getByTestId("composer").getByLabel("Upload attachment").setInputFiles({
    name: "too-large.bin", mimeType: "application/octet-stream", buffer: tooLarge,
  });
  await expect(owner.getByRole("alert")).toContainText("25 MB or smaller");
  const form = new FormData();
  form.append("file", new Blob([tooLarge]), "too-large-api.bin");
  const rejected = await api("owner", `/conversations/${enc(cid)}/files`, "POST", undefined, form);
  expect(rejected.status).toBe(413);
  expect(rejected.body.detail).toMatch(/25 MB/);
  await metric("c06-files", { uploads: results, limitBytes: bytes.length, oversizedBytes: tooLarge.length,
    uiIntelligibleRejection: true, apiOversizedStatus: rejected.status });
});

test("C06 Shared table uploads post attachment messages, realtime rows, keyword filtering and byte-identical UI downloads", async ({ people }) => {
  const { owner, member, outsider, events } = people;
  const { cid } = await setupTeam(owner, member);
  const chat = await createChat(owner, [await identity("member")]);
  const ownerId = (await identity("owner")).id;
  const results = [];
  for (const [kind, conversationId] of [["channel", cid], ["chat", chat.conversationId]]) {
    await navigate(owner, conversationId);
    await navigate(member, conversationId);
    for (const page of [owner, member]) await page.getByRole("button", { name: "Shared", exact: true }).click();
    const shared = member.getByRole("region", { name: "Shared files", exact: true });
    await expect(shared.getByRole("table").getByRole("columnheader", { name: "Name", exact: true })).toBeVisible();
    const keyword = unique(`shared-keyword-${kind}`);
    const uploads = [];
    for (const [index, name] of [[0, `${keyword}.txt`], [1, `${unique(`different-${kind}`)}.bin`]]) {
      const bytes = Buffer.from(`Shared upload ${kind}/${index}: Žluťoučký kůň 🔥\n${keyword}\n`);
      const sha256 = createHash("sha256").update(bytes).digest("hex");
      const uploaded = owner.waitForResponse((response) => response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/conversations/${enc(conversationId)}/files`);
      const posted = owner.waitForResponse((response) => response.request().method() === "POST" &&
        new URL(response.url()).pathname === `/api/conversations/${enc(conversationId)}/messages`);
      const started = Date.now();
      await owner.getByRole("region", { name: "Shared files", exact: true }).getByLabel("Upload shared file")
        .setInputFiles({ name, mimeType: "application/octet-stream", buffer: bytes });
      const uploadResponse = await uploaded;
      expect(uploadResponse.status()).toBe(201);
      const file = await uploadResponse.json();
      const messageResponse = await posted;
      expect(messageResponse.status()).toBe(201);
      const attachment = await messageResponse.json();
      expect(attachment.author.id).toBe(ownerId);
      expect(attachment.conversationId).toBe(conversationId);
      expect(attachment.attachments.map((item) => item.id)).toEqual([file.id]);
      expect(file.name).toBe(name);
      expect(file.sha256).toBe(sha256);
      const row = shared.getByRole("row").filter({ has: member.getByRole("button", { name, exact: true }) });
      await expect(row).toBeVisible();
      await expect(row.locator("time")).toHaveAttribute("datetime", attachment.createdAt);
      const visibilityMs = Date.now() - started;
      const persisted = await ok("member", `/conversations/${enc(conversationId)}/messages`);
      expect(persisted.filter((item) => item.attachments.some((item) => item.id === file.id))
        .map((item) => item.id)).toEqual([attachment.id]);
      uploads.push({ name, fileId: file.id, messageId: attachment.id, sha256, bytes: bytes.length, visibilityMs });
    }
    const filter = shared.getByRole("textbox", { name: "Filter by keyword", exact: true });
    await filter.fill(keyword.toUpperCase());
    await expect(shared.locator("tbody tr")).toHaveCount(1);
    await expect(shared.getByRole("button", { name: uploads[0].name, exact: true })).toBeVisible();
    await expect(shared.getByRole("button", { name: uploads[1].name, exact: true })).toHaveCount(0);
    await filter.fill(unique("no-matching-file"));
    await expect(shared.locator("tbody tr")).toHaveCount(0);
    await expect(shared.getByText("No files match your search.", { exact: true })).toBeVisible();
    await filter.fill(keyword);
    const downloaded = member.waitForEvent("download");
    await shared.getByRole("button", { name: uploads[0].name, exact: true }).click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe(uploads[0].name);
    expect(createHash("sha256").update(await readFile(await download.path())).digest("hex")).toBe(uploads[0].sha256);
    await filter.fill("");
    for (const upload of uploads) {
      await expect(shared.getByRole("button", { name: upload.name, exact: true })).toBeVisible();
      expect((await api("outsider", `/files/${upload.fileId}/content`)).status).toBe(403);
      expect((await api("outsider", `/messages/${upload.messageId}`)).status).toBe(403);
    }
    await member.getByRole("navigation", { name: "Conversation tabs" })
      .getByRole("button", { name: kind === "channel" ? "Conversation" : "Chat", exact: true }).click();
    for (const upload of uploads) {
      await expect(message(member, upload.messageId).getByTestId("file-card")).toContainText(upload.name);
      expect(events.some((event) => event.persona === "member" && event.type === "message.created" &&
        event.messageId === upload.messageId)).toBe(true);
    }
    expect(events.filter((event) => event.persona === "member" && event.type === "file.created" &&
      event.conversationId === conversationId).length).toBeGreaterThanOrEqual(2);
    expect(events.filter((event) => event.persona === "outsider" && event.type === "file.created" &&
      event.conversationId === conversationId)).toEqual([]);
    await expect(outsider.locator(`[data-conversation-id="${conversationId}"]`)).toHaveCount(0);
    results.push({ kind, conversationId, uploads, alreadyOpenMemberRealtime: true,
      searchCaseInsensitive: true, noMatchAndClear: true, uiDownloadHashMatched: true, outsiderDenied: true });
  }
  await metric("c06-shared-ui", { results, authenticatedUiUploads: 4, automaticallyPostedAttachmentMessages: 4 });
});

test("C07 five payloads in actual message/team/file UI never execute; CSP and safe SVG download", async ({ people }) => {
  const { owner } = people;
  const payloads = [
    "<script>console.log('TSIM_XSS_EXECUTED');alert(1)</script>",
    '<img src=x onerror="console.log(\'TSIM_XSS_EXECUTED\');alert(1)">',
    "[open](javascript:alert('TSIM_XSS_EXECUTED'))",
    '<svg onload="console.log(\'TSIM_XSS_EXECUTED\');alert(1)">',
    '[link](https://example.test/" onmouseover="alert(\'TSIM_XSS_EXECUTED\'))',
  ];
  const results = [];
  for (const payload of payloads) {
    await owner.getByRole("button", { name: "See all your teams", exact: true }).click();
    const team = await createTeam(owner, payload);
    const cid = team.channels[0].conversationId;
    await navigate(owner, cid);
    await expect(teamRow(owner, team.id).locator(".team-name")).toContainText(payload);
    const sent = await send(owner, payload);
    await expect(message(owner, sent.id)).toBeVisible();
    await message(owner, sent.id).locator(".message-content").hover();
    expect(await message(owner, sent.id).locator("script, svg[onload], img[onerror], [onclick], a[href^='javascript:']").count())
      .toBe(0);
    const name = `${payload}.svg`;
    const composer = owner.getByTestId("composer");
    const response = owner.waitForResponse((response) => response.request().method() === "POST" &&
      /\/conversations\/[^/]+\/files$/.test(new URL(response.url()).pathname));
    await composer.getByLabel("Upload attachment").setInputFiles({
      name, mimeType: "image/svg+xml", buffer: Buffer.from(payload),
    });
    const uploaded = await response;
    expect(uploaded.status()).toBe(201);
    const file = await uploaded.json();
    const posted = owner.waitForResponse((response) => response.request().method() === "POST" &&
      /\/conversations\/[^/]+\/messages$/.test(new URL(response.url()).pathname));
    await composer.getByRole("button", { name: "Send message", exact: true }).click();
    const attachmentMessage = await (await posted).json();
    const card = message(owner, attachmentMessage.id).getByTestId("file-card");
    await expect(card).toContainText(file.name);
    await card.hover();
    const downloaded = owner.waitForEvent("download");
    await card.getByRole("button", { name: `Download attachment ${file.name}`, exact: true }).click();
    const download = await downloaded;
    expect((await readFile(await download.path())).toString()).toBe(payload);
    const content = await api("owner", `/files/${file.id}/content`);
    expect(content.headers["content-type"]).toBe("application/octet-stream");
    expect(content.headers["content-disposition"]).toMatch(/^attachment;/);
    expect(content.headers["x-content-type-options"]).toBe("nosniff");
    results.push({ payload, teamId: team.id, messageId: sent.id, fileId: file.id,
      fileName: file.name, fileDownloadedNotExecuted: true });
  }
  const root = await owner.request.get("/");
  expect(root.headers()["content-security-policy"]).toContain("script-src 'self'");
  expect(root.headers()["content-security-policy"]).toContain("object-src 'none'");
  await owner.waitForTimeout(300);
  expect(people.problems).toEqual([]);
  await metric("c07-browser-xss", { payloads: results, surfacesPerPayload: ["team name", "message", "filename/SVG content"],
    observedDialogs: 0, executionConsoleMarkers: 0, csp: root.headers()["content-security-policy"],
    idpGuards: "tests/test_auth.py::test_real_auth_default_and_production_guards executed by runner",
    excludedChecks: ["dependency audit and repository secret scan are parent-owned"] });
});

test("C08 export ALL E2E messages/replies/edits and 100% locked Activity model/shape validation", async () => {
  // Owner created every conversation in this isolated DB and retains membership in all of them.
  const exported = await ok("owner", "/activities/export");
  await writeFile(path.join(evidence, "activities-export.json"), JSON.stringify(exported, null, 2));
  const root = path.resolve(import.meta.dirname, "..", "..");
  const args = ["run", "--locked", "python", path.join(root, "frontend", "e2e", "validate_activity.py")];
  const validation = spawnSync("uv", args, {
    cwd: root, env: process.env, encoding: "utf8", windowsHide: true,
  });
  await metric("activity-validator-command", { command: "uv", args, cwd: root, exitCode: validation.status,
    requiredEnvironment: { TSIM_E2E_EVIDENCE: evidence, TSIM_E2E_DATABASE: process.env.TSIM_E2E_DATABASE } });
  await writeFile(path.join(evidence, "activity-validator-output.txt"), validation.stdout + validation.stderr);
  expect(validation.error?.message, "Activity SDK validator launch").toBeUndefined();
  expect(validation.status, `Activity SDK validation:\n${validation.stdout}${validation.stderr}`).toBe(0);
  const result = JSON.parse(await readFile(path.join(evidence, "activity-validation.json"), "utf8"));
  expect(result.coveragePercent).toBe(100);
  expect(result.validatedRevisions).toBe(exported.length);
  expect(result.totalDatabaseRevisions).toBe(exported.length);
});
