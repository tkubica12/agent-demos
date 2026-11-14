import { useEffect, useRef, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as Cards from "adaptivecards";
import { isObject, prepareCard, safeCardUrl } from "./card-policy";
import "./adaptive-cards.css";

export type CardAction = {
  action: Record<string, unknown>; inputs: Record<string, unknown>;
};

Cards.AdaptiveCard.onProcessMarkdown = (text, result) => {
  result.outputHtml = renderToStaticMarkup(<Markdown skipHtml remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => {
      const safe = safeCardUrl(href, location.origin);
      return safe ? <a href={safe} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
    },
    img: ({ alt }) => <span>{alt}</span>,
  }}>{text}</Markdown>);
  result.didProcess = true;
};

function serializationContext() {
  const context = new Cards.SerializationContext(Cards.Versions.v1_5);
  const elements = new Cards.CardObjectRegistry<Cards.CardElement>();
  Cards.GlobalRegistry.defaultElements.copyTo(elements);
  for (let index = elements.getItemCount() - 1; index >= 0; index--) {
    const item = elements.getItemAt(index);
    if (item.schemaVersion.compareTo(Cards.Versions.v1_5) > 0) elements.unregister(item.typeName);
  }
  const actions = new Cards.CardObjectRegistry<Cards.Action>();
  Cards.GlobalRegistry.defaultActions.copyTo(actions);
  const supported = new Set(["Action.Submit", "Action.Execute", "Action.OpenUrl",
    "Action.ShowCard", "Action.ToggleVisibility"]);
  for (let index = actions.getItemCount() - 1; index >= 0; index--) {
    const item = actions.getItemAt(index);
    if (!supported.has(item.typeName) || item.schemaVersion.compareTo(Cards.Versions.v1_5) > 0) {
      actions.unregister(item.typeName);
    }
  }
  context.setElementRegistry(elements);
  context.setActionRegistry(actions);
  return context;
}

function hostConfig(dark: boolean) {
  const text = dark ? "#f5f5f5" : "#242424";
  const subtle = dark ? "#c4c4c4" : "#616161";
  const accent = dark ? "#aaaefa" : "#5b5fc7";
  const colors = {
    default: { default: text, subtle }, accent: { default: accent, subtle: accent },
    good: { default: "#107c10", subtle: "#107c10" },
    warning: { default: dark ? "#ffdf80" : "#835c00", subtle },
    attention: { default: dark ? "#ff9999" : "#c4314b", subtle },
  };
  return new Cards.HostConfig({
    supportsInteractivity: true, fontFamily: '"Segoe UI", Arial, sans-serif',
    fontSizes: { small: 12, default: 14, medium: 14, large: 18, extraLarge: 24 },
    fontWeights: { lighter: 300, default: 400, bolder: 600 },
    lineHeights: { small: 16, default: 20, medium: 20, large: 24, extraLarge: 32 },
    spacing: { small: 8, default: 12, medium: 16, large: 20, extraLarge: 24, padding: 16 },
    separator: { lineThickness: 1, lineColor: dark ? "#484848" : "#d1d1d1" },
    containerStyles: {
      default: { backgroundColor: dark ? "#1f1f1f" : "#fafafa", foregroundColors: colors },
      emphasis: { backgroundColor: dark ? "#292929" : "#f5f5f5", foregroundColors: colors },
    },
    actions: { maxActions: 6, buttonSpacing: 8, spacing: "default",
      actionsOrientation: "horizontal", actionAlignment: "left",
      showCard: { actionMode: "inline", inlineTopMargin: 16 } },
  });
}

export function AdaptiveCardView({ content, dark, onAction, onError }: {
  content: Record<string, unknown>; dark: boolean;
  onAction: (action: CardAction) => Promise<void>; onError: (error: unknown) => void;
}) {
  const target = useRef<HTMLDivElement>(null);
  const renderer = useRef<Cards.AdaptiveCard | undefined>(undefined);
  const previousPayload = useRef("");
  const callbacks = useRef({ onAction, onError });
  const pending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [fallback, setFallback] = useState("");
  const [failed, setFailed] = useState(false);
  const payload = JSON.stringify(content);
  useEffect(() => { callbacks.current = { onAction, onError }; }, [onAction, onError]);

  useEffect(() => {
    const container = target.current;
    if (!container) return;
    container.replaceChildren();
    setFailed(false);
    setFallback("");
    try {
      const inputValues = previousPayload.current === payload && renderer.current
        ? new Map(renderer.current.getAllInputs().filter((input) => typeof input.id === "string")
          .map((input) => [input.id!, String(input.value ?? "")])) : undefined;
      const source: unknown = JSON.parse(payload);
      if (!isObject(source)) throw new Error("Invalid Adaptive Card content.");
      const prepared = prepareCard(source, location.origin, inputValues);
      if (prepared.blockedUrls) callbacks.current.onError(new Error("Unsafe card link or image URL was blocked."));
      const card = new Cards.AdaptiveCard();
      card.maxVersion = Cards.Versions.v1_5;
      card.hostConfig = hostConfig(dark);
      const originals = new WeakMap<Cards.Action, Record<string, unknown>>();
      const context = serializationContext();
      context.onParseAction = (action, parsedSource) => {
        if (isObject(parsedSource)) {
          const original = prepared.originalNodes.get(parsedSource);
          if (original) originals.set(action, original);
        }
      };
      card.parse(prepared.content, context);
      card.onAnchorClicked = (_element, anchor) => {
        const safe = safeCardUrl(anchor.href, location.origin);
        if (!safe) {
          callbacks.current.onError(new Error("Unsafe card link was blocked."));
          return true;
        }
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        return false;
      };
      card.onExecuteAction = (action) => {
        if (action instanceof Cards.OpenUrlAction) {
          const safe = safeCardUrl(action.url, location.origin);
          if (safe) window.open(safe, "_blank", "noopener,noreferrer");
          else callbacks.current.onError(new Error("Unsafe card link was blocked."));
          return;
        }
        if (!(action instanceof Cards.SubmitAction || action instanceof Cards.ExecuteAction)) return;
        if (pending.current) return;
        const data: unknown = action.data ?? {};
        const original = originals.get(action);
        if (!isObject(data) || !original) {
          callbacks.current.onError(new Error("Card action does not match the stored card."));
          return;
        }
        pending.current = true;
        setBusy(true);
        const inputs = action.associatedInputs === "none" ? {} : Object.fromEntries(
          card.getAllInputs().filter((input) => typeof input.id === "string" && Object.hasOwn(data, input.id))
            .map((input) => [input.id!, data[input.id!]]));
        const outgoing: CardAction = { action: original, inputs };
        void callbacks.current.onAction(outgoing).catch((error: unknown) => callbacks.current.onError(error))
          .finally(() => { pending.current = false; setBusy(false); });
      };
      const element = card.render();
      if (element) container.append(element);
      else setFallback(card.fallbackText || "This card is not supported by the simulator.");
      renderer.current = card;
      previousPayload.current = payload;
    } catch (error) {
      setFailed(true);
      setFallback("This card could not be rendered.");
      callbacks.current.onError(error);
    }
  }, [payload, dark]);

  return <div className="adaptive-card" data-testid="adaptive-card" data-schema-max="1.5">
    <fieldset disabled={busy}><div ref={target} /></fieldset>
    {fallback && <p className="card-fallback" role={failed ? "alert" : "status"}>{fallback}</p>}
    {busy && <p className="card-action-status" role="status">Sending card response...</p>}
  </div>;
}
