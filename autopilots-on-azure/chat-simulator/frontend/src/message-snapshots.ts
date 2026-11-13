import type { Message } from "./api";

export function newestMessageSnapshot(current: Message | undefined, incoming: Message): Message {
  if (current && current.id !== incoming.id) throw new Error("Cannot compare snapshots of different messages.");
  return current && current.snapshotRevision >= incoming.snapshotRevision ? current : incoming;
}

export function mergeMessageSnapshots(current: readonly Message[], incoming: readonly Message[]): Message[] {
  const messages = new Map(current.map((message) => [message.id, message]));
  for (const message of incoming) messages.set(message.id, newestMessageSnapshot(messages.get(message.id), message));
  return [...messages.values()].sort((a, b) =>
    Date.parse(a.createdAt) - Date.parse(b.createdAt) || a.id.localeCompare(b.id));
}
