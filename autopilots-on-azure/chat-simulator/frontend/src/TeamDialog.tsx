import { useEffect, useState } from "react";
import {
  Avatar, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Input,
} from "@fluentui/react-components";
import type { Api, Chat, Team, User } from "./api";

export type DialogState = { kind: "createTeam" } | { kind: "preregister" } |
  { kind: "addChannel"; team: Team } | { kind: "addMember"; team: Team } | { kind: "manageTeam"; team: Team } |
  { kind: "manageChat"; chat: Chat };

export function TeamDialog({ state, api, users, onClose, onChanged, onError }: {
  state: DialogState; api: Api; users: User[]; onClose: () => void;
  onChanged: () => Promise<void>; onError: (error: unknown) => void;
}) {
  const [name, setName] = useState("");
  const [upn, setUpn] = useState("");
  const [selected, setSelected] = useState<User | null>(null);
  const [members, setMembers] = useState<User[]>([]);
  const [busy, setBusy] = useState(false);
  const managing = state.kind === "manageTeam" || state.kind === "manageChat";
  const memberPath = "team" in state ? `/teams/${encodeURIComponent(state.team.id)}/members`
    : state.kind === "manageChat" ? `/chats/${encodeURIComponent(state.chat.id)}/members` : null;
  const title = state.kind === "createTeam" ? "Create a team" : state.kind === "addChannel" ? "Add a channel"
    : state.kind === "preregister" ? "Preregister a participant" : state.kind === "manageTeam"
      ? `Manage ${state.team.name}` : state.kind === "manageChat" ? `Members of ${state.chat.name}`
        : `Add members to ${state.team.name}`;
  useEffect(() => {
    if (!memberPath) return;
    let active = true;
    void api.request<User[]>(memberPath)
      .then((value) => { if (active) setMembers(value); }).catch(onError);
    return () => { active = false; };
  }, [api, memberPath, onError]);
  async function submit() {
    setBusy(true);
    try {
      if (state.kind === "createTeam") await api.request("/teams", "POST", { name });
      else if (state.kind === "addChannel") await api.request(
        `/teams/${encodeURIComponent(state.team.id)}/channels`, "POST", { name });
      else if ((state.kind === "addMember" || state.kind === "manageChat") && selected && memberPath)
        await api.request(memberPath, "POST", { userId: selected.id });
      else if (state.kind === "preregister") await api.request("/users", "POST", { upn, displayName: name });
      else throw new Error("Select a registered simulator member.");
      await onChanged();
      onClose();
    } catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  async function remove(user: User) {
    if (!managing || !memberPath) return;
    setBusy(true);
    try {
      await api.request(`${memberPath}/${encodeURIComponent(user.id)}`, "DELETE");
      setMembers((current) => current.filter((item) => item.id !== user.id));
      await onChanged();
    } catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  return <Dialog open onOpenChange={(_, data) => { if (!data.open && !busy) onClose(); }}>
    <DialogSurface className={`team-dialog ${state.kind === "addMember" ? "add-member-dialog" : ""}`}>
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <DialogBody>
          <DialogTitle>{title}</DialogTitle>
          <DialogContent>
            {state.kind === "addMember" ? <>
              <p>Start typing a name or email to add people to your team. Choose a participant from the simulator
                directory. An administrator can preregister participants who have not signed in yet.
                No guest invitations or external messages are sent.</p>
              <Input aria-label="Type a name or email" placeholder="Type a name or email" appearance="filled-darker"
                value={selected?.displayName ?? name} onChange={(_, data) => { setName(data.value); setSelected(null); }} />
              {!selected && name.trim() && <div className="member-suggestions" role="listbox" aria-label="Registered participants">
                {users.filter((user) => !members.some((member) => member.id === user.id) &&
                  `${user.displayName} ${user.upn}`.toLowerCase().includes(name.toLowerCase())).map((user) =>
                  <button type="button" key={user.id} role="option" aria-selected={false} onClick={() => setSelected(user)}>
                    <Avatar name={user.displayName} size={32} color="colorful" />
                    <span>{user.displayName}<small>{user.upn}</small></span>
                    {user.isAgent && <span className="agent-badge">AI agent</span>}
                  </button>)}
              </div>}
            </> : managing ? <>
              <div className="manage-members">
              {members.map((user) => <div key={user.id}><Avatar name={user.displayName} size={32} color="colorful" />
                <span>{user.displayName}<small>{user.upn}</small></span>
                {user.isAgent && <span className="agent-badge">AI agent</span>}
                {state.kind === "manageTeam" && user.id === state.team.ownerId ? <span>Owner</span>
                  : <Button disabled={busy} onClick={() => void remove(user)} aria-label={`Remove ${user.displayName}`}>Remove</Button>}
              </div>)}
              </div>
              {state.kind === "manageChat" && <>
                <p>Any current group participant can add registered people or agents, remove participants, or leave.
                  Personal chat membership cannot be changed.</p>
                <Input aria-label="Add group participant" placeholder="Type a name or email"
                  value={selected?.displayName ?? name} onChange={(_, data) => { setName(data.value); setSelected(null); }} />
                {!selected && name.trim() && <div className="member-suggestions" role="listbox" aria-label="Registered participants">
                  {users.filter((user) => !members.some((member) => member.id === user.id) &&
                    `${user.displayName} ${user.upn}`.toLowerCase().includes(name.toLowerCase())).map((user) =>
                    <button type="button" key={user.id} role="option" aria-selected={false} onClick={() => setSelected(user)}>
                      <Avatar name={user.displayName} size={32} color="colorful" />
                      <span>{user.displayName}<small>{user.upn}</small></span>
                      {user.isAgent && <span className="agent-badge">AI agent</span>}
                    </button>)}
                </div>}
              </>}
            </> : <>
              <label className="form-field">{state.kind === "preregister" ? "Display name" : "Name"}
                <Input aria-label={state.kind === "preregister" ? "Display name" : "Name"} value={name}
                  onChange={(_, data) => setName(data.value)} required maxLength={200} /></label>
              {state.kind === "preregister" && <label className="form-field">UPN
                <Input aria-label="UPN" type="email" value={upn} onChange={(_, data) => setUpn(data.value)} required /></label>}
              {state.kind === "createTeam" && <p>You will be the owner. A General channel is created automatically.</p>}
            </>}
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" disabled={busy} onClick={onClose}>{managing ? "Close" : "Cancel"}</Button>
            {state.kind !== "manageTeam" && <Button appearance="primary" type="submit"
              disabled={busy || (state.kind === "addMember" || state.kind === "manageChat" ? !selected : !name.trim())}>
              {state.kind === "addMember" || state.kind === "manageChat" ? "Add" : state.kind === "preregister" ? "Register" : "Create"}
            </Button>}
          </DialogActions>
        </DialogBody>
      </form>
    </DialogSurface>
  </Dialog>;
}
