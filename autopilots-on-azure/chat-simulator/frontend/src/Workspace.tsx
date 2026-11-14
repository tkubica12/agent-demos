import { useCallback, useEffect, useRef, useState } from "react";
import {
  Avatar, Button, Menu, MenuDivider, MenuItem, MenuList, MenuPopover, MenuTrigger,
} from "@fluentui/react-components";
import {
  Add20Regular, Alert20Regular, Apps20Regular, Archive20Regular, Calendar20Regular, ChannelAdd20Regular,
  Call20Regular, Chat20Regular, ChatMultiple20Regular, ChevronDown16Regular, ChevronRight16Regular, ChevronUp16Regular,
  Cloud20Regular, Compose20Regular, Info20Filled, PanelLeft20Regular, PanelRight20Regular, Sparkle20Regular,
  Delete20Regular, Dismiss20Regular, Eye20Regular, EyeOff20Regular, Link20Regular,
  MoreHorizontal20Regular, People20Regular, PeopleAdd20Regular, PersonAdd20Regular, PersonArrowLeft20Regular, Search20Regular,
  Settings20Regular, Tag20Regular,
} from "@fluentui/react-icons";
import { ApiError, type Channel, type Chat, type FileItem, type Message, type RealtimeEvent,
  type Team, type TypingEvent, type User } from "./api";
import type { Session } from "./auth/session";
import { demoSocketRefusal } from "./auth/demo";
import { Composer } from "./Composer";
import { Emoji } from "./emoji";
import { MessageList } from "./Messages";
import { SharedFiles } from "./SharedFiles";
import { TeamDialog, type DialogState } from "./TeamDialog";
import { AgentDialog } from "./AgentDialog";
import { ActivityFeed, messageNotices, reactionNoticeId, type Notice } from "./Activity";
import { mergeMessageSnapshots, newestMessageSnapshot } from "./message-snapshots";
import "./management.css";

type Page = "conversation" | "directory" | "new-chat" | "activity";
const route = () => new URLSearchParams(location.hash.slice(1));

