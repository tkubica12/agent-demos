import { createContext, useContext, useEffect, useMemo, useState, type ReactElement, type ReactNode } from "react";
import { Popover, PopoverSurface, PopoverTrigger } from "@fluentui/react-components";
import {
  AnimalCat20Regular, Building20Regular, ChevronRight16Regular, Emoji20Regular, FoodPizza20Regular,
  HandWave20Regular, History20Regular, Lightbulb20Regular, NumberSymbol20Regular,
  People20Regular, Search20Regular, SportSoccer20Regular,
} from "@fluentui/react-icons";

type Entry = { emoji: string; label: string; tags: string[]; group: number; order: number; url: string };
const Catalogue = createContext<{ entries: Entry[]; lookup: Map<string, Entry> }>({ entries: [], lookup: new Map() });
const normalized = (emoji: string) => emoji.replace(/\ufe0f/g, "");
const segmenter = new Intl.Segmenter("en", { granularity: "grapheme" });
const recent = ["😮", "😨", "😠", "😍", "😎", "🙌", "🤜", "🔥", "🎉", "👀", "💯", "💡",
  "✔️", "➕", "✖️", "🔜", "🚢", "🤔", "🥺", "😭", "🤭", "😌", "🤯", "📎",
  "😳", "🥹", "😱", "😐", "🙄", "🤩", "🤪", "😒", "🥳", "🤣", "🫠", "😽"];

function isEntry(value: unknown): value is Entry {
  if (typeof value !== "object" || value === null) return false;
  return "emoji" in value && typeof value.emoji === "string" &&
    "label" in value && typeof value.label === "string" &&
    "tags" in value && Array.isArray(value.tags) && value.tags.every((tag) => typeof tag === "string") &&
    "group" in value && typeof value.group === "number" &&
    "order" in value && typeof value.order === "number" &&
    "url" in value && typeof value.url === "string" && /^\/emoji\/[a-f0-9-]+\.webp$/.test(value.url);
}

export function EmojiProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    void (async () => {
      const response = await fetch("/emoji/catalogue.json");
      if (!response.ok) throw new Error(`Emoji catalogue unavailable (${response.status}).`);
      const data: unknown = await response.json();
      if (typeof data !== "object" || data === null || !("entries" in data) ||
        !Array.isArray(data.entries) || !data.entries.every(isEntry) || data.entries.length < 1000) {
        throw new Error("Invalid Fluent 3D catalogue.");
      }
      if (active) {
        setEntries(data.entries);
        document.documentElement.dataset.emojiReady = "true";
      }
    })().catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "Emoji loading failed."); });
    return () => { active = false; };
  }, []);
  const value = useMemo(() => ({
    entries, lookup: new Map(entries.map((entry) => [normalized(entry.emoji), entry])),
  }), [entries]);
  return <Catalogue.Provider value={value}>
    {error && <div role="alert" className="fatal-error">{error}</div>}
    {children}
  </Catalogue.Provider>;
}

export function Emoji({ value, size = 22 }: { value: string; size?: number }) {
  const { lookup } = useContext(Catalogue);
  const [failed, setFailed] = useState(false);
  const entry = lookup.get(normalized(value));
  if (!entry) return <span className="emoji-text" style={{ fontSize: size }}>{value}</span>;
  if (failed) return <span role="alert">Emoji asset unavailable: {entry.label}</span>;
  return <img className="fluent-emoji" src={entry.url} alt={value} title={entry.label}
    width={size} height={size} draggable={false} onError={() => setFailed(true)} />;
}

export function emojiText(text: string): ReactNode[] {
  return [...segmenter.segment(text)].map(({ segment }, index) =>
    /\p{Extended_Pictographic}|\p{Regional_Indicator}/u.test(segment)
      ? <Emoji key={index} value={segment} size={20} /> : segment,
  );
}

export const quickReactions = ["👍", "❤️", "😆", "😮", "😢", "😠"];
const categories = [
  { name: "Recent", group: -1, icon: <History20Regular /> },
  { name: "Smileys", group: 0, icon: <Emoji20Regular /> },
  { name: "Hands", group: 1, icon: <HandWave20Regular /> },
  { name: "People", group: 1, icon: <People20Regular /> },
  { name: "Animals", group: 3, icon: <AnimalCat20Regular /> },
  { name: "Food", group: 4, icon: <FoodPizza20Regular /> },
  { name: "Travel", group: 5, icon: <Building20Regular /> },
  { name: "Objects", group: 7, icon: <Lightbulb20Regular /> },
  { name: "Activities", group: 6, icon: <SportSoccer20Regular /> },
  { name: "Symbols", group: 8, icon: <NumberSymbol20Regular /> },
];

export function EmojiPicker({ trigger, reaction = false, onPick }: {
  trigger: ReactElement; reaction?: boolean; onPick: (emoji: string) => void;
}) {
  const { entries, lookup } = useContext(Catalogue);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState(0);
  const [tab, setTab] = useState("Emoji");
  const summary = !reaction && tab === "All" && !query;
  const selection = query ? entries.filter((entry) =>
    `${entry.emoji} ${entry.label} ${entry.tags.join(" ")}`.toLowerCase().includes(query.toLowerCase()),
  ) : category === 0 ? recent.flatMap((emoji) => {
    const entry = lookup.get(normalized(emoji));
    return entry ? [entry] : [];
  }) : entries.filter((entry) => entry.group === categories[category].group);
  return <Popover open={open} onOpenChange={(_, data) => setOpen(data.open)}
    positioning={{ position: "above", align: reaction ? "center" : "end", offset: 7 }}>
    <PopoverTrigger disableButtonEnhancement>{trigger}</PopoverTrigger>
    <PopoverSurface className={`emoji-picker ${reaction ? "reaction-picker" : ""}`}
      data-testid={reaction ? "reaction-picker" : "emoji-picker"} aria-label={reaction ? "Choose a reaction" : "Emoji picker"}>
      {!reaction && <div className="picker-tabs" role="tablist" aria-label="Expression type">
        {["All", "Emoji", "GIFs", "Stickers"].map((name) =>
          <button key={name} role="tab" aria-selected={tab === name} disabled={name === "GIFs" || name === "Stickers"}
            onClick={() => setTab(name)}>{name}</button>)}
      </div>}
      <div className={`picker-content ${summary ? "picker-summary" : ""}`}>
      <div className="picker-search-row">
        <label className="picker-search"><input aria-label="Find emoji" placeholder="Find something fun"
          value={query} onChange={(event) => setQuery(event.target.value)} /><Search20Regular /></label>
        <Emoji20Regular />
      </div>
      <div className="picker-heading">{query ? "Search results" : summary ? "Emoji" : categories[category].name}
        {summary && <button className="picker-see-all" onClick={() => setTab("Emoji")}>See all <ChevronRight16Regular /></button>}
      </div>
      <div className="emoji-grid">
        {(summary ? selection.slice(0, 6) : selection).map((entry) => <button key={entry.emoji} aria-label={entry.label} title={entry.label}
          onClick={() => { onPick(entry.emoji); setOpen(false); }}><Emoji value={entry.emoji} size={28} /></button>)}
        {!selection.length && <p>No matching emoji.</p>}
      </div>
      {!summary && <nav className="emoji-categories" aria-label="Emoji categories">
        {categories.map((item, index) => <button key={item.name} aria-label={item.name}
          aria-pressed={category === index} onClick={() => { setCategory(index); setQuery(""); }}>{item.icon}</button>)}
      </nav>}
      </div>
    </PopoverSurface>
  </Popover>;
}
