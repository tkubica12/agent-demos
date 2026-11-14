import hashlib
import math
import time
from urllib.parse import quote

from fastapi.testclient import TestClient
from microsoft_agents.activity import Activity

from backend.app import create_app
from backend import db


def test_teams_channels_membership_and_owner_invariant(client, accounts, resources):
    owner = accounts["owner"]["headers"]
    member = accounts["member"]["headers"]
    outsider = accounts["outsider"]["headers"]
    team = resources["team"]
    tid = team["id"]
    uid = accounts["member"]["user"]["id"]
    assert team["ownerId"] == accounts["owner"]["user"]["id"]
    assert team["channels"][0]["name"] == "General"
    assert len(client.get(f"/api/teams/{tid}/members", headers=owner).json()) == 2
    response = client.post(f"/api/teams/{tid}/channels", headers=owner, json={"name": "Design"})
    assert response.status_code == 201
    assert response.json()["conversationId"].startswith("19:")
    assert response.json()["conversationId"].endswith("@thread.tacv2")
    assert {t["id"] for t in client.get("/api/teams", headers=member).json()} == {tid}
    assert client.get("/api/teams", headers=outsider).json() == []
    assert client.post(f"/api/teams/{tid}/channels", headers=member, json={"name": "No"}).status_code == 403
    assert client.post(f"/api/teams/{tid}/members", headers=member,
                       json={"userId": accounts["outsider"]["user"]["id"]}).status_code == 403
    assert client.delete(f"/api/teams/{tid}/members/{uid}", headers=member).status_code == 403
    assert client.delete(f"/api/teams/{tid}/members/{team['ownerId']}", headers=owner).status_code == 409
    assert client.delete(f"/api/teams/{tid}/members/{uid}", headers=owner).status_code == 204
    assert client.get(f"/api/teams/{tid}", headers=member).status_code == 403
    assert client.get(f"/api/conversations/{resources['conversation']}/messages", headers=member).status_code == 403
    assert client.get("/api/teams", headers=member).json() == []


