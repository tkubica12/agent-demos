import { Children, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DocumentText24Filled, ArrowDownload20Regular, Copy20Regular, MoreHorizontal20Regular, Share20Regular } from "@fluentui/react-icons";
import { Menu, MenuItem, MenuList, MenuPopover, MenuTrigger } from "@fluentui/react-components";
import { emojiText } from "./emoji";
import type { Api, FileItem, Message } from "./api";
import { HtmlWidgetView } from "./HtmlWidgetView";
import { widgetParts } from "./widget-policy";

const inlineEmoji = (children: ReactNode) => Children.map(children, (child) =>
  typeof child === "string" ? emojiText(child) : child);
const escapeMarkdown = (text: string) => text.replace(/[\\[\]()*_`]/g, "\\$&");

export function MessageBody({ message, api, dark = false }: { message: Message; api: Api; dark?: boolean }) {
  if (message.deleted) return <span className="deleted-message">This message has been deleted.</span>;
  let text = message.text;
  for (const mention of message.mentions) {
    text = text.replaceAll(`<at>${mention.displayName}</at>`,
      `[${escapeMarkdown(mention.displayName)}](/mention/${encodeURIComponent(mention.userId)})`);
  }
  if (message.textFormat === "plain") return <div className="plain-message">{emojiText(message.text)}</div>;
  const markdown = (source: string, key: number) => <Markdown key={key} remarkPlugins={[remarkGfm]} components={{
    p: ({ children }) => <p>{inlineEmoji(children)}</p>,
    strong: ({ children }) => <strong>{inlineEmoji(children)}</strong>,
    em: ({ children }) => <em>{inlineEmoji(children)}</em>,
    li: ({ children }) => <li>{inlineEmoji(children)}</li>,
    a: ({ href, children }) => href?.startsWith("/mention/")
      ? <span className="mention">{children}</span>
      : href && /^https?:\/\//i.test(href)
        ? <a href={href} target="_blank" rel="noopener noreferrer">{inlineEmoji(children)}</a>
        : <span>{inlineEmoji(children)}</span>,
    img: ({ alt }) => <span>{alt}</span>,
    h1: ({ children }) => <h3>{inlineEmoji(children)}</h3>,
    h2: ({ children }) => <h3>{inlineEmoji(children)}</h3>,
    h3: ({ children }) => <h3>{inlineEmoji(children)}</h3>,
  }}>{source}</Markdown>;
  return <>{widgetParts(text, message.textFormat, !!message.author.isAgent).map((part, index) =>
    "widget" in part ? <HtmlWidgetView key={`widget-${part.index}`} api={api} messageId={message.id}
      index={part.index} widget={part.widget} dark={dark} /> : markdown(part.text, index))}</>;
}

function FileGlyph({ file }: { file: FileItem }) {
  const extension = file.name.match(/\.([a-z0-9]{1,5})$/i)?.[1].toUpperCase() ?? "FILE";
  return <span className="file-glyph" aria-hidden="true"><DocumentText24Filled /><span>{extension}</span></span>;
}

export function FileCard({ file, onDownload, onCopyLink }: {
  file: FileItem; onDownload: (file: FileItem) => void; onCopyLink: () => void;
}) {
  return <div className="file-card" data-testid="file-card">
    <div className="file-card-heading">
      <FileGlyph file={file} />
      <button className="file-card-name" onClick={() => onDownload(file)} title={`Download ${file.name}`}>{file.name}</button>
      <button aria-label={`Copy sharing link for ${file.name}`} title="Copy message link; conversation membership is required"
        onClick={onCopyLink}><Share20Regular /></button>
      <Menu>
        <MenuTrigger disableButtonEnhancement><button aria-label={`More options for ${file.name}`}><MoreHorizontal20Regular /></button></MenuTrigger>
        <MenuPopover><MenuList>
          <MenuItem icon={<ArrowDownload20Regular />} onClick={() => onDownload(file)}>Download</MenuItem>
          <MenuItem icon={<Copy20Regular />} onClick={onCopyLink}>Copy message link</MenuItem>
        </MenuList></MenuPopover>
      </Menu>
    </div>
    <button className="file-card-preview" aria-label={`Download attachment ${file.name}`} onClick={() => onDownload(file)}>
      <FileGlyph file={file} />
    </button>
  </div>;
}
