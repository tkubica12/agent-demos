export type WidgetPayload = {
  type: "widget/mcp-ui"; name: string; html: string; domain: string;
  securityPolicy?: Record<string, string[]>;
  mcpUrl?: string; correlationId?: string;
};
export type WidgetPart = { text: string } | { widget: WidgetPayload; index: number };
const bytes = (value: string) => new TextEncoder().encode(value).length;
export const object = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function httpsUrl(value: unknown) {
  if (typeof value !== "string" || !value || value.length > 2048 || /[\s\\'";\u0000-\u001f]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !!url.hostname && !url.username && !url.password
      && (!url.port || Number(url.port) >= 1 && Number(url.port) <= 65535);
  } catch { return false; }
}

function payload(value: unknown): value is WidgetPayload {
  const fields = new Set(["type", "name", "html", "domain", "securityPolicy", "description",
    "toolInput", "toolOutput", "permissions", "mcpUrl", "correlationId"]);
  if (!object(value) || Object.keys(value).some((key) => !fields.has(key)) || value.type !== "widget/mcp-ui"
    || typeof value.name !== "string" || !value.name.trim() || value.name.length > 200
    || typeof value.html !== "string" || !value.html.trim() || value.html.length > 100_000
    || !httpsUrl(value.domain) || bytes(JSON.stringify(value)) > 100_000) return false;
  const policy = value.securityPolicy ?? {};
  const keys = new Set(["connectDomains", "resourceDomains", "frameDomains", "baseUriDomains"]);
  if (!object(policy) || Object.entries(policy).some(([key, domains]) =>
    !keys.has(key) || !Array.isArray(domains) || domains.length > 20 || !domains.every(httpsUrl))) return false;
  if ("mcpUrl" in value && !httpsUrl(value.mcpUrl)) return false;
  return ["description", "correlationId"].every((key) => !(key in value)
    || typeof value[key] === "string" && value[key].length <= 2048);
}

export function widgetParts(text: string, textFormat: string, agent: boolean): WidgetPart[] {
  if (!agent || textFormat !== "extendedmarkdown" || bytes(text) > 100_000) return [{ text }];
  const lines = text.match(/[^\n]*(?:\n|$)/g) ?? [];
  const result: WidgetPart[] = [];
  let opened: { marker: string; candidate: boolean; start: number; body: number; index: number } | undefined;
  let offset = 0, previous = 0, index = -1;
  for (const line of lines) {
    const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line.replace(/\r?\n$/, ""));
    if (!opened && match) {
      const candidate = match[1][0] === "`" && match[2].trim() === "html-widget";
      if (candidate) index++;
      opened = { marker: match[1], candidate, start: offset, body: offset + line.length, index };
    } else if (opened && match && match[1][0] === opened.marker[0] && match[1].length >= opened.marker.length
      && !match[2].trim()) {
      if (opened.candidate) {
        try {
          const value: unknown = strictWidgetJson(text.slice(opened.body, offset));
          if (payload(value)) {
            if (opened.start > previous) result.push({ text: text.slice(previous, opened.start) });
            result.push({ widget: value, index: opened.index });
            previous = offset + line.length;
          }

          function strictWidgetJson(source: string): unknown {
            const value: unknown = JSON.parse(source);
            const tokens = source.match(/"(?:\\.|[^"\\])*"|[{}\[\]:,]/g) ?? [];
            const stack: (Set<string> | undefined)[] = [];
            for (let index = 0; index < tokens.length; index++) {
              const token = tokens[index];
              if (token === "{") stack.push(new Set());
              else if (token === "[") stack.push(undefined);
              else if (token === "}" || token === "]") stack.pop();
              else if (token.startsWith('"') && tokens[index + 1] === ":") {
                const key: string = JSON.parse(token);
                const keys = stack.at(-1);
                if (keys?.has(key)) throw new Error("Duplicate widget JSON key");
                keys?.add(key);
              }
            }
            return value;
          }
        } catch { /* Malformed widget fences stay visible as ordinary code. */ }
      }
      opened = undefined;
    }
    offset += line.length;
  }
  if (previous < text.length) result.push({ text: text.slice(previous) });
  return result.length ? result : [{ text }];
}

export type WidgetHost = {
  reply: (response: Record<string, unknown>) => void;
  call: (params: { name: string; arguments: Record<string, unknown> }) => Promise<unknown>;
  resize: (height: number) => void;
  initialized: () => void;
  context: () => { theme: string; locale: string };
};

export function createWidgetProtocol(host: WidgetHost) {
  let initializing = false, ready = false, pending = false;
  const calls: number[] = [];
  const error = (id: unknown, code: number, message: string) =>
    host.reply({ jsonrpc: "2.0", id, error: { code, message } });
  return async (data: unknown) => {
    let size: number;
    try { size = bytes(JSON.stringify(data)); } catch { return error(null, -32600, "Invalid JSON-RPC request"); }
    const id = object(data) && (typeof data.id === "string" && data.id.length <= 200
      || typeof data.id === "number" && Number.isSafeInteger(data.id)) ? data.id : null;
    if (size > 16_384 || !object(data) || data.jsonrpc !== "2.0" || typeof data.method !== "string"
      || Object.keys(data).some((key) => !["jsonrpc", "id", "method", "params"].includes(key))
      || ("id" in data && id === null) || !object(data.params)) {
      return error(id, -32600, "Invalid or oversized JSON-RPC request");
    }
    if (data.method === "ui/initialize") {
      if (id === null || initializing || data.params.protocolVersion !== "2026-01-26"
        || !object(data.params.appInfo) || !object(data.params.appCapabilities)) {
        return error(id, -32602, "Unsupported widget initialization");
      }
      initializing = true;
      host.reply({ jsonrpc: "2.0", id, result: {
        protocolVersion: "2026-01-26", hostInfo: { name: "agent-chat-simulator", version: "1.0.0" },
        hostCapabilities: { serverTools: { listChanged: false } },
        hostContext: { ...host.context(), displayMode: "inline" },
      } });
      return;
    }
    if (data.method === "ui/notifications/initialized" && !("id" in data) && initializing && !ready) {
      ready = true; host.initialized(); return;
    }
    if (data.method === "ui/notifications/size-changed" && !("id" in data) && ready) {
      const height = data.params.height;
      if (typeof height !== "number" || !Number.isFinite(height) || height < 0) {
        return error(null, -32602, "Invalid widget height");
      }
      host.resize(Math.max(32, Math.min(4096, Math.ceil(height))));
      return;
    }
    if (data.method !== "tools/call") {
      if (!("id" in data)) return;
      return error(id, -32601, "Host method is not supported");
    }
    if (!ready || id === null || data.params.name !== "autopilot.widget.submit" || !object(data.params.arguments)
      || Object.keys(data.params).some((key) => !["name", "arguments"].includes(key))) {
      return error(id, -32602, "Unsupported tool call");
    }
    const now = Date.now();
    while (calls.length && calls[0] <= now - 60_000) calls.shift();
    if (pending || calls.length >= 6) return error(id, -32000, "Widget tool call is pending or rate limited");
    calls.push(now);
    pending = true;
    try {
      const result = await host.call({ name: data.params.name, arguments: data.params.arguments });
      host.reply({ jsonrpc: "2.0", id, result });
    } catch (cause) {
      error(id, -32000, cause instanceof Error ? cause.message : "Widget tool delivery failed");
    } finally { pending = false; }
  };
}
