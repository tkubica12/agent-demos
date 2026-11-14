"""Synthetic isolated snapshot-ordering regressions, not real Entra evidence."""

import copy
from urllib.parse import quote

import httpx
from sqlalchemy import delete, func, select

from backend import db, domain as d
from backend.channel import ADAPTIVE_CARD
from tests.test_agent_channel import (
    AGENT_A, CARD, CHANNEL_ID, EXECUTE, agents, app, bearer, client, connector_message, installed,
)
from tests.test_agent_members_and_suggestions import SUGGESTED
from tests.test_realtime import authenticate, connect


def journal_revision(app, message_id):
    with app.state.sessions() as session:
        return session.scalar(select(func.max(db.ActivityRevision.sequence)).where(
            db.ActivityRevision.message_id == message_id,
        )) or 0


def test_human_http_and_ws_snapshots_follow_journal_revisions_without_editing_reactions(
    client, app, issuer, accounts, resources
):
    owner = accounts["owner"]["headers"]
    mid = resources["message"]["id"]
    previous = client.get(f"/api/messages/{mid}", headers=owner).json()
    assert type(previous["snapshotRevision"]) is int and previous["snapshotRevision"] > 0
    assert previous["snapshotRevision"] == journal_revision(app, mid)
    with connect(client, issuer, "member") as socket:
        authenticate(socket, issuer, "member")
        for method, route, arguments, event_type, changed in [
            ("post", f"/api/messages/{mid}/reactions", {"json": {"emoji": "👍"}}, "reaction.changed", True),
            ("post", f"/api/messages/{mid}/reactions", {"json": {"emoji": "👍"}}, "reaction.changed", False),
            ("delete", f"/api/messages/{mid}/reactions/{quote('👍', safe='')}", {}, "reaction.changed", True),
            ("patch", f"/api/messages/{mid}", {"json": {"text": "Actually edited"}}, "message.updated", True),
            ("delete", f"/api/messages/{mid}", {}, "message.deleted", True),
            ("delete", f"/api/messages/{mid}", {}, "message.deleted", False),
        ]:
            response = client.request(method, route, headers=owner, **arguments)
            assert response.status_code == 200
            current = response.json()
            event = socket.receive_json()
            assert event["type"] == event_type and event["message"] == current
            assert current["snapshotRevision"] == journal_revision(app, mid)
            assert (current["snapshotRevision"] > previous["snapshotRevision"]) is changed
            if not changed:
                assert current["snapshotRevision"] == previous["snapshotRevision"]
            if event_type == "reaction.changed":
                assert current["updatedAt"] == previous["updatedAt"] is None
            assert client.get(f"/api/messages/{mid}", headers=owner).json() == current
            previous = current


def test_agent_reaction_during_original_send_has_newer_revision_than_late_http_snapshot(
    client, app, issuer, accounts, agents
):
    owner = accounts["owner"]["headers"]
    response = client.post("/api/chats", headers=owner, json={
        "name": "Synchronous native reaction",
        "memberIds": [agents["A"]["userId"], accounts["member"]["user"]["id"]],
    })
    assert response.status_code == 201
    cid = response.json()["conversationId"]

    async def react(activity):
        if activity["type"] == "message" and activity["conversation"]["id"] == cid:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as callback:
                response = await callback.put(
                    f"/v3/conversations/{cid}/activities/{activity['id']}/reactions/like",
                    headers=bearer(issuer, CHANNEL_ID, AGENT_A),
                )
                assert response.status_code == 204, response.text

    app.state.protocol_fixture["onActivity"] = react
    with connect(client, issuer, "member") as socket:
        authenticate(socket, issuer, "member")
        response = client.post(f"/api/conversations/{cid}/messages", headers=owner,
                               json={"text": "React before my HTTP response"})
        assert response.status_code == 201, response.text
        late_response = response.json()
        created, reacted = socket.receive_json(), socket.receive_json()
        assert created["type"] == "message.created" and created["message"] == late_response
        assert reacted["type"] == "reaction.changed"
        latest = reacted["message"]
        assert latest["id"] == late_response["id"]
        assert late_response["reactions"] == []
        assert latest["reactions"][0]["emoji"] == "👍"
        assert latest["reactions"][0]["userIds"] == [agents["A"]["userId"]]
        assert latest["updatedAt"] == late_response["updatedAt"] is None
        assert latest["snapshotRevision"] > late_response["snapshotRevision"]
        assert latest["snapshotRevision"] == journal_revision(app, latest["id"])
        assert client.get(f"/api/messages/{latest['id']}", headers=owner).json() == latest
        history = client.get(f"/api/conversations/{cid}/messages", headers=owner).json()
        assert history == [latest]
        merged = latest
        for incoming in (late_response, history[0], created["message"]):
            if incoming["snapshotRevision"] > merged["snapshotRevision"]:
                merged = incoming
        assert merged == latest and merged["reactions"][0]["emoji"] == "👍"


