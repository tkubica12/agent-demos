import type { ActivityAttachment, FileItem } from "./api";

export const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export function isRepresentedFileAttachment(attachment: ActivityAttachment,
  files: readonly Pick<FileItem, "id">[]): boolean {
  if (attachment.contentType !== "application/vnd.microsoft.teams.file.download.info") return false;
  const id = attachment.content?.uniqueId;
  return typeof id === "string" && files.some(file => file.id === id);
}

export function safeCardUrl(value: unknown, origin: string, image = false): string | undefined {
  if (typeof value !== "string" || !value.trim()) return undefined;
  if (image && /^data:image\/(?:png|jpeg|gif|webp);base64,[a-z\d+/=\s]+$/i.test(value)) return value;
  try {
    const url = new URL(value, origin);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return undefined;
    return url.href;
  } catch (error) {
    if (error instanceof TypeError) return undefined;
    throw error;
  }
}

export function prepareCard(content: Record<string, unknown>, origin: string,
  inputValues?: ReadonlyMap<string, string>): {
    content: Record<string, unknown>; blockedUrls: number;
    originalNodes: WeakMap<Record<string, unknown>, Record<string, unknown>>;
  } {
  const copy: unknown = structuredClone(content);
  if (!isObject(copy) || copy.type !== "AdaptiveCard") throw new Error("Invalid Adaptive Card root.");
  let blockedUrls = 0;
  let count = 0;
  const originalNodes = new WeakMap<Record<string, unknown>, Record<string, unknown>>();
  function visit(value: unknown, depth: number, original: unknown) {
    if (!isObject(value)) return;
    const node = value;
    if (isObject(original)) originalNodes.set(node, original);
    if (depth > 40 || ++count > 2000) throw new Error("Adaptive Card nesting or element limit exceeded.");
    const type = typeof value.type === "string" ? value.type : "";
    if (type === "AdaptiveCard" && node.version !== undefined) {
      const version = typeof node.version === "string" ? /^(\d+)\.(\d+)$/.exec(node.version) : null;
      if (!version) throw new Error("Invalid Adaptive Card version.");
      if (Number(version[1]) > 1 || (Number(version[1]) === 1 && Number(version[2]) > 5)) {
        node.version = "1.5";
        node.body = [{ type: "TextBlock", wrap: true, text: typeof node.fallbackText === "string" && node.fallbackText.trim()
          ? node.fallbackText : "This card version is not supported by the simulator." }];
        delete node.actions;
        delete node.refresh;
        delete node.selectAction;
      }
    }
    function checkUrl(key: string, image: boolean) {
      if (node[key] === undefined) return;
      const safe = safeCardUrl(node[key], origin, image);
      if (safe) node[key] = safe;
      else { delete node[key]; blockedUrls++; }
    }
    if (type === "Image" || type === "Action.OpenUrl") checkUrl("url", type === "Image");
    if (type.startsWith("Action.")) checkUrl("iconUrl", true);
    if (value.backgroundImage !== undefined) {
      if (isObject(value.backgroundImage)) {
        const safe = safeCardUrl(value.backgroundImage.url, origin, true);
        if (safe) value.backgroundImage.url = safe;
        else { delete value.backgroundImage; blockedUrls++; }
      } else checkUrl("backgroundImage", true);
    }
    if (type === "Media" && Array.isArray(value.sources)) {
      value.sources = value.sources.filter((source: unknown) => {
        if (!isObject(source)) { blockedUrls++; return false; }
        const safe = safeCardUrl(source.url, origin);
        if (!safe) { blockedUrls++; return false; }
        source.url = safe;
        return true;
      });
      checkUrl("poster", true);
    }
    if (type.startsWith("Input.") && typeof value.id === "string" && inputValues?.has(value.id)) {
      const saved = inputValues.get(value.id)!;
      if (type === "Input.Number") {
        if (saved.trim() && Number.isFinite(Number(saved))) value.value = Number(saved);
        else delete value.value;
      } else value.value = saved;
    }
    for (const key of ["body", "items", "columns", "images", "actions", "rows", "cells"]) {
      const children = value[key];
      const originals = isObject(original) ? original[key] : undefined;
      if (Array.isArray(children)) children.forEach((item: unknown, index: number) =>
        visit(item, depth + 1, Array.isArray(originals) ? originals[index] : undefined));
    }
    for (const key of ["card", "selectAction", "fallback"]) {
      visit(value[key], depth + 1, isObject(original) ? original[key] : undefined);
    }
  }
  visit(copy, 0, content);
  return { content: copy, blockedUrls, originalNodes };
}
