"""Generated Teams widgets with a fixed host protocol and narrowly scoped callbacks."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import time
import uuid
from html.parser import HTMLParser
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from microsoft_agents.activity import Activity


START = "<HTML_WIDGET_REQUEST>"
END = "</HTML_WIDGET_REQUEST>"
SUBMIT_TOOL = "autopilot.widget.submit"
MAX_BYTES = 48_000
ACTION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
POLICY = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; "
    "form-action 'none'; base-uri 'none'"
)


class FragmentValidator(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"html", "head", "body", "script", "style", "meta", "base", "link", "iframe", "object", "embed"}:
            raise ValueError(f"Widget HTML must be a fragment without {tag} elements.")
        for name, value in attrs:
            if name.startswith("on") or name in {"srcdoc", "formaction", "action", "target"}:
                raise ValueError("Widget HTML cannot contain inline handlers, navigation targets or form actions.")
            if name in {"src", "href", "xlink:href"} and not (
                name in {"href", "xlink:href"} and (value or "").startswith("#")
                or tag == "img" and name == "src" and (value or "").startswith("data:image/")
            ):
                raise ValueError("Widget HTML cannot reference external resources.")
            if name == "id" and (value or "").startswith("autopilot-"):
                raise ValueError("The autopilot- ID prefix belongs to the widget host.")

    handle_startendtag = handle_starttag


def _text(value: object, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > maximum:
        raise ValueError(f"Widget {field} must be {'nonempty ' if required else ''}text of at most {maximum} characters.")
    return value


def validate_spec(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"title", "html", "css", "javascript", "actions"}:
        raise ValueError("Widget request supports only title, html, css, javascript and actions.")
    spec = {
        "title": _text(value.get("title"), "title", 100),
        "html": _text(value.get("html"), "html", 24_000),
        "css": _text(value.get("css", ""), "css", 8_000, required=False),
        "javascript": _text(value.get("javascript", ""), "javascript", 12_000, required=False),
        "actions": [],
    }
    if "</style" in spec["css"].lower() or "</script" in spec["javascript"].lower():
        raise ValueError("Widget CSS/JavaScript cannot close its containing element.")
    FragmentValidator().feed(spec["html"])
    actions = value.get("actions", [])
    if not isinstance(actions, list) or len(actions) > 5:
        raise ValueError("Widget actions must be an array of at most five choices.")
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"id", "label"}:
            raise ValueError("Widget actions have only a visible id and label, never hidden instructions.")
        identifier = _text(action["id"], "action id", 32)
        if not ACTION_ID.fullmatch(identifier) or any(a["id"] == identifier for a in spec["actions"]):
            raise ValueError("Widget action identifiers must be valid and unique.")
        spec["actions"].append({"id": identifier, "label": _text(action["label"], "action label", 80)})
    if len(json.dumps(spec).encode()) > MAX_BYTES:
        raise ValueError("The generated widget exceeds the bounded message size.")
    return spec


def extract_request(response: str) -> tuple[str, dict[str, Any] | None]:
    if START not in response and END not in response:
        return response, None
    if response.count(START) != 1 or response.count(END) != 1 or response.index(START) >= response.index(END):
        raise ValueError("The response must contain exactly one complete HTML widget request.")
    start, end = response.index(START), response.index(END)
    raw = response[start + len(START):end]
    if len(raw.encode()) > MAX_BYTES:
        raise ValueError("The generated widget request is too large.")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError("The HTML widget request is invalid JSON.") from exc
    return (response[:start] + response[end + len(END):]).strip(), validate_spec(value)


def _cipher() -> Fernet:
    key = os.getenv("API_SERVER_KEY", "")
    if not key:
        raise RuntimeError("Widget action capabilities require a configured gateway credential.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("html-widget\0" + key).encode()).digest()))


def decode_submission(value: object, *, conversation_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    if not isinstance(value, dict) or value.get("name") != SUBMIT_TOOL:
        raise ValueError("This widget tool is not supported.")
    args = value.get("arguments")
    if not isinstance(args, dict) or set(args) - {"token", "values", "__msteams__"}:
        raise ValueError("Widget submission arguments are invalid.")
    try:
        token = _text(args.get("token"), "token", 12_000)
        payload = json.loads(_cipher().decrypt(token.encode(), ttl=1800))
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ValueError("The widget capability is invalid or expired.") from exc
    if (
        payload.get("purpose") != SUBMIT_TOOL
        or payload.get("workerId") != os.getenv("WORKER_ID", "")
        or payload.get("conversationId") != conversation_id
        or payload.get("expiresAtUnix", 0) <= time.time()
    ):
        raise ValueError("The widget capability does not belong to this conversation or Worker.")
    values = args.get("values", {})
    if not isinstance(values, dict) or len(values) > 10:
        raise ValueError("Widget submission allows at most ten text fields.")
    bounded = {
        _text(key, "field name", 60): _text(item, "field value", 600, required=False)
        for key, item in values.items()
    }
    return payload, bounded


def tool_result(*, data: dict[str, Any] | None = None, error: str = "") -> dict[str, Any]:
    return {
        "responseType": "htmlwidget/calltoolresult",
        "callToolResult": {
            "content": [{"type": "text", "text": error or "Selection accepted. Hermes will reply in this chat."}],
            "structuredContent": data or {},
            "isError": bool(error),
        },
    }


BOOTSTRAP = r"""
(() => {
  const config = CONFIG;
  const root = document.getElementById('autopilot-root');
  const status = document.getElementById('autopilot-status');
  const post = message => parent.postMessage({jsonrpc:'2.0',...message},'*');
  let ready = false, submitted = false, pending = null, sequence = 0;
  const timeout = setTimeout(() => {status.textContent='Teams did not initialize this widget.';},15000);
  const values = () => Object.fromEntries([...root.querySelectorAll('[data-widget-field]')].map(field => [
    field.dataset.widgetField, field.type==='checkbox' ? String(field.checked) : String(field.value ?? '')
  ]));
  const submit = (actionId, fields = values()) => {
    if (!ready) return Promise.reject(new Error('The widget is not initialized.'));
    if (!navigator.userActivation.isActive) return Promise.reject(new Error('Use a button to submit this selection.'));
    if (submitted) return Promise.reject(new Error('This widget already submitted a selection.'));
    if (pending) return pending.promise;
    if (!Object.hasOwn(config.tokens,actionId)) return Promise.reject(new Error('Unknown widget action.'));
    const id = 'selection-' + (++sequence);
    let resolve, reject;
    const promise = new Promise((ok,fail) => {resolve=ok;reject=fail;});
    const timer=setTimeout(() => {
      if (pending?.id===id) {pending=null;status.textContent='No acknowledgement. Retry the same selection; it will not execute twice.';reject(new Error('No gateway acknowledgement.'));}
    },20000);
    pending={id,promise,resolve,reject,timer};
    status.textContent='Sending selection to Hermes...';
    post({id,method:'tools/call',params:{name:'autopilot.widget.submit',arguments:{token:config.tokens[actionId],values:fields}}});
    return promise;
  };
  window.hermes=Object.freeze({submit});
  document.addEventListener('submit', event => event.preventDefault());
  document.addEventListener('click', event => {
    const button=event.target.closest('[data-hermes-action]');
    if (button) {event.preventDefault();submit(button.dataset.hermesAction).catch(error=>{status.textContent=error.message;});}
  });
  window.addEventListener('message', event => {
    if (event.source!==parent || event.data?.jsonrpc!=='2.0') return;
    const data=event.data;
    if (data.id==='init' && !ready) {
      if (data.error) {clearTimeout(timeout);status.textContent='Teams rejected widget initialization.';return;}
      if (!data.result) return;
      clearTimeout(timeout);ready=true;
      post({method:'ui/notifications/initialized',params:{}});
      const report=()=>post({method:'ui/notifications/size-changed',params:{height:root.getBoundingClientRect().height}});
      new ResizeObserver(report).observe(root);report();
      status.textContent='Interactive preview. Submitting a selection asks Hermes; it does not approve an external action.';
      try { MODEL_SCRIPT } catch (error) {status.textContent='Widget script failed: '+error.message;}
    }
    if (pending && data.id===pending.id) {
      const call=pending;pending=null;clearTimeout(call.timer);
      if (data.error || data.result?.isError) {
        const message=data.error?.message || data.result?.content?.[0]?.text || 'Gateway rejected the selection.';
        status.textContent=message;call.reject(new Error(message));return;
      }
      submitted=true;
      root.querySelectorAll('[data-hermes-action]').forEach(button=>button.disabled=true);
      status.textContent='Selection received. Hermes will reply in this chat.';
      call.resolve(data.result);
    }
  });
  post({id:'init',method:'ui/initialize',params:{
    protocolVersion:'2026-01-26',appInfo:{name:'hermes-generated-ui',version:'1.0.0'},appCapabilities:{}
  }});
})();
"""


def render_widget(
    spec: dict[str, Any], *, session_key: str, user_id: str, conversation_id: str,
    fallback: str = "",
) -> Activity:
    spec = validate_spec(spec)
    interaction_id = uuid.uuid4().hex
    tokens = {}
    for action in spec["actions"]:
        payload = {
            "purpose": SUBMIT_TOOL, "interactionId": interaction_id,
            "choiceId": action["id"], "choiceMessage": f"I selected: {action['label']}.",
            "sessionKey": session_key, "userId": user_id, "conversationId": conversation_id,
            "workerId": os.getenv("WORKER_ID", ""), "expiresAtUnix": time.time() + 1800,
        }
        tokens[action["id"]] = _cipher().encrypt(json.dumps(payload).encode()).decode()
    config = json.dumps({"tokens": tokens}).replace("<", "\\u003c")
    script = BOOTSTRAP.replace("CONFIG", config).replace("MODEL_SCRIPT", spec["javascript"])
    markup = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{html.escape(POLICY, quote=True)}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<style>body{{margin:0;font:16px system-ui}}#autopilot-root{{padding:16px;box-sizing:border-box}}'
        f'{spec["css"]}</style></head><body><main id="autopilot-root">'
        f'{spec["html"]}<p id="autopilot-status" role="status">Connecting to Teams...</p>'
        f'</main><script>{script}</script></body></html>'
    )
    payload = {
        "type": "widget/mcp-ui", "name": spec["title"], "html": markup,
        "domain": "https://teams.microsoft.com",
        "securityPolicy": {"connectDomains": [], "resourceDomains": [], "frameDomains": [], "baseUriDomains": []},
    }
    encoded = json.dumps(payload, ensure_ascii=True).replace("`", "\\u0060")
    text = f"{fallback or spec['title']}\n\n```html-widget\n{encoded}\n```"
    if len(text.encode()) > 100_000:
        raise ValueError("The final widget message is too large.")
    return Activity(type="message", text=text, text_format="extendedmarkdown")
