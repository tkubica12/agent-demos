---
name: teams-html-widget
description: Generate an interactive HTML widget inside a personal Teams message: custom layouts, filters, calculators, charts and forms, with a bounded selection sent back to Hermes.
version: 1.0.0
platforms: linux
metadata:
  hermes:
    category: productivity
    tags: [Teams, HTML, widgets, genUI]
---

# Inline generated UI

Use this for an interactive page inside a personal Teams message. Generate the
actual layout and client-side behavior, not an Adaptive Card or a hosted website.
Use a hosted generated web app only when the user needs a separate URL, external
resources, server application or multiple pages.

Return a concise plain-text fallback, then exactly one JSON request:

```text
<HTML_WIDGET_REQUEST>
{
  "title": "Priority explorer",
  "html": "<h2>Explore priorities</h2><label>Budget <input type=\"number\" id=\"budget\" data-widget-field=\"budget\" value=\"5\"></label><p id=\"estimate\"></p><button type=\"button\" data-hermes-action=\"discuss\">Discuss this plan with Hermes</button>",
  "css": "h2{margin-top:0}label{display:block;margin:16px 0}button{padding:10px 16px}input{font:inherit}",
  "javascript": "const budget=document.getElementById('budget'); const refresh=()=>document.getElementById('estimate').textContent='Illustrative capacity: '+Number(budget.value)*2+' items'; budget.addEventListener('input',refresh);refresh();",
  "actions": [{"id":"discuss","label":"Discuss this plan with Hermes"}]
}
</HTML_WIDGET_REQUEST>
```

## Authoring

- Return the request inline; creating a scratch file is not required. If you test
  calculation logic with tools, use only the permitted runtime workspace and
  report actual failures honestly. Do not invent paths or change write restrictions.
- Write a real HTML fragment, CSS and JavaScript. No document/head/body, script,
  style, meta, iframe, object, external URL or inline event-handler attributes.
  Register event listeners in `javascript`. Do not close script/style elements.
- The gateway executes your script only after the Teams host handshake. Use the
  DOM immediately; do not wait for DOMContentLoaded. It supplies resizing.
- All scripts/styles/data stay inline. No network, CDN, imports, remote images,
  nested frames, secrets, credentials or background effects. Do not ask for host
  permissions. Local SVG/charts, filtering, sorting, input validation, tabs and
  calculations are appropriate. State when numbers are illustrative.
- Use semantic headings, visible labels, keyboard-accessible buttons and readable
  spacing. Avoid fixed heights or viewport widths; the widget sizes to its content.
- Use the user's language. Clearly distinguish previews from completed actions.
- Do not use element IDs beginning with `autopilot-`; those belong to the gateway.
- Limits: title 100 characters; HTML 24,000; CSS 8,000; JavaScript 12,000; the full
  JSON request at most 48,000 UTF-8 bytes. Keep it smaller when practical.
- Do not mix this request with Adaptive Card or generated-app request markers.
  Attachment turns, notifications, group chats and background scheduled messages
  are not supported widget delivery paths.

## Buttons and forms

Local interactions stay in JavaScript and do not call the model.

For a choice that should continue the same Hermes conversation, declare at most
five `actions` with unique lowercase IDs and visible labels (80 characters).
Use `<button type="button" data-hermes-action="the-id">Visible label</button>`.
Label it honestly: the gateway sends that action label, not a hidden instruction.
Never use widget selections for approvals, purchases, deletion, access grants or
claims that an external change was made; those require the existing governed flow.

Fields marked `data-widget-field="field-name"` are included in the submission.
At most ten fields, names at most 60 characters, string values at most 600
characters each. Alternatively call `await hermes.submit("the-id", {field:"value"})`
from a user click handler and show errors. Do not call it automatically on load.

The gateway accepts one selection per widget. Duplicate calls do not run the model
again. After acceptance Hermes replies in the original personal chat; it may return
a fresh widget. Buttons expire after 30 minutes. Keep a plain-text fallback so the
user can continue the conversation if their Teams client cannot render the widget.

Do not implement MCP Apps initialization, postMessage, tools/call, tokens, routing,
or model-context updates. The fixed gateway bootstrap owns those. `domain` and
client-supplied metadata are not identity or authorization.