def test_agent_card_suggestion_update_delete_and_typing_snapshots_advance_the_journal(
    client, app, issuer, accounts, installed
):
    owner = accounts["owner"]["headers"]
    headers, cid = installed["agent"]["headers"], installed["conversation"]
    with connect(client, issuer, "owner") as socket:
        authenticate(socket, issuer, "owner")
        mid = connector_message(client, installed, attachments=[{"contentType": ADAPTIVE_CARD, "content": CARD}])
        created = socket.receive_json()["message"]
        assert created["snapshotRevision"] == journal_revision(app, mid)
        response = client.post(f"/api/messages/{mid}/actions", headers=owner, json={"action": EXECUTE})
        assert response.status_code == 200
        refreshed = response.json()["message"]
        assert socket.receive_json()["message"] == refreshed
        assert refreshed["snapshotRevision"] > created["snapshotRevision"]
        response = client.put(f"/v3/conversations/{cid}/activities/{mid}", headers=headers, json={
            "type": "message", "text": "Choose a reply", "suggestedActions": {"actions": SUGGESTED},
        })
        assert response.status_code == 200
        suggested = socket.receive_json()["message"]
        assert suggested["snapshotRevision"] > refreshed["snapshotRevision"]
        response = client.post(f"/api/messages/{mid}/actions", headers=owner, json={"action": SUGGESTED[0]})
        assert response.status_code == 200
        consumed = response.json()["message"]
        submitted = response.json()["submittedMessage"]
        assert socket.receive_json()["message"] == consumed
        assert socket.receive_json()["message"] == submitted
        assert consumed["snapshotRevision"] > suggested["snapshotRevision"]
        assert submitted["snapshotRevision"] == journal_revision(app, submitted["id"])
        assert client.delete(f"/v3/conversations/{cid}/activities/{mid}", headers=headers).status_code == 204
        deleted = socket.receive_json()["message"]
        assert deleted["snapshotRevision"] > consumed["snapshotRevision"]
        assert deleted["snapshotRevision"] == journal_revision(app, mid)
        response = client.post(f"/v3/conversations/{cid}/activities", headers=headers, json={"type": "typing"})
        assert response.status_code == 200
        handle = response.json()["id"]
        assert socket.receive_json()["type"] == "typing"
        hidden = client.get(f"/api/messages/{handle}", headers=owner).json()
        assert hidden["snapshotRevision"] == journal_revision(app, handle)
        assert client.put(f"/v3/conversations/{cid}/activities/{handle}", headers=headers,
                          json={"type": "message", "text": "Typing completed"}).status_code == 200
        completed = socket.receive_json()["message"]
        assert completed["snapshotRevision"] > hidden["snapshotRevision"]


def test_snapshot_revision_and_fields_share_a_read_snapshot_during_concurrent_edit(
    client, app, accounts, resources, monkeypatch
):
    owner = accounts["owner"]["headers"]
    mid = resources["message"]["id"]
    before = client.get(f"/api/messages/{mid}", headers=owner).json()
    serialize = d._message_json
    changed = False

    def concurrent_edit(session, message):
        nonlocal changed
        if message.id == mid and not changed:
            changed = True
            with app.state.sessions() as writer:
                current = d.require_message(writer, mid, accounts["owner"]["user"]["id"], own=True)
                activity = copy.deepcopy(current.activity)
                activity["text"] = "Concurrent committed edit"
                d.update_message(writer, current, activity)
                writer.commit()
        return serialize(session, message)

    monkeypatch.setattr(d, "_message_json", concurrent_edit)
    during = client.get(f"/api/messages/{mid}", headers=owner)
    assert during.status_code == 200, during.text
    assert during.json() == before
    after = client.get(f"/api/messages/{mid}", headers=owner).json()
    assert after["text"] == "Concurrent committed edit"
    assert after["snapshotRevision"] > before["snapshotRevision"]


def test_legacy_message_without_journal_gets_zero_then_advances_on_mutation(client, app, accounts, resources):
    mid = resources["message"]["id"]
    owner = accounts["owner"]["headers"]
    with app.state.sessions() as session:
        session.execute(delete(db.ActivityRevision).where(db.ActivityRevision.message_id == mid))
        session.commit()
    before = client.get(f"/api/messages/{mid}", headers=owner).json()
    assert before["snapshotRevision"] == 0
    response = client.post(f"/api/messages/{mid}/reactions", headers=owner, json={"emoji": "👍"})
    assert response.status_code == 200
    assert response.json()["snapshotRevision"] > 0
    assert response.json()["updatedAt"] is None
