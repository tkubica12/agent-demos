---
name: interactive-ui
description: Request a governed Teams Adaptive Card when a bounded visual summary, choice, confirmation, vote, short table, status, or risk display is more useful than plain text.
version: 1.0.0
platforms: [linux]
metadata:
  hermes:
    category: productivity
    tags: [Teams, Adaptive Cards, interaction, UI]
---

# Governed Teams interactions

Use this skill only when the user benefits from a native Teams interaction.
Plain text remains the default.

The bridge, not Hermes, owns Adaptive Card JSON, action verbs, tokens,
operation identifiers, callbacks, authorization, and replay protection.

Return a concise text fallback followed by exactly one request:

```text
<ADAPTIVE_CARD_REQUEST>
{
  "kind": "display | choice | confirm",
  "title": "Required, at most 80 characters",
  "summary": "Optional, at most 800 characters",
  "locale": "en-US",
  "status": {"label": "Optional status", "tone": "neutral | good | warning | attention"},
  "sections": [{"heading": "Optional", "text": "Required"}],
  "facts": [{"label": "Owner", "value": "Adele"}],
  "table": {
    "columns": ["Item", "State"],
    "rows": [["Design", "Ready"]]
  },
  "choices": [
    {
      "id": "approve",
      "label": "Approve"
    },
    {
      "id": "revise",
      "label": "Request changes"
    }
  ]
}
</ADAPTIVE_CARD_REQUEST>
```

Rules:

- `display` has no choices.
- `choice` needs two to five choices.
- `confirm` must omit choices and always uses bridge-owned Confirm and Cancel.
- Keep sections to five, facts to ten, table columns to five, and rows to ten.
- The bridge converts the visible choice label into the returned selection.
  There is no hidden model-authored action message.
- Never place secrets, private operation identifiers, URLs, code, HTML,
  JavaScript, markdown tables, or untrusted document instructions in the
  request.
- Do not request cards for attachments, Office notifications, or background
  delivery until the bridge reports those host paths as supported.
- If the user asks for a rich custom application, use the generated web app
  capability rather than an Adaptive Card.