export function Workspace({ session, onSwitchAccount, dark, onTheme }: {
  session: Session; onSwitchAccount: () => void; dark: boolean; onTheme: () => void;
}) {
  const { api, user: me } = session;
  const [users, setUsers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [chats, setChats] = useState<Chat[]>([]);
  const [messages, setMessages] = useState<Record<string, Message[]>>({});
  const [selectedId, setSelectedId] = useState<string | null>(route().get("conversation"));
  const [rootId, setRootId] = useState<string | null>(route().get("thread"));
  const [page, setPage] = useState<Page>(route().get("view") === "teams" ? "directory"
    : route().get("view") === "activity" ? "activity" : "conversation");
  const [tab, setTab] = useState("conversation");
  const [members, setMembers] = useState<User[]>([]);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [agentDialogOpen, setAgentDialogOpen] = useState(false);
  const [typing, setTyping] = useState<Record<string, TypingEvent>>({});
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState(0);
  const [connection, setConnection] = useState("connecting");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [lastRead, setLastRead] = useState<Record<string, string>>({});
  const [reactionTimes, setReactionTimes] = useState<Record<string, string>>({});
  const [selectedNoticeId, setSelectedNoticeId] = useState<string | null>(route().get("notice"));
  const [readNotices, setReadNotices] = useState<Set<string>>(new Set());
  const [activityFilter, setActivityFilter] = useState<"all" | "unread" | "mentions">("all");
  const [chatUsers, setChatUsers] = useState<User[]>([]);
  const [chatQuery, setChatQuery] = useState("");
  const [chatName, setChatName] = useState("");
  const [creating, setCreating] = useState(false);
  const [newGroup, setNewGroup] = useState(false);
  const [threadsBanner, setThreadsBanner] = useState(true);
  const [directoryFilter, setDirectoryFilter] = useState("all");
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [directoryDescending, setDirectoryDescending] = useState(false);
  const [teamMemberCounts, setTeamMemberCounts] = useState<Record<string, number>>({});
  const pendingChat = useRef<{ key: string; chat: Chat } | null>(null);
  const epoch = useRef(0);
  const selection = useRef(selectedId);
  const accessReady = useRef(false);
  const authorizedConversations = useRef<Set<string>>(new Set());
  const messageSnapshot = useRef<Map<string, Message>>(new Map());
  const loadedHistories = useRef<Set<string>>(new Set());
  const searchInput = useRef<HTMLInputElement>(null);
  const recoverAfterFailure = useRef<(() => Promise<void>) | undefined>(undefined);
  const recoveringFailure = useRef(false);
  selection.current = selectedId;
  const reportError = useCallback((reason: unknown) => {
    setError(reason instanceof Error ? reason.message : "The operation failed.");
    if (reason instanceof ApiError && [502, 503].includes(reason.status)
      && recoverAfterFailure.current && !recoveringFailure.current) {
      recoveringFailure.current = true;
      void recoverAfterFailure.current().finally(() => { recoveringFailure.current = false; });
    }
  }, []);

  const refresh = useCallback(async (invalidate = false) => {
    const generation = ++epoch.current;
    if (invalidate) {
      accessReady.current = false;
      setReady(false);
      setMessages({});
      setMembers([]);
      setFiles([]);
      setReactionTimes({});
      setTeamMemberCounts({});
      setTyping({});
      messageSnapshot.current.clear();
      loadedHistories.current.clear();
    }
    try {
      const [nextUsers, nextTeams, nextChats] = await Promise.all([
        api.request<User[]>("/users"), api.request<Team[]>("/teams"), api.request<Chat[]>("/chats"),
      ]);
      if (generation !== epoch.current) return;
      const authorized = new Set([
        ...nextTeams.flatMap((team) => team.channels.map((channel) => channel.conversationId)),
        ...nextChats.map((chat) => chat.conversationId),
      ]);
      authorizedConversations.current = authorized;
      setUsers(nextUsers); setTeams(nextTeams); setChats(nextChats);
      setMessages((current) => Object.fromEntries(Object.entries(current).filter(([id]) => authorized.has(id))));
      for (const [id, message] of messageSnapshot.current) {
        if (!authorized.has(message.conversationId)) messageSnapshot.current.delete(id);
      }
      for (const id of loadedHistories.current) if (!authorized.has(id)) loadedHistories.current.delete(id);
      if (selection.current && !authorized.has(selection.current)) {
        setSelectedId(null); setRootId(null); setSelectedNoticeId(null);
      }
      accessReady.current = true;
      setReady(true);
      setVersion((current) => current + 1);
    } catch (reason) { reportError(reason); }
  }, [api, reportError]);
  recoverAfterFailure.current = () => refresh(true);
  useEffect(() => { void refresh(); return () => { epoch.current++; }; }, [refresh]);
  useEffect(() => {
    const timer = setInterval(() => setTyping((current) => {
      const active = Object.entries(current).filter(([, event]) => Date.parse(event.expiresAt) > Date.now());
      return active.length === Object.keys(current).length ? current : Object.fromEntries(active);
    }), 500);
    return () => clearInterval(timer);
  }, []);

  const applyMessage = useCallback((message: Message) => {
    if (!accessReady.current || !authorizedConversations.current.has(message.conversationId)) return false;
    if (newestMessageSnapshot(messageSnapshot.current.get(message.id), message) !== message) return false;
    messageSnapshot.current.set(message.id, message);
    setMessages((current) => {
      const existing = current[message.conversationId] ?? [];
      return { ...current, [message.conversationId]: mergeMessageSnapshots(existing, [message]) };
    });
    return true;
  }, []);

  useEffect(() => {
    let active = true;
    let socket: WebSocket;
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    function connect() {
      if (!active) return;
      setConnection(attempts ? "reconnecting" : "connecting");
      socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/ws`);
      socket.onopen = () => {
        void api.getToken().then((token) => {
          if (active && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ token }));
        }).catch((reason) => { reportError(reason); socket.close(); });
      };
      socket.onmessage = (event) => {
        if (!active) return;
        try {
          const value: RealtimeEvent = JSON.parse(event.data);
          if (value.type === "ready") {
            const reconnecting = attempts > 0;
            setConnection("ready"); attempts = 0;
            void refresh(reconnecting);
          } else if (value.type === "directory.changed") {
            void refresh();
          } else if (value.type.startsWith("membership.") || value.type === "chat.created") {
            void refresh(true);
          } else if (value.type === "typing") {
            if (!value.conversationId || !value.actor || !value.expiresAt
              || !Number.isFinite(Date.parse(value.expiresAt))) throw new Error("Invalid typing notification.");
            if (!accessReady.current || !authorizedConversations.current.has(value.conversationId)) return;
            const event: TypingEvent = { conversationId: value.conversationId, replyToId: value.replyToId,
              actor: value.actor, expiresAt: value.expiresAt };
            setTyping((current) => ({ ...current,
              [`${event.conversationId}:${event.replyToId ?? ""}:${event.actor.id}`]: event }));
          } else if (value.message) {
            const message = value.message;
            if (!accessReady.current || !authorizedConversations.current.has(message.conversationId)) return;
            const previous = messageSnapshot.current.get(message.id);
            if (!applyMessage(message)) return;
            if (value.type === "reaction.changed" && value.actor && message.author.id === me.id) {
              const actorId = value.actor.id;
              const additions = previous ? message.reactions.filter((reaction) =>
                reaction.userIds.includes(actorId) && !previous.reactions.some((old) =>
                  old.emoji === reaction.emoji && old.userIds.includes(actorId))) : [];
              const changedIds = additions.map((reaction) => reactionNoticeId(message.id, actorId, reaction.emoji));
              const occurredAt = value.occurredAt;
              const existingIds = new Set(message.reactions.flatMap((reaction) => reaction.userIds.map((id) =>
                reactionNoticeId(message.id, id, reaction.emoji))));
              setReactionTimes((current) => ({
                ...Object.fromEntries(Object.entries(current).filter(([id]) =>
                  !id.startsWith(`reaction:${message.id}:`) || existingIds.has(id))),
                ...(occurredAt && Number.isFinite(Date.parse(occurredAt))
                  ? Object.fromEntries(changedIds.map((id) => [id, occurredAt])) : {}),
              }));
              if (changedIds.length) setReadNotices((current) => new Set([...current].filter((id) => !changedIds.includes(id))));
            }
          } else if (value.type === "file.created") setVersion((current) => current + 1);
        } catch (reason) { reportError(reason); }
      };
      socket.onclose = async (event) => {
        if (!active) return;
        if (session.demo) {
          const refusal = demoSocketRefusal(event.code, event.reason);
          if (refusal) {
            active = false;
            setConnection("refused");
            session.demo.refuse(refusal);
            return;
          }
          try { await api.request<User>("/me"); }
          catch (reason) {
            try { session.demo.assertActive(); }
            catch { active = false; setConnection("refused"); return; }
            reportError(reason);
          }
        }
        if (!active) return;
        setConnection("reconnecting");
        timer = setTimeout(connect, Math.min(250 * 2 ** attempts++, 4000));
      };
      socket.onerror = () => setConnection("reconnecting");
    }
    connect();
    return () => { active = false; clearTimeout(timer); socket?.close(); };
  }, [api, me.id, session.demo, applyMessage, refresh, reportError]);

  const team = teams.find((item) => item.channels.some((channel) => channel.conversationId === selectedId));
  const channel = team?.channels.find((item) => item.conversationId === selectedId);
  const chat = chats.find((item) => item.conversationId === selectedId);
  useEffect(() => {
    if (dialog?.kind === "manageChat" && !chats.some((item) => item.id === dialog.chat.id)) setDialog(null);
  }, [chats, dialog]);
  const currentMessages = selectedId ? messages[selectedId] ?? [] : [];
  const root = currentMessages.find((message) => message.id === rootId && !message.replyToId);
  const title = channel?.name ?? chat?.name ?? "Chat";
  useEffect(() => {
    if (!ready || !selectedId) return;
    const generation = epoch.current;
    let active = true;
    const selectedTeam = teams.find((item) => item.channels.some((item) => item.conversationId === selectedId));
    const selectedChat = chats.find((item) => item.conversationId === selectedId);
    void Promise.all([
      api.request<Message[]>(`/conversations/${encodeURIComponent(selectedId)}/messages`),
      api.request<FileItem[]>(`/conversations/${encodeURIComponent(selectedId)}/files`),
      selectedTeam ? api.request<User[]>(`/teams/${encodeURIComponent(selectedTeam.id)}/members`)
        : Promise.resolve(users.filter((user) => selectedChat?.memberIds.includes(user.id))),
    ]).then(([nextMessages, nextFiles, nextMembers]) => {
      if (!active || generation !== epoch.current) return;
      setMessages((current) => {
        const merged = mergeMessageSnapshots(current[selectedId] ?? [], nextMessages)
          .map((message) => newestMessageSnapshot(messageSnapshot.current.get(message.id), message));
        for (const message of merged) messageSnapshot.current.set(message.id, message);
        return { ...current, [selectedId]: merged };
      });
      setFiles(nextFiles); setMembers(nextMembers);
      loadedHistories.current.add(selectedId);
    }).catch((reason) => { if (active && generation === epoch.current) reportError(reason); });
    return () => { active = false; };
  }, [api, selectedId, ready, version, teams, chats, users, reportError]);

  useEffect(() => {
    if (!ready || page !== "activity") return;
    const generation = epoch.current;
    let active = true;
    const ids = [...authorizedConversations.current].filter((id) => !loadedHistories.current.has(id));
    void Promise.all(ids.map(async (id) => {
      const history = await api.request<Message[]>(`/conversations/${encodeURIComponent(id)}/messages`);
      if (!active || generation !== epoch.current) return;
      loadedHistories.current.add(id);
      setMessages((current) => {
        const merged = mergeMessageSnapshots(current[id] ?? [], history)
          .map((message) => newestMessageSnapshot(messageSnapshot.current.get(message.id), message));
        for (const message of merged) messageSnapshot.current.set(message.id, message);
        return { ...current, [id]: merged };
      });
    })).catch((reason) => { if (active && generation === epoch.current) reportError(reason); });
    return () => { active = false; };
  }, [api, page, ready, version, reportError]);

  useEffect(() => {
    if (!ready || page !== "directory") return;
    const generation = epoch.current;
    let active = true;
    void Promise.all(teams.map(async (item) => {
      const teamMembers = await api.request<User[]>(`/teams/${encodeURIComponent(item.id)}/members`);
      return [item.id, teamMembers.length] as const;
    })).then((counts) => {
      if (active && generation === epoch.current) setTeamMemberCounts(Object.fromEntries(counts));
    }).catch((reason) => { if (active && generation === epoch.current) reportError(reason); });
    return () => { active = false; };
  }, [api, page, ready, teams, reportError]);

  function navigate(conversationId: string, thread?: string) {
    setSelectedId(conversationId); setRootId(thread ?? null); setPage("conversation"); setTab("conversation");
    if (conversationId !== selection.current) { setMembers([]); setFiles([]); }
    location.hash = new URLSearchParams({ conversation: conversationId, ...(thread ? { thread } : {}) }).toString();
  }
  useEffect(() => {
    const changed = () => {
      const value = route();
      if (value.get("view") === "teams") setPage("directory");
      else if (value.get("view") === "activity") {
        setPage("activity"); setSelectedNoticeId(value.get("notice"));
        if (value.get("conversation")) setSelectedId(value.get("conversation"));
        setRootId(value.get("thread")); setTab("conversation");
      }
      else if (value.get("conversation")) {
        setSelectedId(value.get("conversation")); setRootId(value.get("thread")); setPage("conversation");
      }
    };
    window.addEventListener("hashchange", changed);
    const shortcut = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.altKey && event.key.toLowerCase() === "e") {
        event.preventDefault(); searchInput.current?.focus();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => { window.removeEventListener("hashchange", changed); window.removeEventListener("keydown", shortcut); };
  }, []);
  function openThread(message: Message) {
    if (page !== "activity") navigate(message.conversationId, message.replyToId ?? message.id);
    else {
      const thread = message.replyToId ?? message.id;
      setRootId(thread);
      location.hash = new URLSearchParams({ view: "activity", conversation: message.conversationId, thread,
        ...(selectedNoticeId ? { notice: selectedNoticeId } : {}) }).toString();
    }
  }
  function selectNotice(notice: Notice) {
    const conversationId = notice.message.conversationId;
    const isChannel = teams.some((item) => item.channels.some((item) => item.conversationId === conversationId));
    const thread = isChannel ? notice.message.replyToId ?? notice.message.id : undefined;
    setSelectedId(conversationId); setRootId(thread ?? null); setPage("activity"); setTab("conversation");
    setSelectedNoticeId(notice.id); setReadNotices((current) => new Set(current).add(notice.id));
    if (conversationId !== selection.current) { setMembers([]); setFiles([]); }
    location.hash = new URLSearchParams({ view: "activity", conversation: conversationId, notice: notice.id,
      ...(thread ? { thread } : {}) }).toString();
  }
  function openActivity() {
    const selected = activityItems.find((notice) => notice.id === selectedNoticeId);
    if (selected) selectNotice(selected);
    else {
      setPage("activity"); setSelectedNoticeId(null);
      location.hash = "view=activity";
    }
  }
  function newChat() {
    setPage("new-chat"); setRootId(null); setChatUsers([]); setChatQuery(""); setChatName(""); setNewGroup(false);
    pendingChat.current = null;
  }
  function toggleTeam(id: string) {
    setCollapsed((current) => {
      const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next;
    });
  }
  async function createChat() {
    const key = JSON.stringify({ members: chatUsers.map((user) => user.id).sort(), name: chatName.trim() });
    if (pendingChat.current?.key === key) return pendingChat.current.chat;
    setCreating(true);
    try {
      const value = await api.request<Chat>("/chats", "POST", {
        memberIds: chatUsers.map((user) => user.id), ...(chatName.trim() ? { name: chatName } : {}),
      });
      pendingChat.current = { key, chat: value };
      await refresh();
      return value;
    }
    finally { setCreating(false); }
  }
  const teamMenu = (item: Team) => <Menu positioning={{ position: "below", align: "start" }}>
    <MenuTrigger disableButtonEnhancement><button className="row-more" aria-label={`More options for ${item.name}`}><MoreHorizontal20Regular /></button></MenuTrigger>
    <MenuPopover className="team-menu management-team-menu" data-testid="team-menu"><MenuList>
      <MenuItem icon={collapsed.has(item.id) ? <Eye20Regular /> : <EyeOff20Regular />}
        onClick={() => toggleTeam(item.id)}>{collapsed.has(item.id) ? "Show channels" : "Hide channels"}</MenuItem>
      <MenuDivider />
      <MenuItem icon={<PersonAdd20Regular />} disabled={item.ownerId !== me.id}
        onClick={() => setDialog({ kind: "addMember", team: item })}>Add member</MenuItem>
      <MenuItem icon={<ChannelAdd20Regular />} disabled={item.ownerId !== me.id}
        onClick={() => setDialog({ kind: "addChannel", team: item })}>Add channel</MenuItem>
      <MenuItem icon={<Settings20Regular />} disabled={item.ownerId !== me.id}
        onClick={() => setDialog({ kind: "manageTeam", team: item })}>Manage team</MenuItem>
      <MenuItem icon={<Tag20Regular />} disabled>Manage tags</MenuItem>
      <MenuItem icon={<Link20Regular />} disabled={!item.channels.length} onClick={() => {
        const link = new URL(location.href);
        link.hash = new URLSearchParams({ conversation: item.channels[0].conversationId }).toString();
        void navigator.clipboard.writeText(link.href).catch(reportError);
      }}>Copy link</MenuItem>
      <MenuItem icon={<Archive20Regular />} disabled>Archive team</MenuItem><MenuDivider />
      <MenuItem icon={<PersonArrowLeft20Regular />} disabled>Leave team</MenuItem>
      <MenuItem icon={<Delete20Regular />} disabled>Delete team</MenuItem>
    </MenuList></MenuPopover>
  </Menu>;
  const visibleChats = chats.filter((item) => item.name.toLowerCase().includes(search.toLowerCase()) &&
    (filter !== "unread" || !!lastRead[item.conversationId]));
  const visibleTeams = teams.filter((item) => `${item.name} ${item.channels.map((channel) => channel.name).join(" ")}`
    .toLowerCase().includes(search.toLowerCase()));
  const activityItems = messageNotices(Object.values(messages).flat(), me, reactionTimes);
  const directoryTeams = teams.filter((item) => (directoryFilter === "all" ||
    (directoryFilter === "owned" ? item.ownerId === me.id : item.ownerId !== me.id)) &&
    item.name.toLocaleLowerCase().includes(directoryQuery.toLocaleLowerCase()))
    .sort((a, b) => (directoryDescending ? -1 : 1) * a.name.localeCompare(b.name));
  const sharedProps = {
    api, me, messages: currentMessages, channel: !!channel,
    groupChat: chat?.conversationType === "groupChat", onChange: applyMessage,
    dark, typing: Object.values(typing).filter((event) => event.conversationId === selectedId),
    onThread: openThread, onError: reportError,
    onMarkUnread: (message: Message) => setLastRead((current) => ({ ...current, [message.conversationId]: message.id })),
    lastReadId: selectedId ? lastRead[selectedId] : undefined,
  };
  const showConversation = ready && selectedId && (channel || chat) && page === "conversation";
  const showActivityConversation = ready && page === "activity" && selectedNoticeId && selectedId && (channel || chat);

  return <div className={`workspace ${page === "activity" ? "activity-workspace" : ""} ${dark ? "dark" : ""}`} data-testid="authenticated"
    data-auth-source={session.mode} data-connection={connection}>
    <header className="app-topbar">
      <button className="waffle" aria-label="App launcher" disabled><Apps20Regular /></button>
      <button className="sidebar-layout-button" aria-label="Sidebar layout" disabled><PanelLeft20Regular /></button>
      <span className="simulator-label">Agent Chat Simulator</span>
      <label className="global-search"><Search20Regular /><input ref={searchInput} aria-label="Search chats and channels"
        placeholder="Search chats and channels (Ctrl+Alt+E)" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      <div className="topbar-actions"><button onClick={onTheme} aria-label={dark ? "Use light theme" : "Use dark theme"}
        title={dark ? "Use light theme" : "Use dark theme"}><Settings20Regular /></button>
        <Menu><MenuTrigger disableButtonEnhancement><button className="profile-button" aria-label="Your profile">
          <Avatar name={me.displayName} size={32} color="colorful" /></button></MenuTrigger>
          <MenuPopover><MenuList><div className="profile-info"><strong>{me.displayName}</strong><span>Role: {me.roles.join(", ")}</span></div>
            <MenuDivider /><MenuItem onClick={onSwitchAccount}>Switch account</MenuItem>
            <MenuItem onClick={onTheme}>{dark ? "Light theme" : "Dark theme"}</MenuItem>
            {me.roles.includes("Admin") && <>
              <MenuItem onClick={() => setDialog({ kind: "preregister" })}>Preregister participant</MenuItem>
              <MenuItem onClick={() => setAgentDialogOpen(true)}>Manage AI agents</MenuItem>
            </>}
          </MenuList></MenuPopover></Menu>
      </div>
    </header>
    <nav className="app-rail" aria-label="Apps">
      <button className={page === "activity" ? "active" : ""} onClick={openActivity} aria-label="Activity">
        <span className="rail-icon"><Alert20Regular />{activityItems.some((notice) => !readNotices.has(notice.id)) &&
          <span className="notification-dot" />}</span><span>Activity</span></button>
      <button className={page !== "activity" ? "active" : ""} onClick={() => selectedId ? navigate(selectedId) : newChat()} aria-label="Chat">
        <Chat20Regular /><span>Chat</span></button>
      <button disabled aria-label="Calendar"><Calendar20Regular /><span>Calendar</span></button>
      <button disabled aria-label="Calls"><Call20Regular /></button>
      <button disabled aria-label="Files app"><Cloud20Regular /><span>Files</span></button>
      <button disabled aria-label="Copilot app"><Sparkle20Regular /></button>
      <button disabled aria-label="Apps"><Apps20Regular /><span>Apps</span></button>
      <button disabled aria-label="More apps"><MoreHorizontal20Regular /></button>
    </nav>
    <aside className={`sidebar ${page === "activity" ? "management-activity-sidebar" : ""}`}>
      <div className="sidebar-heading"><h1>{page === "activity" ? "Activity" : "Chat"}</h1>
        <button aria-label={page === "activity" ? "More activity options" : "More chat options"} disabled><MoreHorizontal20Regular /></button>
        <button aria-label={page === "activity" ? "Find activity" : "Find chats"} onClick={() => searchInput.current?.focus()}><Search20Regular /></button>
        {page !== "activity" && <button aria-label="New message" onClick={newChat}><Compose20Regular /></button>}</div>
      {page === "activity" ? <ActivityFeed notices={activityItems} selectedId={selectedNoticeId}
        readIds={readNotices} filter={activityFilter} onFilter={setActivityFilter} onSelect={selectNotice}
        locationLabel={(message) => {
          const item = teams.find((team) => team.channels.some((channel) => channel.conversationId === message.conversationId));
          return { team: item?.name, conversation: item?.channels.find((channel) =>
            channel.conversationId === message.conversationId)?.name ??
              chats.find((chat) => chat.conversationId === message.conversationId)?.name ?? "Conversation" };
        }} /> : <>
        <div className="sidebar-filters">
          {["unread", "channels", "chats"].map((item) => <button key={item} className={filter === item ? "selected" : ""}
            onClick={() => setFilter(filter === item ? "all" : item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}
          <button aria-label="More filters" disabled><ChevronDown16Regular /></button>
        </div>
        <div className="sidebar-scroll">
          <div className="quick-views">
            <button disabled><Sparkle20Regular />Copilot</button>
            <div className="quick-section-title"><ChevronDown16Regular />Quick views</div>
            <button onClick={openActivity}><ChatMultiple20Regular />Followed threads</button>
            <button disabled><Compose20Regular />Drafts</button>
          </div>
          <div className="sidebar-section-title"><ChevronDown16Regular />Favorites</div>
          <div className="sidebar-profile"><Avatar name={me.displayName} size={24} color="colorful" />
            <span>{me.displayName} (You)</span></div>
          {filter !== "channels" && <>
            <div className="sidebar-section-title"><ChevronDown16Regular />Chats</div>
            {page === "new-chat" && <button className="sidebar-chat selected" onClick={() => setPage("new-chat")}>
              <Chat20Regular /><span>New message</span></button>}
            {visibleChats.map((item) => <button key={item.id} className={`sidebar-chat ${selectedId === item.conversationId && page === "conversation" ? "selected" : ""}`}
              onClick={() => navigate(item.conversationId)} data-conversation-id={item.conversationId}>
              <Avatar name={item.name} size={24} shape={item.conversationType === "groupChat" ? "square" : "circular"} color="colorful" />
              <span>{item.name}</span>{lastRead[item.conversationId] && <span className="unread-dot" />}
            </button>)}
          </>}
          {filter !== "chats" && <><div className="sidebar-section-title"><ChevronDown16Regular />Teams and channels</div>
            {visibleTeams.map((item) => <div key={item.id} className="sidebar-team" data-team-id={item.id}>
              <div className="team-row">
                <button className="team-expander" aria-label={`${collapsed.has(item.id) ? "Expand" : "Collapse"} ${item.name}`}
                  onClick={() => toggleTeam(item.id)}>{collapsed.has(item.id) ? <ChevronRight16Regular /> : <ChevronDown16Regular />}</button>
                <button className="team-name" onClick={() => item.channels[0] && navigate(item.channels[0].conversationId)}>
                  <Avatar name={item.name} shape="square" size={24} color="colorful" /><span>{item.name}</span></button>
                {teamMenu(item)}
              </div>
              {!collapsed.has(item.id) && <div className="channel-rows">{item.channels.map((item) =>
                <button key={item.id} className={selectedId === item.conversationId && page === "conversation" ? "selected" : ""}
                  onClick={() => navigate(item.conversationId)} data-conversation-id={item.conversationId}>
                  <span>{item.name}</span></button>)}</div>}
            </div>)}</>}
          <button className={`teams-directory-link ${page === "directory" ? "selected" : ""}`}
            onClick={() => { setPage("directory"); location.hash = "view=teams"; }}>See all your teams</button>
        </div>
      </>}
    </aside>
    <main className={`main-panel ${root && page === "conversation" ? "thread-open" : ""}`}>
      {error && <div className="error-banner" role="alert"><span>{error}</span>
        {me.roles.includes("Admin") && error.startsWith("Agent delivery failed.") &&
          <button onClick={() => setAgentDialogOpen(true)}>Review agent deliveries</button>}
        <button aria-label="Dismiss error" onClick={() => setError("")}><Dismiss20Regular /></button></div>}
      {session.mode === "test-oidc" && <div className="test-mode-label">LOCAL TEST IDP · synthetic identities only</div>}
      {!ready && <div className="loading-state" role="status">Loading authorized conversations…</div>}
      {ready && page === "directory" && <div className="management-directory-page">
        <header className="management-directory-heading"><h2>Your teams and channels</h2>
          <button className="management-directory-tab" aria-current="page">Teams</button>
          <button disabled>Pending invites</button>
          <Button appearance="subtle" icon={<PeopleAdd20Regular />} onClick={() => setDialog({ kind: "createTeam" })}>Create team</Button></header>
        <div className="management-directory-controls">
          <div className="management-directory-filters" aria-label="Filter teams">
            <button aria-pressed={directoryFilter === "all"} onClick={() => setDirectoryFilter("all")}>All</button>
            <button aria-pressed={directoryFilter === "owned"} onClick={() => setDirectoryFilter("owned")}>Teams you own</button>
            <button aria-pressed={directoryFilter === "member"} onClick={() => setDirectoryFilter("member")}>Teams you're in</button>
          </div>
          <button className="management-directory-sort" onClick={() => setDirectoryDescending(!directoryDescending)}>
            Sort: {directoryDescending ? "Z-A" : "A-Z"}<ChevronDown16Regular /></button>
          <label className="management-directory-search"><input aria-label="Search teams" placeholder="Search teams"
            value={directoryQuery} onChange={(event) => setDirectoryQuery(event.target.value)} /></label>
        </div>
        <div className="management-directory-list" aria-label="Your teams">
          {directoryTeams.map((item) => <div className="management-directory-team" data-team-id={item.id} key={item.id}>
            <button disabled={!item.channels.length} onClick={() => navigate(item.channels[0].conversationId)}>
              <Avatar name={item.name} size={32} color="colorful" />
              <span className="management-directory-team-info"><strong>{item.name}</strong>
                <span className="management-directory-team-meta"><span>Private</span>
                  {teamMemberCounts[item.id] !== undefined && <span>{teamMemberCounts[item.id]} {teamMemberCounts[item.id] === 1 ? "person" : "people"}</span>}
                  <span>{item.channels.length} {item.channels.length === 1 ? "channel" : "channels"}</span></span></span></button>
            <span className="management-directory-role">{item.ownerId === me.id ? "Owner" : "Member"}</span>
            {teamMenu(item)}</div>)}
          {!directoryTeams.length && <p>{!teams.length ? "Create a team to start collaborating." : "No teams match these filters."}</p>}
        </div>
      </div>}
      {ready && page === "new-chat" && <div className="new-chat-page">
        <div className="new-chat-to"><strong>To:</strong>{chatUsers.map((user) => <button key={user.id}
          onClick={() => setChatUsers((current) => current.filter((item) => item.id !== user.id))}>{user.displayName}<Dismiss20Regular /></button>)}
          <input aria-label="To: participants" placeholder="Type a name or email" value={chatQuery} onChange={(event) => setChatQuery(event.target.value)} />
          <button aria-label="Name group chat" onClick={() => setNewGroup(!newGroup)}>{newGroup ? <ChevronUp16Regular /> : <ChevronDown16Regular />}</button>
          {chatQuery.trim() && <div className="chat-suggestions" role="listbox" aria-label="Chat participants">
            {users.filter((user) => user.id !== me.id && !chatUsers.some((item) => item.id === user.id) &&
              `${user.displayName} ${user.upn}`.toLowerCase().includes(chatQuery.toLowerCase())).map((user) =>
              <button key={user.id} role="option" aria-selected={false} onClick={() => {
                setChatUsers((current) => [...current, user]); setChatQuery("");
              }}><Avatar name={user.displayName} size={32} color="colorful" /><span>{user.displayName}<small>{user.upn}</small></span></button>)}
          </div>}
        </div>
        {(newGroup || chatUsers.length > 1) && <label className="group-name">Group name:
          <input aria-label="Group name" aria-required={chatUsers.length > 1}
            value={chatName} onChange={(event) => setChatName(event.target.value)} /></label>}
        <div className="new-chat-illustration"><div><Emoji value="💬" size={92} /><Emoji value="👋" size={80} /></div>
          <h3>You're starting a new conversation</h3><p>Choose participants from the simulator directory.</p>
          {chatUsers.length > 0 && <Button appearance="primary" disabled={creating || (chatUsers.length > 1 && !chatName.trim())}
            onClick={() => void createChat().then((value) => navigate(value.conversationId)).catch(reportError)}>Start conversation</Button>}
        </div>
        <Composer api={api} createConversation={() => createChat().then((value) => value.conversationId)}
          disabled={!chatUsers.length || creating || (chatUsers.length > 1 && !chatName.trim())} label="Type a message"
          users={[me, ...chatUsers]} onSent={(message) => { applyMessage(message); navigate(message.conversationId); }} onError={reportError} />
      </div>}
      {ready && page === "activity" && !showActivityConversation && <div className="activity-empty">
        <Alert20Regular /><h2>Your activity</h2><p>Select a notification to open its conversation.</p></div>}
      {showActivityConversation && <section className="management-activity-conversation" aria-label="Activity conversation">
        <header className="management-activity-conversation-header">
          <Avatar name={team?.name ?? title} shape={channel ? "square" : "circular"} size={32} color="colorful" />
          <div><h2>{title}</h2><span>{team?.name ?? chat?.name}</span></div>
          <button className="management-go-to-channel" onClick={() => navigate(selectedId, rootId ?? undefined)}>
            Go to {channel ? "channel" : "chat"}</button>
          <button aria-label="Find in activity conversation" disabled><Search20Regular /></button>
          <button aria-label="More activity conversation options" disabled><MoreHorizontal20Regular /></button>
        </header>
        {channel && !root ? <div className="loading-state" role="status">{messages[selectedId]
          ? "This thread is no longer available. Go to the channel to continue." : "Loading thread…"}</div> : <>
          <MessageList {...sharedProps} root={channel ? root : undefined} thread={!!channel} />
          <Composer key={`activity:${selectedId}:${rootId ?? ""}`} api={api} conversationId={selectedId} users={members}
            label={channel ? "Reply in thread" : "Type a message"} replyToId={channel ? root?.id : undefined}
            onSent={applyMessage} onError={reportError} />
        </>}
      </section>}
      {showConversation && <div className="conversation-layout">
        <section className="conversation-section">
          <header className="conversation-header">
            <Avatar name={team?.name ?? title} shape={channel || chat?.conversationType === "groupChat" ? "square" : "circular"}
              size={32} color="colorful" />
            <div className="conversation-heading"><h2>{title}</h2>{root && channel && <span>Conversation <ChevronDown16Regular /></span>}</div>
            {!root && <nav className="conversation-tabs" aria-label="Conversation tabs">
              <button className={tab === "conversation" ? "active" : ""} onClick={() => setTab("conversation")}>{channel ? "Conversation" : "Chat"}</button>
              <button className={tab === "files" ? "active" : ""} onClick={() => setTab("files")}>Shared</button>
              <button disabled>Storyline</button><button disabled aria-label="Add tab"><Add20Regular /></button>
            </nav>}
            <div className="conversation-header-actions">
              <button aria-label="Conversation members" title={`${members.length} members`}
                onClick={() => {
                  if (team?.ownerId === me.id) setDialog({ kind: "manageTeam", team });
                  else if (chat?.conversationType === "groupChat") setDialog({ kind: "manageChat", chat });
                }}
                disabled={team ? team.ownerId !== me.id : chat?.conversationType !== "groupChat"}>
                <People20Regular />{members.length > 2 && <span>{members.length}</span>}</button>
              <button aria-label="Find in conversation" disabled><Search20Regular /></button>
              <button aria-label="Pop out conversation" disabled><PanelRight20Regular /></button>
              <button aria-label="More conversation options" disabled><MoreHorizontal20Regular /></button>
            </div>
          </header>
          {channel && !root && threadsBanner && <div className="threads-banner"><Info20Filled /><span>Follow threads to stay on top of conversations.</span>
            <button disabled>Learn more</button><button aria-label="Dismiss threads tip" onClick={() => setThreadsBanner(false)}><Dismiss20Regular /></button></div>}
          {tab === "files" ? <SharedFiles key={selectedId} api={api} conversationId={selectedId} files={files}
            messages={currentMessages} onSent={applyMessage} onError={reportError} /> : <>
            <MessageList {...sharedProps} />
            <Composer key={selectedId} api={api} conversationId={selectedId} users={members}
              label={channel ? "Message in channel" : "Type a message"} onSent={applyMessage} onError={reportError} />
          </>}
        </section>
        {root && <aside className="thread-pane" aria-label="Thread">
          <header className="thread-header"><h2>Threads</h2><ChevronRight16Regular />
            <span className="thread-title">{root.text.replace(/<\/?at>/g, "")}</span>
            <button aria-label="Close thread" onClick={() => navigate(selectedId)}><Dismiss20Regular /></button></header>
          <MessageList {...sharedProps} root={root} thread />
          <Composer key={root.id} api={api} conversationId={selectedId} users={members}
            label="Reply in thread" replyToId={root.id} onSent={applyMessage} onError={reportError} />
        </aside>}
      </div>}
      {ready && page === "conversation" && !showConversation && <div className="welcome-state">
        <Chat20Regular /><h2>Welcome to the simulator</h2><p>Select a channel or start a conversation.</p>
        <Button appearance="primary" onClick={newChat}>New message</Button>
        <Button onClick={() => setDialog({ kind: "createTeam" })}>Create team</Button></div>}
    </main>
    {dialog && <TeamDialog key={`${dialog.kind}:${"team" in dialog ? dialog.team.id : "chat" in dialog ? dialog.chat.id : ""}`}
      state={dialog} api={api} users={users}
      onClose={() => setDialog(null)} onChanged={() => refresh()} onError={reportError} />}
    {agentDialogOpen && me.roles.includes("Admin") && <AgentDialog api={api}
      onClose={() => setAgentDialogOpen(false)} onChanged={() => refresh()} onError={reportError} />}
  </div>;
}
