import type { Message } from "./api";

type ConversationKind = { channel?: boolean; thread?: boolean; groupChat?: boolean };

export function groupsWithPrevious(
  previous: Message | undefined,
  message: Message,
  conversation: ConversationKind,
) {
  if ((conversation.channel && !conversation.thread) || !previous) return false;
  return previous.createdAt.slice(0, 10) === message.createdAt.slice(0, 10)
    && previous.author.id === message.author.id
    && Date.parse(message.createdAt) - Date.parse(previous.createdAt) < 60_000;
}

export function shouldShowAvatar(
  message: Pick<Message, "author">,
  conversation: ConversationKind,
) {
  return !!conversation.channel || !!conversation.groupChat || !message.author.isAgent;
}
