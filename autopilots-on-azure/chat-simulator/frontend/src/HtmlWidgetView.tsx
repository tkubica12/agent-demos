import { useEffect, useRef, useState } from "react";
import type { Api } from "./api";
import { createWidgetProtocol, type WidgetPayload } from "./widget-policy";

export function HtmlWidgetView({ api, messageId, index, widget, dark }: {
  api: Api; messageId: string; index: number; widget: WidgetPayload; dark: boolean;
}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const container = useRef<HTMLDivElement>(null);
  const loads = useRef(0);
  const closed = useRef(false);
  const callbacks = useRef({ api, dark });
  callbacks.current = { api, dark };
  const [url, setUrl] = useState("");
  const [height, setHeight] = useState(180);
  const [status, setStatus] = useState("Connecting widget...");
  const [failed, setFailed] = useState(false);
  const [visible, setVisible] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const html = widget.html;
  useEffect(() => {
    const target = container.current;
    if (!target) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    }, { root: target.closest(".message-list"), rootMargin: "300px" });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let active = true, initialized = false;
    loads.current = 0; closed.current = false;
    setUrl(""); setFailed(false); setStatus("Connecting widget...");
    const path = `/messages/${encodeURIComponent(messageId)}/widgets/${index}`;
    const protocol = createWidgetProtocol({
      reply: (data) => { if (active && !closed.current) frame.current?.contentWindow?.postMessage(data, "*"); },
      call: async (params) => {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 18_000);
        try {
          const response = await callbacks.current.api.request<{ callToolResult: unknown }>(
            `${path}/tools`, "POST", params, controller.signal);
          return response.callToolResult;
        } catch (cause) {
          if (controller.signal.aborted) throw new Error("Widget delivery timed out; outcome unknown. Retry the same selection.");
          throw cause;
        } finally { clearTimeout(timer); }
      },
      resize: (next) => {
        const scroller = frame.current?.closest(".message-list");
        const atBottom = scroller && scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
        setHeight((previous) => previous === next ? previous : next);
        if (atBottom) requestAnimationFrame(() => { if (active) scroller.scrollTop = scroller.scrollHeight; });
      },
      initialized: () => { initialized = true; setStatus(""); },
      context: () => ({ theme: callbacks.current.dark ? "dark" : "light", locale: navigator.language || "en-US" }),
    });
    const receive = (event: MessageEvent<unknown>) => {
      if (active && !closed.current && frame.current?.contentWindow && event.source === frame.current.contentWindow) {
        void protocol(event.data);
      }
    };
    window.addEventListener("message", receive);
    const timer = setTimeout(() => {
      if (active && !initialized) { setFailed(true); setStatus("Widget initialization failed or timed out."); }
    }, 16_000);
    void callbacks.current.api.request<{ url: string }>(`${path}/frame`, "POST")
      .then((result) => { if (active) setUrl(result.url); })
      .catch((cause: unknown) => {
        if (active) { setFailed(true); setStatus(cause instanceof Error ? cause.message : "Widget frame failed."); }
      });
    return () => { active = false; clearTimeout(timer); window.removeEventListener("message", receive); };
  }, [messageId, index, html, visible, attempt]);

  return <div ref={container} className="html-widget" data-testid="html-widget" style={{ minHeight: visible ? undefined : 180 }}>
    {url && <iframe ref={frame} src={url} title={widget.name} sandbox="allow-scripts"
      onLoad={() => {
        if (++loads.current > 1) {
          closed.current = true; setUrl(""); setFailed(true); setStatus("Widget navigated away and was closed.");
        }
      }}
      referrerPolicy="no-referrer" style={{ width: "100%", height, border: 0, display: "block" }} />}
    {status && <p role={failed ? "alert" : "status"}>{status}</p>}
    {failed && <button onClick={() => setAttempt((previous) => previous + 1)}>Retry widget</button>}
  </div>;
}
