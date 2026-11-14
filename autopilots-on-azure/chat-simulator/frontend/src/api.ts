export type User = {
  id: string; displayName: string; upn: string; roles: string[]; authSource?: string;
  isAgent?: boolean; agentId?: string;
};
export type Agent = {
  id: string; userId: string; name: string; clientId: string; endpointUrl: string; enabled: boolean;
};
export type AgentDelivery = {
  id: string; agentId: string; conversationId: string; activityId: string; activityType: string;
  status: string; attempts: number; error: string | null; createdAt: string;
};
export type SuggestedAction = Record<string, unknown> & { type: string; title?: string; value?: unknown };
export type ActivityAttachment = {
  contentType: string; content?: Record<string, unknown>; contentUrl?: string; name?: string;
};
export type CardActionResponse = {
  activityId: string; invokeResponse?: unknown; message?: Message; submittedMessage?: Message | null;
};
export type TypingEvent = { conversationId: string; replyToId?: string | null; actor: User; expiresAt: string };
export type Channel = { id: string; name: string; conversationId: string; teamId: string };
export type Team = { id: string; name: string; ownerId: string; channels: Channel[] };
export type Chat = {
  id: string; name: string; conversationId: string; memberIds: string[];
  conversationType: "personal" | "groupChat";
};
export type FileItem = {
  id: string; name: string; size: number; contentType: string; conversationId: string;
  sha256: string; downloadUrl: string;
};
export type Mention = { userId: string; displayName: string };
export type Reaction = { emoji: string; count: number; userIds: string[]; users: User[] };
export type Message = {
  id: string; snapshotRevision: number; conversationId: string; replyToId: string | null; text: string;
  textFormat: "markdown" | "plain" | "extendedmarkdown"; author: User; createdAt: string; updatedAt: string | null;
  deleted: boolean; mentions: Mention[]; attachments: FileItem[]; reactions: Reaction[];
  activityAttachments?: ActivityAttachment[];
  suggestedActions?: { actions: SuggestedAction[]; to?: string[] } | null;
};
type SharedAuthConfig = {
  channelId: string;
  agentChannel?: { clientId: string; serviceUrl: string; tenantId: string };
};
export type EntraAuthConfig = SharedAuthConfig & {
  authMode: "entra";
  tenantId: string; clientId: string; scope: string; redirectUri: string;
  testAuth?: { issuer: string; tokenEndpoint: string; personas: User[] };
};
export type DemoAuthConfig = SharedAuthConfig & {
  authMode: "demo";
  demo: { expiresAt: string; banner: string };
};
export type AuthConfig = EntraAuthConfig | DemoAuthConfig;
export type RealtimeEvent = {
  type: string; conversationId?: string; teamId?: string; message?: Message; actor?: User; occurredAt?: string;
  replyToId?: string | null; expiresAt?: string;
};
export type GetToken = () => Promise<string>;

export class ApiError extends Error {
  constructor(readonly status: number, message: string, readonly diagnostics?: unknown) { super(message); }
}

export async function readApiResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const error: unknown = await response.json().catch(() => null);
    const detail = typeof error === "object" && error !== null && "detail" in error ? error.detail : error;
    const committedDelivery = typeof detail === "object" && detail !== null && "code" in detail
      && detail.code === "agent_delivery_failed" && "resourceCommitted" in detail && detail.resourceCommitted === true;
    const message = typeof detail === "string" ? detail : committedDelivery
      ? "Agent delivery failed. Your changes were saved; a simulator administrator can retry delivery."
      : typeof detail === "object" && detail !== null && "message" in detail && typeof detail.message === "string"
        ? detail.message : `Request failed (${response.status}).`;
    throw new ApiError(response.status, message, error);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export class Api {
  constructor(readonly getToken: GetToken, private readonly onError?: (error: ApiError) => void) {}

  async request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
    const token = await this.getToken();
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(`/api${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal,
    });
    return this.read<T>(response);
  }

  private async read<T>(response: Response): Promise<T> {
    try { return await readApiResponse<T>(response); }
    catch (error) {
      if (error instanceof ApiError) this.onError?.(error);
      throw error;
    }
  }

  async upload(conversationId: string, file: File): Promise<FileItem> {
    const data = new FormData();
    data.append("file", file);
    const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}/files`, {
      method: "POST", headers: { Authorization: `Bearer ${await this.getToken()}` }, body: data,
    });
    return this.read<FileItem>(response);
  }

  async download(file: FileItem): Promise<void> {
    const response = await fetch(`/api/files/${encodeURIComponent(file.id)}/content`, {
      headers: { Authorization: `Bearer ${await this.getToken()}` },
    });
    if (!response.ok) await this.read<never>(response);
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = file.name;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
