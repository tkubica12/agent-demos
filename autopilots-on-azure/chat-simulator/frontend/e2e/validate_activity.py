"""Validate the complete real browser database/export; never reads authentication material."""

import json
import os
import sqlite3
from collections import Counter
from importlib.metadata import version
from pathlib import Path

from microsoft_agents.activity import Activity

evidence = Path(os.environ["TSIM_E2E_EVIDENCE"])
sdk_version = version("microsoft-agents-activity")
assert sdk_version == "1.1.0"
exported = json.loads((evidence / "activities-export.json").read_text(encoding="utf-8"))
connection = sqlite3.connect(os.environ["TSIM_E2E_DATABASE"])
rows = connection.execute("SELECT activity FROM activity_revisions ORDER BY sequence").fetchall()
persisted = [json.loads(row[0]) for row in rows]
assert exported == persisted, "API export must include 100% of this E2E database's revisions in order"
assert exported
messages = connection.execute("SELECT id, reply_to_id, activity FROM messages ORDER BY sequence").fetchall()
message_ids = {row[0] for row in messages}
assert message_ids == {item["id"] for item in exported if item["type"] == "message"}
types = Counter()
conversations = Counter()
for activity in exported:
    Activity.model_validate(activity)
    types[activity["type"]] += 1
    conversation = activity["conversation"]
    kind = conversation["conversationType"]
    assert kind in {"personal", "groupChat", "channel"}
    conversations[kind] += 1
    assert activity["channelId"] == "msteams"
    if kind == "channel":
        base = conversation["id"].split(";messageid=")[0]
        assert base.startswith("19:") and base.endswith("@thread.tacv2")
        for field in ("team", "channel", "tenant"):
            assert activity["channelData"][field]["id"]
    if activity["type"] == "messageReaction":
        # A reaction's replyToId targets the reacted message; it is not a thread reply.
        assert activity["replyToId"] in message_ids
        assert activity.get("reactionsAdded") or activity.get("reactionsRemoved")
    elif activity.get("replyToId"):
        assert conversation["id"].endswith(";messageid=" + activity["replyToId"])
assert {"personal", "groupChat", "channel"} <= set(conversations)
assert {"message", "messageUpdate", "messageDelete", "messageReaction"} <= set(types)
assert any(activity["type"] == "message" and activity.get("replyToId") for activity in exported)
mentions = [
    (activity, entity)
    for activity in exported
    for entity in activity.get("entities", [])
    if entity.get("type") == "mention"
]
assert mentions
for activity, mention in mentions:
    assert mention["mentioned"]["id"]
    assert mention["text"] in activity["text"]
    assert mention["text"].startswith("<at>") and mention["text"].endswith("</at>")
result = {
    "totalDatabaseMessages": len(messages),
    "totalDatabaseRevisions": len(persisted),
    "exportedRevisions": len(exported),
    "validatedRevisions": len(exported),
    "coveragePercent": 100,
    "types": dict(types),
    "conversationTypes": dict(conversations),
    "mentionRevisions": len(mentions),
    "sdk": f"microsoft-agents-activity=={sdk_version} (uv.lock, uv run --locked)",
}
(evidence / "activity-validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
