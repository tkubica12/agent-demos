import { useEffect, useRef, useState } from "react";
import {
  Avatar, Menu, MenuDivider, MenuItem, MenuList, MenuPopover, MenuTrigger,
} from "@fluentui/react-components";
import {
  ArrowForward20Regular, Bookmark20Regular, ChevronRight20Regular, ClockAlarm20Regular, Copy20Regular,
  Delete20Regular, Edit20Regular, EmojiAdd20Regular, Link20Regular, Mail20Regular,
  MoreHorizontal20Regular, Pin20Regular, EyeOff20Regular, Shield20Regular, Lightbulb20Regular,
  TextQuote20Regular, Translate20Regular, Bot16Regular,
} from "@fluentui/react-icons";
import { Emoji, EmojiPicker, quickReactions } from "./emoji";
import { FileCard, MessageBody } from "./MessageBody";
import type { Api, CardActionResponse, FileItem, Message, SuggestedAction, TypingEvent, User } from "./api";
import { AdaptiveCardView, type CardAction } from "./AdaptiveCardView";
import { isRepresentedFileAttachment, safeCardUrl } from "./card-policy";
import { groupsWithPrevious, shouldShowAvatar } from "./message-grouping";
import "./agents.css";

type Props = {
  api: Api; me: User; messages: Message[]; channel?: boolean; thread?: boolean;
  groupChat?: boolean;
  root?: Message; lastReadId?: string; onChange: (message: Message) => void;
  dark?: boolean; typing?: TypingEvent[];
  onThread: (message: Message) => void; onMarkUnread: (message: Message) => void;
  onError: (error: unknown) => void;
};
const timestamp = (value: string) => new Date(value).toLocaleString("en-US", {
  month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit",
});

export function MessageList(props: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const previousRoot = useRef<string | undefined>(undefined);
  const visible = props.channel && !props.thread ? props.messages.filter((message) => !message.replyToId)
    : props.thread ? props.messages.filter((message) => message.replyToId === props.root?.id)
      : props.messages;
  const typing = (props.typing ?? []).filter((event) => props.thread
    ? event.replyToId === props.root?.id : !event.replyToId);
  useEffect(() => {
    if (scroller.current) scroller.current.scrollTop = props.thread && props.root?.id !== previousRoot.current
      ? 0 : scroller.current.scrollHeight;
    previousRoot.current = props.root?.id;
  }, [visible.length, props.root?.id, props.thread, typing.length]);
  return <div className="message-list" ref={scroller} data-testid={props.thread ? "thread-messages" : "messages"}>
    <div className="message-stack">
      {props.thread && props.root && <>
        <MessageRow {...props} message={props.root} grouped={false} rootPreview />
        <div className="replies-divider">{visible.length} replies</div>
      </>}
      {visible.map((message, index) => {
        const previous = visible[index - 1];
        const differentDay = !previous || previous.createdAt.slice(0, 10) !== message.createdAt.slice(0, 10);
        const grouped = groupsWithPrevious(previous, message, props);
        return <div key={message.id}>
          {!props.thread && differentDay && index > 0 &&
            <div className="day-divider">{new Date(message.createdAt).toLocaleDateString("en-US", {
              weekday: "long", month: "long", day: "numeric",
            })}</div>}
          {props.lastReadId === message.id && <div className="last-read">Last read</div>}
          <MessageRow {...props} message={message} grouped={grouped} />
        </div>;
      })}
      {typing.map((event) => <div className="typing-indicator" role="status" key={event.actor.id}>
        {event.actor.displayName} is typing
      </div>)}
    </div>
  </div>;
}

