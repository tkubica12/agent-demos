import { useEffect, useState } from "react";
import { Avatar, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface,
  DialogTitle, Input, Switch } from "@fluentui/react-components";
import type { Agent, AgentDelivery, Api, AuthConfig } from "./api";
import "./agents.css";

export function AgentDialog({ api, onClose, onChanged, onError }: {
  api: Api; onClose: () => void; onChanged: () => Promise<void>; onError: (error: unknown) => void;
}) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<Record<string, AgentDelivery[]>>({});
  const [channel, setChannel] = useState<AuthConfig["agentChannel"]>();
  useEffect(() => {
    let active = true;
    void api.request<Agent[]>("/agents").then((value) => { if (active) setAgents(value); }).catch(onError);
    void api.request<AuthConfig>("/config").then((value) => {
      if (active) setChannel(value.agentChannel);
    }).catch(onError);
    return () => { active = false; };
  }, [api, onError]);
  async function register() {
    setBusy(true);
    try {
      const agent = await api.request<Agent>("/agents", "POST", {
        name: name.trim(), clientId: clientId.trim(), endpointUrl: endpointUrl.trim(), enabled: true,
      });
      setAgents((current) => [...current, agent]);
      setName(""); setClientId(""); setEndpointUrl("");
      await onChanged();
    } catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  async function toggle(agent: Agent) {
    setBusy(true);
    try {
      const updated = await api.request<Agent>(`/agents/${encodeURIComponent(agent.id)}`, "PATCH", {
        enabled: !agent.enabled,
      });
      setAgents((current) => current.map((value) => value.id === updated.id ? updated : value));
      await onChanged();
    } catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  async function history(agent: Agent) {
    if (expanded === agent.id) { setExpanded(null); return; }
    setExpanded(agent.id);
    setBusy(true);
    try {
      const values = await api.request<AgentDelivery[]>(`/agents/${encodeURIComponent(agent.id)}/deliveries`);
      setDeliveries((current) => ({ ...current, [agent.id]: values }));
    } catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  async function retry(agent: Agent, delivery: AgentDelivery) {
    setBusy(true);
    try {
      await api.request(`/agents/${encodeURIComponent(agent.id)}/deliveries/${encodeURIComponent(delivery.id)}/retry`, "POST");
      const values = await api.request<AgentDelivery[]>(`/agents/${encodeURIComponent(agent.id)}/deliveries`);
      setDeliveries((current) => ({ ...current, [agent.id]: values }));
      await onChanged();
    } catch (error) { onError(error); }
    finally { setBusy(false); }
  }
  return <Dialog open onOpenChange={(_, value) => { if (!value.open && !busy) onClose(); }}>
    <DialogSurface className="agent-dialog"><DialogBody>
      <DialogTitle>Manage AI agents</DialogTitle>
      <DialogContent>
        <p>Register a Microsoft 365 Agents SDK endpoint. The agent keeps its own credentials;
          the simulator never displays or collects its client secret.</p>
        {channel?.clientId && <section className="agent-channel-settings" aria-label="Agent channel connection">
          <strong>Agent channel connection</strong>
          <span>Tenant: <code>{channel.tenantId}</code></span>
          <span>Channel client ID: <code>{channel.clientId}</code></span>
          <span>Connector service URL: <code>{channel.serviceUrl}</code></span>
          <small>The channel client ID is not the browser application's client ID.</small>
        </section>}
        <div className="registered-agents" aria-label="Registered AI agents">
          {agents.map((agent) => <div className="registered-agent" key={agent.id}><div className="agent-summary">
            <Avatar name={agent.name} size={32} color="colorful" />
            <span><strong>{agent.name}</strong><small>{agent.endpointUrl}</small><small>Client ID: {agent.clientId}</small></span>
            <Switch label={agent.enabled ? "Enabled" : "Disabled"} checked={agent.enabled}
              disabled={busy} aria-label={`Enable ${agent.name}`} onChange={() => void toggle(agent)} />
            <Button disabled={busy} onClick={() => void history(agent)}
              aria-expanded={expanded === agent.id} aria-label={`Delivery history for ${agent.name}`}>Deliveries</Button>
            </div>
            {expanded === agent.id && <div className="agent-deliveries" aria-label={`Deliveries for ${agent.name}`}>
              {deliveries[agent.id] ? deliveries[agent.id].length
                ? deliveries[agent.id].map((delivery) => <div key={delivery.id}>
                  <span><strong>{delivery.activityType}: {delivery.status}</strong>
                    <small>{delivery.attempts} attempts · {new Date(delivery.createdAt).toLocaleString()}</small>
                    {delivery.error && <small role="status">{delivery.error}</small>}</span>
                  {delivery.status === "failed" && <Button disabled={busy || !agent.enabled}
                    aria-label={`Retry delivery ${delivery.id}`} onClick={() => void retry(agent, delivery)}>Retry delivery</Button>}
                </div>) : <p>No deliveries recorded.</p>
                : <p role="status">{busy ? "Loading deliveries…" : "Delivery history has not loaded."}</p>}
            </div>}
          </div>)}
          {!agents.length && <p>No agents registered yet.</p>}
        </div>
        <form id="register-agent" onSubmit={(event) => { event.preventDefault(); void register(); }}>
          <h3>Register an agent</h3>
          <label className="form-field">Name<Input required aria-label="Agent name" maxLength={200}
            value={name} onChange={(_, value) => setName(value.value)} /></label>
          <label className="form-field">Entra client ID<Input required aria-label="Agent client ID" maxLength={36}
            pattern="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
            value={clientId} onChange={(_, value) => setClientId(value.value)} /></label>
          <label className="form-field">Messages endpoint<Input required type="url" aria-label="Agent messages endpoint"
            placeholder="https://your-agent.example/api/messages" value={endpointUrl}
            onChange={(_, value) => setEndpointUrl(value.value)} /></label>
        </form>
      </DialogContent>
      <DialogActions><Button disabled={busy} onClick={onClose}>Close</Button>
        <Button appearance="primary" type="submit" form="register-agent"
          disabled={busy || !name.trim() || !clientId.trim() || !endpointUrl.trim()}>Register agent</Button>
      </DialogActions>
    </DialogBody></DialogSurface>
  </Dialog>;
}
