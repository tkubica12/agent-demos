import { useRef, useState, type ChangeEvent } from "react";
import {
  Add20Regular, ArrowUpload20Regular, Attach20Regular, Cloud20Regular, CodeBlock20Regular, Delete20Regular,
  Emoji20Regular, Link20Regular, MoreHorizontal20Regular, Send20Regular, TextBold20Regular,
  TextBulletList20Regular, Highlight20Regular, TextColor20Regular, TextFontSize20Regular, TextItalic20Regular, TextNumberListLtr20Regular,
  TextEditStyle20Filled, TextEditStyle20Regular, TextQuote20Regular, TextStrikethrough20Regular, TextUnderline20Regular, Search20Regular,
} from "@fluentui/react-icons";
import { Popover, PopoverSurface, PopoverTrigger } from "@fluentui/react-components";
import { Emoji, EmojiPicker } from "./emoji";
import type { Api, FileItem, Mention, Message, User } from "./api";

type Target = { conversationId: string; createConversation?: never }
  | { conversationId?: never; createConversation: () => Promise<string> };

export function Composer({ api, conversationId, createConversation, disabled, label, users, replyToId, onSent, onError, onDraft }: {
  api: Api; label: string; users: User[]; replyToId?: string; disabled?: boolean;
  onSent: (message: Message) => void; onError: (error: unknown) => void; onDraft?: (text: string) => void;
} & Target) {
  const [text, setText] = useState("");
  const [formatting, setFormatting] = useState(false);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [attachments, setAttachments] = useState<FileItem[]>([]);
  const [mentions, setMentions] = useState<Mention[]>([]);
  const [attachmentMenu, setAttachmentMenu] = useState(false);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  function update(value: string) {
    setText(value);
    onDraft?.(value);
  }
  function insert(before: string, after = "") {
    const start = input.current?.selectionStart ?? text.length;
    const end = input.current?.selectionEnd ?? text.length;
    update(text.slice(0, start) + before + text.slice(start, end) + after + text.slice(end));
    requestAnimationFrame(() => {
      input.current?.focus();
      input.current?.setSelectionRange(start + before.length, end + before.length);
    });
  }
  function changed(event: ChangeEvent<HTMLTextAreaElement>) {
    update(event.target.value);
    const prefix = event.target.value.slice(0, event.target.selectionStart);
    setMentionQuery(prefix.match(/(?:^|\s)@([^\s@]*)$/)?.[1] ?? null);
  }
  function mention(user: User) {
    const caret = input.current?.selectionStart ?? text.length;
    const prefix = text.slice(0, caret).replace(/@([^\s@]*)$/, `<at>${user.displayName}</at> `);
    update(prefix + text.slice(caret));
    setMentions((current) => current.some((item) => item.userId === user.id)
      ? current : [...current, { userId: user.id, displayName: user.displayName }]);
    setMentionQuery(null);
    input.current?.focus();
  }
  async function send() {
    if (disabled || sending || uploading || (!text.trim() && !attachments.length)) return;
    setSending(true);
    try {
      const target = conversationId ?? (createConversation ? await createConversation() : null);
      if (!target) throw new Error("Choose a conversation before sending.");
      const message = await api.request<Message>(`/conversations/${encodeURIComponent(target)}/messages`, "POST", {
        text, textFormat: "markdown", replyToId,
        mentions: mentions.filter((item) => text.includes(`<at>${item.displayName}</at>`)),
        attachmentIds: attachments.map((file) => file.id),
      });
      onSent(message);
      update("");
      setAttachments([]);
      setMentions([]);
    } catch (error) { onError(error); }
    finally { setSending(false); }
  }
  async function upload(file: File) {
    if (!conversationId) { onError(new Error("Start the conversation before uploading attachments.")); return; }
    if (file.size > 25 * 1024 * 1024) { onError(new Error("Files must be 25 MB or smaller.")); return; }
    setUploading(true);
    try {
      const uploaded = await api.upload(conversationId, file);
      setAttachments((current) => [...current, uploaded]);
      setAttachmentMenu(false);
    } catch (error) { onError(error); }
    finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  return <div className={`composer ${formatting ? "expanded" : ""} ${replyToId ? "thread-composer" : ""}`} data-testid="composer">
    {replyToId && <div className="send-target"><strong>Send to:</strong> thread only</div>}
    {formatting && <div className="format-toolbar" data-testid="format-toolbar">
      <button aria-label="Bold" onClick={() => insert("**", "**")}><TextBold20Regular /></button>
      <button aria-label="Italic" onClick={() => insert("_", "_")}><TextItalic20Regular /></button>
      <button aria-label="Underline" disabled title="Not part of the pilot"><TextUnderline20Regular /></button>
      <button aria-label="Strikethrough" onClick={() => insert("~~", "~~")}><TextStrikethrough20Regular /></button>
      <span className="toolbar-divider" />
      <button aria-label="Bullet list" onClick={() => insert("\n- ")}><TextBulletList20Regular /></button>
      <button aria-label="Numbered list" onClick={() => insert("\n1. ")}><TextNumberListLtr20Regular /></button>
      <span className="toolbar-divider" />
      <button aria-label="Highlight text" disabled title="Not part of the pilot"><Highlight20Regular /></button>
      <button aria-label="Font color" disabled title="Not part of the pilot"><TextColor20Regular /></button>
      <button aria-label="Font size" disabled title="Not part of the pilot"><TextFontSize20Regular /></button>
      <span className="toolbar-divider" />
      <button aria-label="Quote" onClick={() => insert("\n> ")}><TextQuote20Regular /></button>
      <button aria-label="Insert link" onClick={() => insert("[", "](https://example.com)")}><Link20Regular /></button>
      <button aria-label="Code" onClick={() => insert("`", "`")}><CodeBlock20Regular /></button>
      <button aria-label="More formatting" disabled><MoreHorizontal20Regular /></button>
      <button aria-label="Discard draft" className="discard-draft" onClick={() => { update(""); setAttachments([]); setMentions([]); }}><Delete20Regular /></button>
    </div>}
    <textarea ref={input} aria-label={label} placeholder={label} value={text} onChange={changed}
      onKeyDown={(event) => {
        if (event.key === "Escape") setMentionQuery(null);
        if (event.key === "Enter" && !event.shiftKey && !formatting && mentionQuery === null) {
          event.preventDefault(); void send();
        }
      }} />
    {mentionQuery !== null && <div className="mention-suggestions" role="listbox" aria-label="Mention a member">
      {users.filter((user) => user.displayName.toLowerCase().includes(mentionQuery.toLowerCase())).map((user) =>
        <button key={user.id} role="option" aria-selected={false} onClick={() => mention(user)}>{user.displayName}</button>)}
    </div>}
    {attachments.length > 0 && <div className="pending-attachments">{attachments.map((file) =>
      <span key={file.id}>{file.name}<button aria-label={`Remove attachment ${file.name}`}
        onClick={() => setAttachments((current) => current.filter((item) => item.id !== file.id))}>×</button></span>)}</div>}
    <div className="composer-actions">
      {uploading && <span role="status">Uploading…</span>}
      <button aria-label="Format message" aria-pressed={formatting} onClick={() => setFormatting(!formatting)}>
        {formatting ? <TextEditStyle20Filled /> : <TextEditStyle20Regular />}
      </button>
      <EmojiPicker trigger={<button aria-label="Emoji"><Emoji20Regular /></button>} onPick={(value) => insert(value)} />
      <Popover open={attachmentMenu} onOpenChange={(_, data) => setAttachmentMenu(data.open)}
        positioning={{ position: "above", align: "end" }}>
        <PopoverTrigger disableButtonEnhancement><button aria-label="Attach file" disabled={!conversationId}
          title={!conversationId ? "Start the conversation before attaching files." : undefined}><Attach20Regular /></button></PopoverTrigger>
        <PopoverSurface className="attachment-menu" data-testid="attachment-menu">
          <div className="cloud-search"><Search20Regular /><span>Search cloud files</span></div>
          <div className="attachment-empty"><div className="attachment-illustration" aria-hidden="true">
            <Emoji value="📂" size={76} /><Emoji value="🕒" size={40} />
          </div><p>Your recent file suggestions<br />will show up here soon.</p></div>
          <div className="attachment-divider" />
          <button disabled title="Cloud files are outside the pilot"><Cloud20Regular />Attach cloud files</button>
          <button onClick={() => fileInput.current?.click()}><ArrowUpload20Regular />Upload from this device</button>
        </PopoverSurface>
      </Popover>
      <button aria-label="More composer actions" disabled><Add20Regular /></button>
      <span className="toolbar-divider" />
      <button className="send-button" aria-label="Send message" disabled={disabled || sending || uploading || (!text.trim() && !attachments.length)}
        onClick={() => void send()}><Send20Regular /></button>
    </div>
    <input type="file" ref={fileInput} hidden aria-label="Upload attachment" onChange={(event) => {
      const file = event.target.files?.[0];
      if (file) void upload(file);
    }} />
  </div>;
}