function MessageRow(props: Props & { message: Message; grouped: boolean; rootPreview?: boolean }) {
  const { message, me, api, onChange, onError } = props;
  const own = message.author.id === me.id;
  const showAvatar = shouldShowAvatar(message, props);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.text);
  const [saving, setSaving] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const replies = props.messages.filter((item) => item.replyToId === message.id);
  const replyAuthors = [...new Map(replies.map((reply) => [reply.author.id, reply.author])).values()].slice(0, 3);

  async function react(emoji: string) {
    const mine = message.reactions.find((reaction) => reaction.emoji === emoji)?.userIds.includes(me.id);
    try {
      const path = `/messages/${encodeURIComponent(message.id)}/reactions`;
      onChange(await api.request<Message>(mine ? `${path}/${encodeURIComponent(emoji)}` : path,
        mine ? "DELETE" : "POST", mine ? undefined : { emoji }));
    } catch (error) { onError(error); }
  }
  async function save() {
    setSaving(true);
    try {
      onChange(await api.request<Message>(`/messages/${encodeURIComponent(message.id)}`, "PATCH", {
        text: draft, textFormat: message.textFormat,
        mentions: message.mentions.filter((mention) => draft.includes(`<at>${mention.displayName}</at>`)),
      }));
      setEditing(false);
    } catch (error) { onError(error); }
    finally { setSaving(false); }
  }
  async function remove() {
    try {
      await api.request<void>(`/messages/${encodeURIComponent(message.id)}`, "DELETE");
    } catch (error) { onError(error); }
  }
  function download(file: FileItem) { void api.download(file).catch(onError); }
  function copy(text: string) { void navigator.clipboard.writeText(text).catch(onError); }
  async function respondToCard(action: CardAction) {
    const response = await api.request<CardActionResponse>(`/messages/${encodeURIComponent(message.id)}/actions`,
      "POST", action);
    if (response.message) onChange(response.message);
    if (response.submittedMessage) onChange(response.submittedMessage);
  }
  async function respondToSuggestion(action: SuggestedAction) {
    setActionBusy(true);
    try {
      const response = await api.request<CardActionResponse>(`/messages/${encodeURIComponent(message.id)}/actions`,
        "POST", { action });
      if (response.message) onChange(response.message);
      if (response.submittedMessage) onChange(response.submittedMessage);
    } catch (error) { onError(error); }
    finally { setActionBusy(false); }
  }
  const messageLink = `${location.origin}/#${new URLSearchParams({ conversation: message.conversationId, message: message.id })}`;

  return <article className={`message-row ${own ? "own" : "foreign"} ${props.grouped ? "grouped" : ""} ${props.rootPreview ? "root-preview" : ""} ${showAvatar ? "" : "no-avatar"}`}
    data-testid="message" data-message-id={message.id} data-author-id={message.author.id} data-own={own}>
    {!props.grouped && <div className="message-meta">
      {!own && <span>{message.author.displayName}</span>}
      {message.author.isAgent && <span className="agent-badge"><Bot16Regular />AI agent</span>}
      <time dateTime={message.createdAt}>{timestamp(message.createdAt)}</time>
      {message.updatedAt && !message.deleted && <span className="edited-label">Edited</span>}
    </div>}
    <div className="message-line">
      {!own && showAvatar && <div className="message-avatar">{!props.grouped && <Avatar name={message.author.displayName} size={32} color="colorful" />}</div>}
      <div className="message-content">
        {!message.deleted && !editing && <div className="hover-actions" data-testid="reaction-bar">
          {quickReactions.slice(0, 4).map((emoji) => <button key={emoji} aria-label={`React ${emoji}`}
            onClick={() => void react(emoji)}><Emoji value={emoji} size={22} /></button>)}
          <EmojiPicker reaction trigger={<button aria-label="More reactions"><EmojiAdd20Regular /></button>}
            onPick={(emoji) => void react(emoji)} />
          <span className="toolbar-divider" />
          <button aria-label="Reply in thread" disabled={!props.channel} onClick={() => props.onThread(message)}><TextQuote20Regular /></button>
          <Menu positioning={{ position: "above", align: "start" }}>
            <MenuTrigger disableButtonEnhancement><button aria-label="Message more options"><MoreHorizontal20Regular /></button></MenuTrigger>
            <MenuPopover className="message-menu" data-testid="message-menu"><MenuList>
              <MenuItem icon={<ArrowForward20Regular />} disabled secondaryContent={<span className="menu-hint">F <ChevronRight20Regular /></span>}>Forward</MenuItem>
              <MenuItem icon={<Link20Regular />} onClick={() => copy(messageLink)}>Copy link</MenuItem>
              <MenuItem icon={<Bookmark20Regular />} disabled secondaryContent="S">Save this message</MenuItem>
              <MenuItem icon={<ClockAlarm20Regular />} disabled secondaryContent={<ChevronRight20Regular />}>Remind me</MenuItem>
              <MenuItem icon={<Pin20Regular />} disabled secondaryContent="P">Pin for everyone</MenuItem>
              <MenuItem icon={<Copy20Regular />} onClick={() => copy(message.text)}>Copy text</MenuItem>
              <MenuDivider />
              <MenuItem icon={<EyeOff20Regular />} onClick={() => props.onMarkUnread(message)}>Mark as unread</MenuItem>
              <MenuItem icon={<Mail20Regular />} disabled>Share to Outlook</MenuItem>
              <MenuItem icon={<Translate20Regular />} disabled secondaryContent={<ChevronRight20Regular />}>Translation</MenuItem>
              <MenuDivider />
              <MenuItem icon={<Shield20Regular />} disabled>Report this message</MenuItem>
              <MenuDivider />
              {own ? <>
                <MenuItem icon={<Edit20Regular />} onClick={() => { setDraft(message.text); setEditing(true); }}>Edit</MenuItem>
                <MenuItem icon={<Delete20Regular />} onClick={() => void remove()}>Delete</MenuItem>
              </> : <MenuItem secondaryContent={<ChevronRight20Regular />} disabled>More actions</MenuItem>}
            </MenuList></MenuPopover>
          </Menu>
        </div>}
        <div className={`message-bubble ${editing ? "editing" : ""}`}>
          {editing ? <div className="message-editor">
            <textarea aria-label="Edit message" value={draft} onChange={(event) => setDraft(event.target.value)} />
            <div><button onClick={() => setEditing(false)}>Cancel edit</button>
              <button onClick={() => void save()} disabled={saving}>Save edit</button></div>
          </div> : <MessageBody message={message} />}
          {!message.deleted && message.attachments.map((file) => <FileCard key={file.id} file={file} onDownload={download}
            onCopyLink={() => copy(messageLink)} />)}
          {!message.deleted && message.activityAttachments?.map((attachment, index) =>
            attachment.contentType === "application/vnd.microsoft.card.adaptive" && attachment.content
              ? <AdaptiveCardView key={index} content={attachment.content} dark={!!props.dark}
                onAction={respondToCard} onError={onError} />
              : isRepresentedFileAttachment(attachment, message.attachments) ? null
              : <p className="card-fallback" role="status" key={index}>
                {attachment.name ?? "This attachment type is not supported."}</p>)}
        </div>
        {!message.deleted && message.reactions.length > 0 && <div className="reaction-pills" data-testid="reaction-pills">
          {message.reactions.map((reaction) => <button key={reaction.emoji}
            className={reaction.userIds.includes(me.id) ? "mine" : ""}
            title={reaction.users.map((user) => user.displayName).join(", ")}
            aria-label={`${reaction.emoji} ${reaction.count}: ${reaction.users.map((user) => user.displayName).join(", ")}`}
            onClick={() => void react(reaction.emoji)}><Emoji value={reaction.emoji} size={20} />
            {reaction.count > 1 && <span>{reaction.count}</span>}</button>)}
          <EmojiPicker reaction trigger={<button aria-label="Add reaction"><EmojiAdd20Regular /></button>}
            onPick={(emoji) => void react(emoji)} />
        </div>}
        {props.channel && !props.thread && replies.length > 0 && <button className="reply-summary"
          onClick={() => props.onThread(message)} aria-label={`Open ${replies.length} replies`}>
          <span className="reply-avatars">{replyAuthors.map((user) =>
            <Avatar key={user.id} name={user.displayName} size={24} color="colorful" />)}</span>
          <strong>{replies.length} {replies.length === 1 ? "reply" : "replies"}</strong>
          <span>Last reply {new Date(replies[replies.length - 1].createdAt).toLocaleDateString("en-US", {
            month: "numeric", day: "numeric",
          })}</span>
        </button>}
      </div>
    </div>
    {!message.deleted && !!message.suggestedActions?.actions.length &&
      <div className="suggested-replies" aria-label="Suggested replies">
        {message.suggestedActions.actions.map((action, index) => {
          const label = action.title ?? "Suggested reply";
          if (action.type === "openUrl") {
            const url = safeCardUrl(action.value, location.origin);
            return url ? <a href={url} target="_blank" rel="noopener noreferrer" key={index}>{label}</a>
              : <span className="card-fallback" role="status" key={index}>Unsafe suggested link blocked.</span>;
          }
          return ["imBack", "postBack", "messageBack", "Action.Submit"].includes(action.type)
            ? <button key={index} disabled={actionBusy} onClick={() => void respondToSuggestion(action)}>{label}</button>
            : <span className="card-fallback" role="status" key={index}>Unsupported suggested action: {label}</span>;
        })}
        <Lightbulb20Regular aria-hidden="true" />
      </div>}
  </article>;
}
