import math
import time
from datetime import datetime, timezone
from urllib.parse import quote

import pytest
from starlette.websockets import WebSocketDisconnect


ORIGIN = "http://localhost:5173"


def connect(client, issuer, persona):
    socket = client.websocket_connect("/api/ws", headers={"origin": ORIGIN})
    return socket


def authenticate(socket, issuer, persona):
    socket.send_json({"token": issuer.mint(persona)})
    assert socket.receive_json() == {"type": "ready"}


@pytest.mark.parametrize("group", [False, True], ids=["personal", "group"])
def test_chat_created_notifies_participants_but_not_outsider(client, issuer, accounts, group):
    with (
        connect(client, issuer, "owner") as owner_socket,
        connect(client, issuer, "member") as member_socket,
        connect(client, issuer, "admin") as admin_socket,
        connect(client, issuer, "outsider") as outsider_socket,
    ):
        for socket, persona in (
            (owner_socket, "owner"), (member_socket, "member"),
            (admin_socket, "admin"), (outsider_socket, "outsider"),
        ):
            authenticate(socket, issuer, persona)
        body = {"memberIds": [accounts["member"]["user"]["id"]]}
        if group:
            body.update({"name": "New group", "memberIds": [
                accounts["member"]["user"]["id"], accounts["admin"]["user"]["id"],
            ]})
        response = client.post("/api/chats", headers=accounts["owner"]["headers"], json=body)
        assert response.status_code == 201
        chat = response.json()
        event = {"type": "chat.created", "chatId": chat["id"], "conversationId": chat["conversationId"]}
        assert owner_socket.receive_json() == event
        assert member_socket.receive_json() == event
        if group:
            assert admin_socket.receive_json() == event
        assert chat["id"] in {
            c["id"] for c in client.get("/api/chats", headers=accounts["member"]["headers"]).json()
        }
        assert client.get("/api/chats", headers=accounts["outsider"]["headers"]).json() == []
        # A later authorized marker must be first: no private chat event was queued.
        marker = client.post("/api/teams", headers=accounts["outsider"]["headers"],
                             json={"name": "Outsider marker"}).json()
        assert outsider_socket.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}
        if not group:
            admin_marker = client.post("/api/teams", headers=accounts["admin"]["headers"],
                                       json={"name": "Uninvited admin marker"}).json()
            assert admin_socket.receive_json() == {"type": "membership.changed", "teamId": admin_marker["id"]}


@pytest.mark.parametrize("kind", ["channel", "chat"])
def test_file_created_notifies_member_but_not_outsider(client, issuer, accounts, resources, kind):
    conversation_id = resources["conversation"] if kind == "channel" else resources["chat"]["conversationId"]
    with connect(client, issuer, "member") as member_socket, connect(client, issuer, "outsider") as outsider_socket:
        authenticate(member_socket, issuer, "member")
        authenticate(outsider_socket, issuer, "outsider")
        response = client.post(f"/api/conversations/{conversation_id}/files",
                               headers=accounts["owner"]["headers"],
                               files={"file": ("shared.txt", b"already committed", "text/plain")})
        assert response.status_code == 201
        file = response.json()
        assert member_socket.receive_json() == {
            "type": "file.created", "conversationId": conversation_id, "fileId": file["id"],
        }
        assert file in client.get(f"/api/conversations/{conversation_id}/files",
                                  headers=accounts["member"]["headers"]).json()
        assert client.get(file["downloadUrl"], headers=accounts["member"]["headers"]).content == b"already committed"
        assert client.get(f"/api/files/{file['id']}", headers=accounts["outsider"]["headers"]).status_code == 403
        marker = client.post("/api/teams", headers=accounts["outsider"]["headers"],
                             json={"name": "Outsider file-event marker"}).json()
        assert outsider_socket.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}


def test_websocket_delivery_order_p95_and_no_outsider_leak(client, issuer, accounts, resources):
    with connect(client, issuer, "member") as member_socket, connect(client, issuer, "outsider") as outsider_socket:
        authenticate(member_socket, issuer, "member")
        authenticate(outsider_socket, issuer, "outsider")
        delays = []
        expected = []
        received = []
        for index in range(20):
            start = time.perf_counter()
            response = client.post(f"/api/conversations/{resources['conversation']}/messages",
                                   headers=accounts["owner"]["headers"], json={"text": f"ordered-{index:02d}"})
            assert response.status_code == 201
            expected.append(response.json()["id"])
            event = member_socket.receive_json()
            delays.append(time.perf_counter() - start)
            assert event["type"] == "message.created"
            assert event["conversationId"] == resources["conversation"]
            received.append(event["message"]["id"])
        assert received == expected
        p95 = sorted(delays)[math.ceil(len(delays) * .95) - 1]
        assert p95 <= 2
        # A guaranteed later, authorized marker must be the outsider's first event.
        own_team = client.post("/api/teams", headers=accounts["outsider"]["headers"],
                               json={"name": "Outsider's own team"}).json()
        assert outsider_socket.receive_json() == {"type": "membership.changed", "teamId": own_team["id"]}
        persisted = client.get(f"/api/conversations/{resources['conversation']}/messages",
                               headers=accounts["member"]["headers"]).json()
        assert [m["id"] for m in persisted][-20:] == expected
        print(f"WebSocket API delivery: n=20, lost=0, ordered=True, p95={p95:.4f}s")


