import { Avatar } from "@fluentui/react-components";
import { ChatMultiple20Regular, ChevronRight16Regular } from "@fluentui/react-icons";
import type { Message, User } from "./api";
import { Emoji } from "./emoji";

export type Notice = {
  id: string; message: Message; actor: User; kind: "mention" | "reply" | "reaction";
  occurredAt?: string; emoji?: string;
};

export const reactionNoticeId = (messageId: string, actorId: string, emoji: string) =>
  `reaction:${messageId}:${actorId}:${emoji}`;

export function messageNotices(messages: Message[], me: User, reactionTimes: Record<string, string>): Notice[] {
  const byId = new Map(messages.map((message) => [message.id, message]));
  return messages.flatMap((message) => {
    if (message.deleted) return [];
    const result: Notice[] = [];
    if (message.author.id !== me.id) {
      if (message.mentions.some((mention) => mention.userId === me.id)) result.push({
        id: `mention:${message.id}`, message, actor: message.author, kind: "mention", occurredAt: message.createdAt,
      });
      if (message.replyToId && byId.get(message.replyToId)?.author.id === me.id) result.push({
        id: `reply:${message.id}`, message, actor: message.author, kind: "reply", occurredAt: message.createdAt,
      });
    }
    if (message.author.id === me.id) {
      for (const reaction of message.reactions) {
        for (const actor of reaction.users.filter((user) => user.id !== me.id && reaction.userIds.includes(user.id))) {
          const id = reactionNoticeId(message.id, actor.id, reaction.emoji);
          result.push({ id, message, actor, kind: "reaction", emoji: reaction.emoji, occurredAt: reactionTimes[id] });
        }
      }
    }
    return result;
  }).sort((a, b) => (b.occurredAt ? Date.parse(b.occurredAt) : 0) -
    (a.occurredAt ? Date.parse(a.occurredAt) : 0) || a.id.localeCompare(b.id)).slice(0, 100);
}

export function ActivityFeed({ notices, selectedId, readIds, filter, onFilter, onSelect, locationLabel }: {
  notices: Notice[]; selectedId: string | null; readIds: Set<string>; filter: "all" | "unread" | "mentions";
  onFilter: (filter: "all" | "unread" | "mentions") => void;
  onSelect: (notice: Notice) => void; locationLabel: (message: Message) => { team?: string; conversation: string };
}) {
  const visible = notices.filter((notice) => filter === "unread" ? !readIds.has(notice.id)
    : filter === "mentions" ? notice.kind === "mention" : true);
  return <div className="activity-feed management-activity-feed">
    <div className="management-activity-filters" aria-label="Activity filters">
      <button aria-pressed={filter === "unread"} onClick={() => onFilter(filter === "unread" ? "all" : "unread")}>Unread</button>
      <button aria-pressed={filter === "mentions"} onClick={() => onFilter(filter === "mentions" ? "all" : "mentions")}>@Mentions</button>
      <button disabled>Tag mentions</button>
    </div>
    <div aria-label="Notifications">
      {visible.map((notice) => {
        const label = locationLabel(notice.message);
        return <button key={notice.id} className={`activity-item management-activity-card ${notice.id === selectedId ? "is-selected" : ""}`}
          data-notice-kind={notice.kind} data-notice-id={notice.id} data-actor-id={notice.actor.id}
          aria-current={notice.id === selectedId ? "true" : undefined} onClick={() => onSelect(notice)}>
          <span className="management-notice-avatar">
            <Avatar name={notice.actor.displayName} size={32} color="colorful" />
            <span className="management-notice-badge" aria-label={notice.kind === "reaction" ? "Reaction" : notice.kind === "mention" ? "Mention" : "Reply"}>
              {notice.emoji ? <Emoji value={notice.emoji} size={17} />
                : notice.kind === "mention" ? <span>@</span> : <ChatMultiple20Regular />}
            </span>
          </span>
          <span className="management-notice-content">
            <span className="management-notice-heading">
              <strong>{notice.actor.displayName} {notice.kind === "reaction" ? "reacted to your message"
                : notice.kind === "mention" ? "mentioned you" : "replied to message in a thread"}</strong>
              {notice.occurredAt && <time dateTime={notice.occurredAt} title={new Date(notice.occurredAt).toLocaleString()}>
                {new Date(notice.occurredAt).toLocaleDateString("en-US", { month: "numeric", day: "numeric" })}
              </time>}
            </span>
            <span className="management-notice-preview">{notice.message.text.replace(/<\/?at>/g, "")}</span>
            <span className="management-notice-location">{label.team && <>{label.team}<ChevronRight16Regular /></>}{label.conversation}</span>
          </span>
          {!readIds.has(notice.id) && <span className="management-notice-unread" aria-label="Unread notification" />}
        </button>;
      })}
    </div>
    {!visible.length && <p className="sidebar-empty">{filter === "unread" ? "You're all caught up."
      : filter === "mentions" ? "No mentions in your loaded conversations." : "Mentions, replies and reactions will appear here."}</p>}
  </div>;
}
