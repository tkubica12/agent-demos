import { useRef, useState } from "react";
import {
  ArrowUpload20Regular, Clock20Regular, Document20Regular, Globe20Regular, Image20Regular,
} from "@fluentui/react-icons";
import type { Api, FileItem, Message } from "./api";

export function SharedFiles({ api, conversationId, files, messages, onSent, onError }: {
  api: Api; conversationId: string; files: FileItem[]; messages: Message[];
  onSent: (message: Message) => void; onError: (error: unknown) => void;
}) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("Recent");
  const [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const visible = files.filter((file) => file.name.toLowerCase().includes(query.toLowerCase()));
  async function upload(file: File) {
    if (file.size > 25 * 1024 * 1024) { onError(new Error("Files must be 25 MB or smaller.")); return; }
    setUploading(true);
    try {
      const uploaded = await api.upload(conversationId, file);
      onSent(await api.request<Message>(`/conversations/${encodeURIComponent(conversationId)}/messages`, "POST", {
        text: "", textFormat: "plain", attachmentIds: [uploaded.id],
      }));
    } catch (reason) { onError(reason); }
    finally { setUploading(false); if (input.current) input.current.value = ""; }
  }
  return <section className="files-page" aria-label="Shared files">
    <div className="shared-toolbar">
      <div className="shared-filters" role="tablist" aria-label="Shared content">
        <button role="tab" aria-selected={filter === "Recent"} onClick={() => setFilter("Recent")}><Clock20Regular />Recent</button>
        <button role="tab" aria-selected={filter === "Files"} onClick={() => setFilter("Files")}><Document20Regular />Files</button>
        <button role="tab" aria-selected={false} disabled><Globe20Regular />Links</button>
        <button role="tab" aria-selected={false} disabled><Image20Regular />Media</button>
      </div>
      <input className="shared-search" aria-label="Filter by keyword" placeholder="Filter by keyword"
        value={query} onChange={(event) => setQuery(event.target.value)} />
      <button className="shared-upload" disabled={uploading} onClick={() => input.current?.click()}>
        <ArrowUpload20Regular />{uploading ? "Uploading…" : "Upload"}</button>
      <input ref={input} type="file" hidden aria-label="Upload shared file" onChange={(event) => {
        const file = event.target.files?.[0]; if (file) void upload(file);
      }} />
    </div>
    <table className="shared-table">
      <colgroup><col className="shared-checkbox-column" /><col /><col className="shared-date-column" /></colgroup>
      <thead><tr><th><input type="checkbox" disabled aria-label="Select all files" /></th><th>Name</th><th>Date</th></tr></thead>
      <tbody>{visible.map((file) => {
        const sharedAt = messages.find((message) => message.attachments.some((item) => item.id === file.id))?.createdAt;
        return <tr key={file.id}>
          <td><input type="checkbox" disabled aria-label={`Select ${file.name}`} /></td>
          <td><button className="file-list-row" onClick={() => void api.download(file).catch(onError)}>
            <Document20Regular /><span>{file.name}</span></button></td>
          <td>{sharedAt ? <time dateTime={sharedAt}>{new Date(sharedAt).toLocaleDateString("en-US")}</time> : "—"}</td>
        </tr>;
      })}</tbody>
    </table>
    {!visible.length && <p>{query ? "No files match your search." : "No files have been shared in this conversation."}</p>}
  </section>;
}