def test_membership_notifications_and_immediate_revocation(client, issuer, accounts):
    owner = accounts["owner"]["headers"]
    member = accounts["member"]["headers"]
    member_id = accounts["member"]["user"]["id"]
    with connect(client, issuer, "member") as member_socket, connect(client, issuer, "outsider") as outsider_socket:
        authenticate(member_socket, issuer, "member")
        authenticate(outsider_socket, issuer, "outsider")
        team = client.post("/api/teams", headers=owner, json={"name": "Private team"}).json()
        start = time.perf_counter()
        assert client.post(f"/api/teams/{team['id']}/members", headers=owner,
                           json={"userId": member_id}).status_code == 200
        assert member_socket.receive_json() == {"type": "membership.changed", "teamId": team["id"]}
        assert time.perf_counter() - start < 5
        assert client.get(f"/api/teams/{team['id']}", headers=member).status_code == 200
        cid = team["channels"][0]["conversationId"]
        response = client.post(f"/api/conversations/{cid}/messages", headers=owner, json={"text": "Visible once"})
        assert member_socket.receive_json()["message"]["id"] == response.json()["id"]
        assert client.delete(f"/api/teams/{team['id']}/members/{member_id}", headers=owner).status_code == 204
        assert member_socket.receive_json() == {"type": "membership.removed", "teamId": team["id"]}
        assert client.get(f"/api/conversations/{cid}/messages", headers=member).status_code == 403
        assert client.get("/api/activities/export", headers=member).json() == []
        client.post(f"/api/conversations/{cid}/messages", headers=owner, json={"text": "Must remain private"})
        own_team = client.post("/api/teams", headers=member, json={"name": "Removed member's own team"}).json()
        assert member_socket.receive_json() == {"type": "membership.changed", "teamId": own_team["id"]}
        outside = client.post("/api/teams", headers=accounts["outsider"]["headers"],
                              json={"name": "Outside marker"}).json()
        assert outsider_socket.receive_json() == {"type": "membership.changed", "teamId": outside["id"]}


def test_edit_delete_reaction_events_are_committed(client, issuer, accounts, resources):
    owner = accounts["owner"]["headers"]
    mid = resources["message"]["id"]
    with connect(client, issuer, "member") as socket:
        authenticate(socket, issuer, "member")
        for method, url, kwargs, kind in [
            ("patch", f"/api/messages/{mid}", {"json": {"text": "Updated"}}, "message.updated"),
            ("post", f"/api/messages/{mid}/reactions", {"json": {"emoji": "🦄"}}, "reaction.changed"),
            ("delete", f"/api/messages/{mid}", {}, "message.deleted"),
        ]:
            response = client.request(method, url, headers=owner, **kwargs)
            assert response.status_code == 200
            event = socket.receive_json()
            assert event["type"] == kind
            assert event["message"] == response.json()
            assert client.get(f"/api/messages/{mid}", headers=accounts["member"]["headers"]).json() == event["message"]


def test_reaction_add_remove_events_identify_actor_time_and_exclude_outsider(client, issuer, accounts, resources):
    member = accounts["member"]
    expected_actor = {field: member["user"][field] for field in ("id", "displayName", "upn", "roles")}
    mid = resources["message"]["id"]
    assert expected_actor["id"] != resources["message"]["author"]["id"]
    with (
        connect(client, issuer, "owner") as owner_socket,
        connect(client, issuer, "member") as member_socket,
        connect(client, issuer, "outsider") as outsider_socket,
    ):
        for socket, persona in (
            (owner_socket, "owner"), (member_socket, "member"), (outsider_socket, "outsider"),
        ):
            authenticate(socket, issuer, persona)
        for method, url, kwargs in [
            ("post", f"/api/messages/{mid}/reactions", {"json": {"emoji": "👍"}}),
            ("delete", f"/api/messages/{mid}/reactions/{quote('👍', safe='')}", {}),
        ]:
            started = datetime.now(timezone.utc)
            response = client.request(method, url, headers=member["headers"], **kwargs)
            assert response.status_code == 200
            event = owner_socket.receive_json()
            finished = datetime.now(timezone.utc)
            assert event["type"] == "reaction.changed"
            assert event["conversationId"] == resources["conversation"]
            assert event["actor"] == expected_actor
            assert event["occurredAt"].endswith("Z")
            occurred = datetime.fromisoformat(event["occurredAt"])
            assert occurred.tzinfo == timezone.utc
            assert started <= occurred <= finished
            assert event["message"] == response.json()
            assert member_socket.receive_json() == event
            committed = client.get(f"/api/messages/{mid}", headers=member["headers"]).json()
            assert committed == event["message"]
            assert committed["reactions"] == ([] if method == "delete" else [{
                "emoji": "👍", "count": 1, "userIds": [expected_actor["id"]], "users": [expected_actor],
            }])
        marker = client.post("/api/teams", headers=accounts["outsider"]["headers"],
                             json={"name": "Outsider reaction-event marker"}).json()
        assert outsider_socket.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}


@pytest.mark.parametrize("frame", [{}, {"token": "malformed"}, {"token": None}, [], {"other": "value"}])
def test_websocket_invalid_auth_closes_1008(client, frame):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/ws", headers={"origin": ORIGIN}) as socket:
            socket.send_json(frame)
            socket.receive_json()
    assert error.value.code == 1008


def test_websocket_binary_auth_disallowed_origin_query_and_expiry(client, issuer):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/ws", headers={"origin": ORIGIN}) as socket:
            socket.send_bytes(b"not JSON text")
            socket.receive_json()
    assert error.value.code == 1008
    for path, origin in (("/api/ws", "https://attacker.invalid"), ("/api/ws?token=redacted", ORIGIN)):
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path, headers={"origin": origin}):
                pass
        assert error.value.code == 1008
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/ws", headers={"origin": ORIGIN}) as socket:
            socket.send_json({"token": issuer.mint(overrides={"exp": int(time.time()) + 2})})
            assert socket.receive_json() == {"type": "ready"}
            socket.receive_json()
    assert error.value.code == 1008