def test_every_openapi_resource_id_method_is_outsider_tested(idor_client, idor_app, accounts, resources, issuer):
    """New ID routes fail this test until an explicit, valid hostile request is added."""
    client, app = idor_client, idor_app
    agent_client_id = "20000000-0000-4000-8000-000000000002"
    response = client.post("/api/agents", headers=accounts["owner"]["headers"], json={
        "name": "Uninvited agent", "clientId": agent_client_id, "endpointUrl": "http://127.0.0.1:9102/api/messages",
    })
    assert response.status_code == 201
    agent = response.json()
    # A registered, signed application identity that is not in either private conversation.
    agent_headers = {"Authorization": f"Bearer {issuer.mint(overrides={
        'aud': app.state.settings.channel_client_id, 'azp': agent_client_id, 'scp': None, 'idtyp': 'app',
    })}"}
    with app.state.sessions() as session:
        delivery = db.AgentDelivery(agent_id=agent["id"], conversation_id=resources["conversation"],
                                    activity={"id": resources["message"]["id"], "type": "message"},
                                    created_at="2026-11-08T08:00:00Z")
        session.add(delivery)
        session.commit()
        delivery_id = delivery.id
    ids = {
        "teamId": resources["team"]["id"], "userId": accounts["member"]["user"]["id"],
        "chatId": resources["chat"]["id"], "conversationId": resources["conversation"],
        "messageId": resources["message"]["id"], "fileId": resources["file"]["id"],
        "emoji": quote("👍", safe=""),
        "agentId": agent["id"], "deliveryId": delivery_id, "activityId": resources["message"]["id"],
        "replyToId": resources["message"]["id"], "reactionType": "like",
        "attachmentId": resources["file"]["id"], "viewId": "original",
        "message_id": resources["message"]["id"], "index": 0, "capability": "invalid-frame-capability",
    }
    cases = {
        ("get", "/api/teams/{teamId}"): {},
        ("post", "/api/teams/{teamId}/channels"): {"json": {"name": "Stolen"}},
        ("get", "/api/teams/{teamId}/members"): {},
        ("post", "/api/teams/{teamId}/members"): {"json": {"userId": ids["userId"]}},
        ("delete", "/api/teams/{teamId}/members/{userId}"): {},
        ("get", "/api/chats/{chatId}"): {},
        ("get", "/api/chats/{chatId}/members"): {},
        ("post", "/api/chats/{chatId}/members"): {"json": {"userId": ids["userId"]}},
        ("delete", "/api/chats/{chatId}/members/{userId}"): {},
        ("get", "/api/conversations/{conversationId}/messages"): {},
        ("post", "/api/conversations/{conversationId}/messages"): {"json": {"text": "Forbidden"}},
        ("get", "/api/messages/{messageId}"): {},
        ("patch", "/api/messages/{messageId}"): {"json": {"text": "Stolen"}},
        ("delete", "/api/messages/{messageId}"): {},
        ("post", "/api/messages/{messageId}/reactions"): {"json": {"emoji": "👍"}},
        ("delete", "/api/messages/{messageId}/reactions/{emoji}"): {},
        ("get", "/api/conversations/{conversationId}/files"): {},
        ("post", "/api/conversations/{conversationId}/files"): {
            "files": {"file": ("untrusted.txt", b"no", "text/plain")},
        },
        ("get", "/api/files/{fileId}"): {},
        ("get", "/api/files/{fileId}/content"): {},
        ("get", "/api/agents/{agentId}"): {},
        ("patch", "/api/agents/{agentId}"): {"json": {"enabled": False}},
        ("get", "/api/agents/{agentId}/deliveries"): {},
        ("post", "/api/agents/{agentId}/deliveries/{deliveryId}/retry"): {},
        ("get", "/api/agent-files/{fileId}/content"): {"params": {"capability": "invalid-capability-for-explicit-hostile-test"}},
        ("post", "/api/messages/{messageId}/actions"): {"json": {"action": {"type": "Action.Submit", "data": {}}}},
        ("post", "/api/messages/{message_id}/widgets/{index}/frame"): {},
        ("post", "/api/messages/{message_id}/widgets/{index}/tools"): {
            "json": {"name": "autopilot.widget.submit", "arguments": {}},
        },
        ("get", "/api/widget-frames/{capability}"): {
            "headers": {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-origin"},
        },
        ("post", "/v3/conversations/{conversationId}/activities"): {
            "headers": agent_headers, "json": {"type": "message", "text": "Forbidden"},
        },
        ("post", "/v3/conversations/{conversationId}/activities/{replyToId}"): {
            "headers": agent_headers, "json": {"type": "message", "text": "Forbidden"},
        },
        ("put", "/v3/conversations/{conversationId}/activities/{activityId}"): {
            "headers": agent_headers, "json": {"type": "message", "text": "Stolen"},
        },
        ("delete", "/v3/conversations/{conversationId}/activities/{activityId}"): {"headers": agent_headers},
        ("put", "/v3/conversations/{conversationId}/activities/{activityId}/reactions/{reactionType}"): {
            "headers": agent_headers,
        },
        ("delete", "/v3/conversations/{conversationId}/activities/{activityId}/reactions/{reactionType}"): {
            "headers": agent_headers,
        },
        ("get", "/v3/conversations/{conversationId}/members"): {"headers": agent_headers},
        ("post", "/v3/conversations/{conversationId}/attachments"): {
            "headers": agent_headers, "json": {"name": "untrusted.txt", "type": "text/plain", "originalBase64": "bm8="},
        },
        ("get", "/v3/attachments/{attachmentId}"): {"headers": agent_headers},
        ("get", "/v3/attachments/{attachmentId}/views/{viewId}"): {"headers": agent_headers},
    }
    discovered = {
        (method, path)
        for path, methods in app.openapi()["paths"].items() if "{" in path
        for method in methods if method in {"get", "post", "put", "patch", "delete", "head", "options"}
    }
    assert discovered == set(cases), f"Missing IDOR cases: {discovered - set(cases)}"
    tested = set()
    for (method, template), arguments in cases.items():
        kwargs = {"headers": accounts["outsider"]["headers"], **arguments}
        response = client.request(method, template.format(**ids), **kwargs)
        assert response.status_code in {403, 404}, f"{method.upper()} {template} is not isolated"
        tested.add((method, template))
    assert tested == discovered
    # Also exercise conversation routes using a private chat, not only a channel.
    for method, suffix, arguments in [
        ("get", "messages", {}),
        ("post", "messages", {"json": {"text": "Forbidden"}}),
        ("get", "files", {}),
        ("post", "files", {"files": {"file": ("no.txt", b"no", "text/plain")}}),
    ]:
        response = client.request(method, f"/api/conversations/{resources['chat']['conversationId']}/{suffix}",
                                  headers=accounts["outsider"]["headers"], **arguments)
        assert response.status_code in {403, 404}


def test_personal_group_chat_and_discovery(client, accounts, resources):
    owner = accounts["owner"]["headers"]
    member_id = accounts["member"]["user"]["id"]
    admin_id = accounts["admin"]["user"]["id"]
    personal = resources["chat"]
    assert personal["conversationType"] == "personal"
    assert set(personal["memberIds"]) == {accounts["owner"]["user"]["id"], member_id}
    repeated = client.post("/api/chats", headers=owner, json={"memberIds": [member_id]}).json()
    assert repeated["id"] == personal["id"]
    assert client.post("/api/chats", headers=owner, json={"memberIds": [member_id, admin_id]}).status_code == 422
    response = client.post("/api/chats", headers=owner, json={
        "name": "Hackathon", "memberIds": [member_id, admin_id],
    })
    assert response.status_code == 201
    group = response.json()
    assert group["conversationType"] == "groupChat"
    assert len(group["memberIds"]) == 3
    assert client.get("/api/chats", headers=accounts["outsider"]["headers"]).json() == []
    assert len(client.get("/api/chats", headers=accounts["member"]["headers"]).json()) == 2
    assert client.post("/api/chats", headers=owner, json={"memberIds": ["unknown"]}).status_code == 422
    assert client.post("/api/chats", headers=owner,
                       json={"memberIds": [accounts["owner"]["user"]["id"]]}).status_code == 422
    for chat in (personal, group):
        response = client.post(f"/api/conversations/{chat['conversationId']}/messages",
                               headers=owner, json={"text": "Chat text"})
        assert response.status_code == 201
    activities = client.get("/api/activities/export", headers=owner).json()
    assert {a["conversation"]["conversationType"] for a in activities} == {"channel", "personal", "groupChat"}


def test_messages_threads_author_edits_tombstones_and_persistence(client, settings, accounts, resources):
    owner = accounts["owner"]["headers"]
    member = accounts["member"]["headers"]
    conversation = resources["conversation"]
    root = resources["message"]
    text = "**bold** *italic* `code` [link](https://example.test)\n- one\n- two"
    response = client.post(f"/api/conversations/{conversation}/messages", headers=owner,
                           json={"text": text, "replyToId": root["id"], "textFormat": "markdown"})
    assert response.status_code == 201
    reply = response.json()
    nested = client.post(f"/api/conversations/{conversation}/messages", headers=member,
                         json={"text": "reply to reply", "replyToId": reply["id"]}).json()
    assert nested["replyToId"] == root["id"]
    assert client.patch(f"/api/messages/{root['id']}", headers=member, json={"text": "Stolen"}).status_code == 403
    assert client.delete(f"/api/messages/{root['id']}", headers=member).status_code == 403
    edit = client.patch(f"/api/messages/{reply['id']}", headers=owner,
                        json={"text": "Changed", "textFormat": "plain"}).json()
    assert edit["updatedAt"] is not None
    assert edit["textFormat"] == "plain"
    assert edit["author"]["id"] == accounts["owner"]["user"]["id"]
    deletion = client.delete(f"/api/messages/{reply['id']}", headers=owner).json()
    assert deletion["deleted"] is True
    assert deletion["text"] == ""
    assert client.patch(f"/api/messages/{reply['id']}", headers=owner, json={"text": "Resurrect"}).status_code == 409
    assert client.post(f"/api/conversations/{conversation}/messages", headers=owner,
                       json={"text": "Spoof", "authorId": accounts["member"]["user"]["id"]}).status_code == 422
    another = client.post("/api/teams", headers=owner, json={"name": "Another"}).json()
    foreign_conversation = another["channels"][0]["conversationId"]
    assert client.post(f"/api/conversations/{foreign_conversation}/messages", headers=owner,
                       json={"text": "cross thread", "replyToId": root["id"]}).status_code == 422
    before = client.get(f"/api/conversations/{conversation}/messages", headers=owner).json()
    with TestClient(create_app(settings)) as restarted:
        after = restarted.get(f"/api/conversations/{conversation}/messages", headers=owner)
        assert after.status_code == 200
        assert after.json() == before
        activities = restarted.get("/api/activities/export", headers=owner).json()
    assert all(Activity.model_validate(a) for a in activities)
    thread = [a for a in activities if a["id"] == reply["id"]]
    assert [a["type"] for a in thread] == ["message", "messageUpdate", "messageDelete"]
    assert thread[0]["text"] == text
    assert thread[1]["text"] == "Changed"
    for activity in thread:
        assert activity["replyToId"] == root["id"]
        assert activity["conversation"]["id"] == f"{conversation};messageid={root['id']}"
        assert activity["channelData"] == {
            "team": {"id": resources["team"]["id"]},
            "channel": {"id": resources["team"]["channels"][0]["id"]},
            "tenant": {"id": settings.tenant_id},
        }
        assert activity["channelId"] == "msteams"


def test_emoji_reactions_mentions_and_authoritative_names(client, accounts, resources):
    owner = accounts["owner"]["headers"]
    member = accounts["member"]["headers"]
    mid = resources["message"]["id"]
    uid = accounts["member"]["user"]["id"]
    for emoji in ("👍", "❤️", "😆", "😮", "😢", "😠", "🧑🏽‍🚀", "🦄", "1️⃣", "🏳️‍🌈", "↔️", "⭐",
                  "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"):
        response = client.post(f"/api/messages/{mid}/reactions", headers=owner, json={"emoji": emoji})
        assert response.status_code == 200
        response = client.post(f"/api/messages/{mid}/reactions", headers=owner, json={"emoji": emoji})
        assert next(r for r in response.json()["reactions"] if r["emoji"] == emoji)["count"] == 1
    response = client.post(f"/api/messages/{mid}/reactions", headers=member, json={"emoji": "👍"})
    reaction = next(r for r in response.json()["reactions"] if r["emoji"] == "👍")
    assert reaction["count"] == 2
    assert {u["id"] for u in reaction["users"]} == {accounts["owner"]["user"]["id"], uid}
    response = client.delete(f"/api/messages/{mid}/reactions/{quote('👍', safe='')}", headers=owner)
    reaction = next(r for r in response.json()["reactions"] if r["emoji"] == "👍")
    assert reaction["userIds"] == [uid]
    assert client.delete(f"/api/messages/{mid}/reactions/{quote('👍', safe='')}", headers=owner).status_code == 200
    for bad in ("not emoji", "x" * 50, "👍<script>", "👍\n"):
        assert client.post(f"/api/messages/{mid}/reactions", headers=owner, json={"emoji": bad}).status_code == 422
    response = client.post(f"/api/conversations/{resources['conversation']}/messages", headers=owner, json={
        "text": "Hello <at>Untrusted name</at> 🦄", "mentions": [{"userId": uid, "displayName": "Untrusted name"}],
    })
    assert response.status_code == 201
    mention = response.json()
    assert mention["mentions"] == [{"userId": uid, "displayName": accounts["member"]["user"]["displayName"]}]
    assert "Untrusted name" not in mention["text"]
    response = client.post(f"/api/conversations/{resources['conversation']}/messages", headers=owner, json={
        "text": "No outsider mention", "mentions": [
            {"userId": accounts["outsider"]["user"]["id"], "displayName": "Outsider"},
        ],
    })
    assert response.status_code == 422
    exported = client.get("/api/activities/export", headers=owner).json()
    assert all(Activity.model_validate(a) for a in exported)
    activity = next(a for a in exported if a["id"] == mention["id"])
    entity = activity["entities"][0]
    assert entity["type"] == "mention"
    assert entity["mentioned"]["id"] == uid
    assert entity["text"] in activity["text"]
    reactions = [a for a in exported if a["type"] == "messageReaction"]
    assert any("reactionsAdded" in a for a in reactions)
    assert any("reactionsRemoved" in a for a in reactions)
    assert client.get("/api/activities/export", headers=accounts["outsider"]["headers"]).json() == []
