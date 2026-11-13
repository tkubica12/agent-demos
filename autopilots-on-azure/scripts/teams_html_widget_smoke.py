"""Send a fixed HTML widget to an existing Worker's owner chat without deployment."""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import uuid

from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
from azure.identity import AzureCliCredential


def widget_activity(probe_id: str, *, interactive: bool = False) -> dict:
    if not probe_id or not probe_id.isascii() or not probe_id.isalnum():
        raise ValueError("The probe ID must contain only ASCII letters and digits.")
    counter = (
        '<button id="increment" disabled>Click to test</button><p id="count">Clicks: 0</p>'
        if interactive else ""
    )
    interaction = (
        """let clicks = 0;
document.getElementById('increment').disabled = false;
document.getElementById('increment').addEventListener('click', () => {
  document.getElementById('count').textContent = 'Clicks: ' + (++clicks);
});"""
        if interactive else ""
    )
    html = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body { margin: 0; font: 16px system-ui, sans-serif; color: #242424; background: white; }
#root { padding: 20px; border-top: 5px solid #006da0; box-sizing: border-box; }
h2 { margin: 0 0 12px; } p { margin: 12px 0; }
button { font: inherit; padding: 8px 16px; }
</style></head><body><main id="root">
<h2>Hermes 2: HTML inside Teams</h2>
<p>Fixed test content. No model call, external resources or business actions.</p>
<p>Probe: PROBE_ID</p>
COUNTER
<p id="status" role="status">Waiting for the Teams host handshake...</p>
</main><script>
const root = document.getElementById('root');
const status = document.getElementById('status');
const post = (message) => parent.postMessage({jsonrpc: '2.0', ...message}, '*');
let initialized = false;
const timer = setTimeout(() => {
  status.textContent = 'No Teams host response after 15 seconds.';
}, 15000);
window.addEventListener('message', (event) => {
  if (event.source !== parent || !event.data || event.data.jsonrpc !== '2.0') return;
  const data = event.data;
  if (data.id !== 'init' || initialized) return;
  if (data.error) {
    clearTimeout(timer);
    status.textContent = 'Teams host rejected initialization.';
    return;
  }
  if (!data.result || !data.result.protocolVersion) return;
  initialized = true;
  clearTimeout(timer);
  status.textContent = 'Teams MCP Apps handshake completed.';
  post({method: 'ui/notifications/initialized', params: {}});
  const reportSize = () => post({
    method: 'ui/notifications/size-changed',
    params: {height: root.getBoundingClientRect().height}
  });
  new ResizeObserver(reportSize).observe(root);
  reportSize();
  INTERACTION
});
post({id: 'init', method: 'ui/initialize', params: {
  protocolVersion: '2026-01-26',
  appInfo: {name: 'hermes-html-proof', version: '1.0.0'},
  appCapabilities: {}
}});
</script></body></html>""".replace("PROBE_ID", probe_id).replace("COUNTER", counter).replace("INTERACTION", interaction)
    payload = {
        "type": "widget/mcp-ui",
        "name": f"Hermes 2 HTML proof {probe_id}",
        "html": html,
        "domain": "https://teams.microsoft.com",
    }
    encoded = json.dumps(payload, ensure_ascii=True).replace("`", "\\u0060")
    return {
        "type": "message",
        "textFormat": "extendedmarkdown",
        "text": f"HTML widget technical test ({probe_id}).\n\n```html-widget\n{encoded}\n```",
    }


SEND_IN_GATEWAY = r"""
import asyncio,base64,json,os,sys
from pathlib import Path
config=json.loads(Path('/app/.sandbox-service.json').read_text())
environment=config['environment']
os.environ.update({key:str(value) for key,value in environment.items()})
sys.path.insert(0,config['workingDirectory'])
if environment.get('WORKER_ID')!=sys.argv[1] or environment.get('TRANSPORT_ROLE')!='gateway':
 raise RuntimeError('The selected Sandbox is not the expected Worker gateway.')
from microsoft_agents.activity import Activity,load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.fastapi import CloudAdapter
from microsoft_agents.hosting.core.app.proactive import Conversation
from bridge.proactive_delivery import send_proactive_activity
activity=Activity.model_validate(json.loads(base64.b64decode(sys.argv[2],validate=True)))
async def send():
 reference=json.loads(base64.b64decode(sys.argv[3],validate=True))
 if reference.get('boundary')!='one_to_one':
  raise RuntimeError('Widget smoke requires the existing owner personal chat.')
 conversation=Conversation.from_json_to_store_item(reference['conversation'])
 conversation.validate()
 user=conversation.conversation_reference.user
 if str(getattr(user,'aad_object_id','') or '')!=environment.get('WORKER_OWNER_ID'):
  raise RuntimeError('The stored personal chat does not attest the configured Worker owner.')
 adapter=CloudAdapter(connection_manager=MsalConnectionManager(**load_configuration_from_env(os.environ)))
 result=await send_proactive_activity(adapter,reference,activity=activity)
 print(json.dumps({**result,'rendering':'unverified; inspect the actual Teams client'}))
asyncio.run(send())
""".strip()

READ_OWNER_REFERENCE = r"""
import base64,json,sys
from pathlib import Path
config=json.loads(Path('/app/.sandbox-service.json').read_text())
environment=config['environment']
if environment.get('WORKER_ID')!=sys.argv[1]:
 raise RuntimeError('The selected runtime does not belong to the expected Worker.')
home=Path(environment.get('HERMES_HOME','/data/hermes'))
reference=json.loads((home/'local'/'delivery-references.json').read_text())['learning-owner-personal-chat']
print(base64.b64encode(json.dumps(reference).encode()).decode())
""".strip()

GATEWAY_LAUNCHER = r"""
import json,os,sys
from pathlib import Path
config=json.loads(Path('/app/.sandbox-service.json').read_text())
python=config['command'][0]
if not python.startswith('/app/releases/') or not python.endswith('/.venv/bin/python'):
 raise RuntimeError('The gateway launcher is not a source-release Python environment.')
os.chdir(config['workingDirectory'])
os.execv(python,[python,'-c',sys.argv[1],*sys.argv[2:]])
""".strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", default="hermes2")
    parser.add_argument("--interactive", action="store_true", help="Add an in-widget counter; no server callbacks.")
    parser.add_argument("--send", action="store_true", help="Send one real widget to the captured owner personal chat.")
    parser.add_argument("--subscription-id")
    parser.add_argument("--resource-group")
    parser.add_argument("--sandbox-group")
    parser.add_argument("--sandbox-id")
    parser.add_argument("--runtime-group")
    parser.add_argument("--runtime-id")
    parser.add_argument("--location")
    args = parser.parse_args()
    probe_id = uuid.uuid4().hex[:12]
    activity = widget_activity(probe_id, interactive=args.interactive)
    if not args.send:
        print(json.dumps(activity, indent=2))
        return
    if not all((
        args.subscription_id, args.resource_group, args.sandbox_group,
        args.sandbox_id, args.runtime_group, args.runtime_id, args.location,
    )):
        parser.error("--send requires the existing subscription/resource group, gateway/runtime group and ID, and location.")
    body = base64.b64encode(json.dumps(activity).encode()).decode()
    with AzureCliCredential(subscription=args.subscription_id, process_timeout=60) as credential:
        with SandboxGroupClient(
            endpoint_for_region(args.location), credential, subscription_id=args.subscription_id,
            resource_group=args.resource_group, sandbox_group=args.runtime_group,
        ) as group:
            runtime = group.get_sandbox_client(args.runtime_id)
            runtime.ensure_running(timeout=300)
            reference = runtime.exec(
                "python3 -c " + shlex.quote(READ_OWNER_REFERENCE) + " " + shlex.quote(args.worker),
            )
        if reference.exit_code != 0:
            raise RuntimeError(f"Reading the existing owner conversation failed before send: {reference.stderr}")
        encoded_reference = reference.stdout.strip()
        json.loads(base64.b64decode(encoded_reference, validate=True))
        command = "python3 -c " + shlex.quote(GATEWAY_LAUNCHER) + " " + shlex.quote(SEND_IN_GATEWAY)
        command += " " + " ".join(shlex.quote(value) for value in (args.worker, body, encoded_reference))
        with SandboxGroupClient(
            endpoint_for_region(args.location), credential, subscription_id=args.subscription_id,
            resource_group=args.resource_group, sandbox_group=args.sandbox_group,
        ) as group:
            sandbox = group.get_sandbox_client(args.sandbox_id)
            sandbox.ensure_running(timeout=300)
            # Exec does not retry the outbound send if its acknowledgement is lost.
            result = sandbox.exec(command)
    if result.exit_code != 0:
        raise RuntimeError(f"Widget send failed; inspect before retrying: {result.stderr}")
    accepted = json.loads(result.stdout)
    print(json.dumps({"worker": args.worker, "probeId": probe_id, **accepted}, indent=2))


if __name__ == "__main__":
    main()
