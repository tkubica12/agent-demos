"""Synthetic G3 group membership and native suggested-action protocol tests."""

import copy
from dataclasses import replace

import pytest
from sqlalchemy import select

from backend import db
from backend.channel import ADAPTIVE_CARD
from tests.test_agent_channel import AGENT_A, AGENT_B, CARD, EXECUTE, SUBMIT, agents, app, client, installed
from tests.test_realtime import authenticate, connect


SUGGESTED = [
    {"type": "imBack", "title": "Visible reply", "value": "Visible reply"},
    {"type": "postBack", "title": "Hidden reply", "value": "hidden-command"},
    {"type": "messageBack", "title": "Programmatic reply", "text": "internal-command",
     "displayText": "Friendly reply", "value": {"choice": "yes"}},
]
DOCUMENT_SUGGESTED = [
    {"type": "Action.Submit", "title": "Approve",
     "value": {"documentActionToken": "synthetic-document-action", "documentActionChoice": "approve"}},
    {"type": "Action.Submit", "title": "Reject",
     "value": {"documentActionToken": "synthetic-document-action", "documentActionChoice": "reject"}},
]


@pytest.fixture
def group(client, accounts):
    response = client.post("/api/chats", headers=accounts["owner"]["headers"], json={
        "name": "Existing group", "memberIds": [accounts["member"]["user"]["id"], accounts["admin"]["user"]["id"]],
    })
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def agent_group(client, accounts, agents, group):
    for agent in agents.values():
        assert client.post(f"/api/chats/{group['id']}/members", headers=accounts["member"]["headers"],
                           json={"userId": agent["userId"]}).status_code == 200
    return group


def suggested_message(client, group, agents, actions=SUGGESTED, to=None):
    response = client.post(f"/v3/conversations/{group['conversationId']}/activities", headers=agents["A"]["headers"],
                           json={"type": "message", "text": "Choose a reply",
                                 "suggestedActions": {"actions": actions, "to": to or []}})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_current_group_member_can_install_remove_agents_and_manage_humans(client, app, accounts, agents, group):
    route = f"/api/chats/{group['id']}/members"
    member = accounts["member"]["headers"]
    assert len(client.get(route, headers=member).json()) == 3
    response = client.post(route, headers=member, json={"userId": agents["A"]["userId"]})
    assert response.status_code == 200 and response.json()["isAgent"] is True
    installation = app.state.protocol_fixture["activities"][-1]
    assert installation["type"] == "conversationUpdate"
    assert installation["conversation"]["id"] == group["conversationId"]
    assert installation["conversation"]["conversationType"] == "groupChat"
    assert installation["membersAdded"] == [{"id": f"28:{AGENT_A}", "name": "Agent A"}]
    assert installation["recipient"]["isTargeted"] is False
    before = len(app.state.protocol_fixture["activities"])
    assert client.post(route, headers=member, json={"userId": agents["A"]["userId"]}).status_code == 200
    assert len(app.state.protocol_fixture["activities"]) == before
    assert client.post(route, headers=member, json={"userId": agents["B"]["userId"]}).status_code == 200
    before = len(app.state.protocol_fixture["activities"])
    assert client.delete(f"{route}/{agents['A']['userId']}", headers=accounts["admin"]["headers"]).status_code == 204
    removals = app.state.protocol_fixture["activities"][before:]
    assert len(removals) == 2
    assert {a["recipient"]["id"] for a in removals} == {f"28:{AGENT_A}", f"28:{AGENT_B}"}
    assert all(a["membersRemoved"] == [{"id": f"28:{AGENT_A}", "name": "Agent A"}] for a in removals)
    assert client.get(f"/v3/conversations/{group['conversationId']}/members",
                      headers=agents["A"]["headers"]).status_code == 403
    assert client.get(f"/v3/conversations/{group['conversationId']}/members",
                      headers=agents["B"]["headers"]).status_code == 200
    # No creator/owner privilege is retained in a group chat, unlike a team.
    owner_id = accounts["owner"]["user"]["id"]
    assert client.delete(f"{route}/{owner_id}", headers=member).status_code == 204
    assert client.get(route, headers=accounts["owner"]["headers"]).status_code == 403
    assert client.post(route, headers=accounts["owner"]["headers"],
                       json={"userId": owner_id}).status_code == 403
    assert client.delete(f"{route}/{accounts['member']['user']['id']}", headers=member).status_code == 204
    assert client.get(f"/api/chats/{group['id']}", headers=accounts["admin"]["headers"]).json()["conversationType"] == "groupChat"


def test_group_membership_outsiders_missing_users_and_personal_chat_immutability(client, accounts, resources, group):
    route = f"/api/chats/{group['id']}/members"
    outsider = accounts["outsider"]["headers"]
    uid = accounts["member"]["user"]["id"]
    for method, path, body in [
        ("get", route, None), ("post", route, {"userId": uid}), ("delete", f"{route}/{uid}", None),
    ]:
        assert client.request(method, path, headers=outsider, json=body).status_code == 403
        assert client.request(method, path, json=body).status_code == 401
    assert client.post(route, headers=accounts["member"]["headers"], json={"userId": "missing"}).status_code == 404
    assert client.delete(f"{route}/missing", headers=accounts["member"]["headers"]).status_code == 404
    personal_route = f"/api/chats/{resources['chat']['id']}/members"
    before = client.get(personal_route, headers=accounts["owner"]["headers"]).json()
    assert len(before) == 2
    assert client.post(personal_route, headers=accounts["owner"]["headers"],
                       json={"userId": accounts["admin"]["user"]["id"]}).status_code == 409
    assert client.delete(f"{personal_route}/{uid}", headers=accounts["owner"]["headers"]).status_code == 409
    assert client.get(personal_route, headers=accounts["owner"]["headers"]).json() == before


def test_group_membership_ws_refresh_and_immediate_revocation(client, issuer, accounts, group):
    uid = accounts["outsider"]["user"]["id"]
    owner = accounts["owner"]["headers"]
    route = f"/api/chats/{group['id']}/members"
    with connect(client, issuer, "outsider") as joined, connect(client, issuer, "member") as member:
        authenticate(joined, issuer, "outsider")
        authenticate(member, issuer, "member")
        assert client.post(route, headers=owner, json={"userId": uid}).status_code == 200
        event = {"type": "membership.changed", "chatId": group["id"], "conversationId": group["conversationId"]}
        assert joined.receive_json() == event
        assert member.receive_json() == event
        assert group["id"] in {c["id"] for c in client.get("/api/chats", headers=accounts["outsider"]["headers"]).json()}
        assert client.delete(f"{route}/{uid}", headers=owner).status_code == 204
        assert joined.receive_json() == {"type": "membership.removed", "chatId": group["id"]}
        assert member.receive_json() == event
        assert client.get(f"/api/conversations/{group['conversationId']}/messages",
                          headers=accounts["outsider"]["headers"]).status_code == 403
        assert client.post(f"/api/conversations/{group['conversationId']}/messages", headers=owner,
                           json={"text": "Private after removal"}).status_code == 201
        assert member.receive_json()["type"] == "message.created"
        marker = client.post("/api/teams", headers=accounts["outsider"]["headers"], json={"name": "Marker"}).json()
        assert joined.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}


def test_group_membership_delivery_failures_report_real_committed_state(client, app, accounts, agents, group):
    route = f"/api/chats/{group['id']}/members"
    app.state.protocol_fixture["deliveryStatus"] = 503
    response = client.post(route, headers=accounts["member"]["headers"], json={"userId": agents["A"]["userId"]})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["resourceCommitted"] is True and detail["resourceId"] == group["id"]
    assert agents["A"]["userId"] in {u["id"] for u in client.get(route, headers=accounts["member"]["headers"]).json()}
    response = client.delete(f"{route}/{agents['A']['userId']}", headers=accounts["member"]["headers"])
    assert response.status_code == 502 and response.json()["detail"]["resourceCommitted"] is True
    assert agents["A"]["userId"] not in {u["id"] for u in client.get(route, headers=accounts["member"]["headers"]).json()}
    assert client.get(f"/v3/conversations/{group['conversationId']}/members",
                      headers=agents["A"]["headers"]).status_code == 403
    app.state.channel.auth.settings = replace(app.state.settings, channel_client_secret="")
    response = client.post(route, headers=accounts["member"]["headers"], json={"userId": agents["B"]["userId"]})
    assert response.status_code == 503 and response.json()["detail"]["resourceCommitted"] is True
    assert agents["B"]["userId"] in {u["id"] for u in client.get(route, headers=accounts["member"]["headers"]).json()}


def test_registry_create_update_broadcast_only_safe_directory_refresh(client, issuer, accounts):
    with connect(client, issuer, "owner") as owner, connect(client, issuer, "member") as member:
        authenticate(owner, issuer, "owner")
        authenticate(member, issuer, "member")
        response = client.post("/api/agents", headers=accounts["owner"]["headers"], json={
            "name": "Directory agent", "clientId": AGENT_A, "endpointUrl": "http://127.0.0.1:9101/api/messages",
        })
        assert response.status_code == 201
        agent = response.json()
        assert owner.receive_json() == member.receive_json() == {"type": "directory.changed"}
        assert client.patch(f"/api/agents/{agent['id']}", headers=accounts["owner"]["headers"],
                            json={"name": "Renamed"}).status_code == 200
        assert owner.receive_json() == member.receive_json() == {"type": "directory.changed"}
        directory = client.get("/api/users", headers=accounts["member"]["headers"]).json()
        assert next(u["displayName"] for u in directory if u["id"] == agent["userId"]) == "Renamed"
        assert client.patch(f"/api/agents/{agent['id']}", headers=accounts["member"]["headers"],
                            json={"name": "Forbidden"}).status_code == 403
        marker = client.post("/api/teams", headers=accounts["member"]["headers"], json={"name": "Marker"}).json()
        assert member.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}


def test_all_three_native_suggested_replies_survive_send_update_and_reload(client, app, accounts, agents, agent_group):
    actions = [{"type": "imBack", "title": label, "value": label} for label in ("Nápověda", "Příklad", "Další")]
    mid = suggested_message(client, agent_group, agents, actions)
    expected = {"to": [], "actions": actions}
    assert client.get(f"/api/messages/{mid}", headers=accounts["member"]["headers"]).json()["suggestedActions"] == expected
    response = client.put(f"/v3/conversations/{agent_group['conversationId']}/activities/{mid}",
                          headers=agents["A"]["headers"],
                          json={"type": "message", "text": "Updated prompt", "suggestedActions": expected})
    assert response.status_code == 200
    messages = client.get(f"/api/conversations/{agent_group['conversationId']}/messages",
                          headers=accounts["member"]["headers"]).json()
    assert next(m["suggestedActions"] for m in messages if m["id"] == mid) == expected
    with app.state.sessions() as session:
        stored = session.scalar(select(db.Message).where(db.Message.id == mid))
        assert stored.activity["suggestedActions"] == expected


@pytest.mark.parametrize("index", [0, 1, 2], ids=["imBack", "postBack", "messageBack"])
def test_native_stored_actions_target_only_author_and_preserve_visible_vs_wire_text(
    client, app, accounts, agents, agent_group, index
):
    mid = suggested_message(client, agent_group, agents)
    before = len(app.state.protocol_fixture["activities"])
    response = client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                           json={"action": SUGGESTED[index]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["invokeResponse"] is None
    assert result["message"]["id"] == mid and result["message"]["suggestedActions"] is None
    assert len(app.state.protocol_fixture["activities"]) == before + 1
    activity = app.state.protocol_fixture["activities"][-1]
    assert activity["type"] == "message"
    assert activity["id"] == result["activityId"] and activity["replyToId"] == mid
    assert activity["recipient"] == {"id": f"28:{AGENT_A}", "name": "Agent A", "isTargeted": False}
    assert activity["from"]["id"] == accounts["member"]["user"]["id"]
    assert activity["text"] == (SUGGESTED[index]["text"] if index == 2 else SUGGESTED[index]["value"])
    if index == 2:
        assert activity["value"] == {"choice": "yes"}
        assert result["submittedMessage"]["text"] == "Friendly reply"
    elif index == 1:
        assert result["submittedMessage"] is None
        hidden = client.get(f"/api/messages/{result['activityId']}", headers=accounts["member"]["headers"]).json()
        assert hidden["text"] == "" and hidden["isHidden"] is True
    else:
        assert result["submittedMessage"]["text"] == "Visible reply"
    feed = client.get(f"/api/conversations/{agent_group['conversationId']}/messages",
                      headers=accounts["member"]["headers"]).json()
    assert (result["activityId"] in {m["id"] for m in feed}) is (index != 1)
    assert client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                       json={"action": SUGGESTED[index]}).status_code == 403


def test_suggested_reply_ws_consumption_hidden_postback_and_no_outsider_leak(
    client, issuer, accounts, agents, agent_group
):
    mid = suggested_message(client, agent_group, agents)
    with connect(client, issuer, "member") as member, connect(client, issuer, "outsider") as outsider:
        authenticate(member, issuer, "member")
        authenticate(outsider, issuer, "outsider")
        response = client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                               json={"action": SUGGESTED[1]})
        assert response.status_code == 200
        event = member.receive_json()
        assert event["type"] == "message.updated" and event["message"]["suggestedActions"] is None
        # The marker is first: postBack did not add a blank/private-value message to the feed.
        marker = client.post("/api/teams", headers=accounts["member"]["headers"], json={"name": "Member marker"}).json()
        assert member.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}
        marker = client.post("/api/teams", headers=accounts["outsider"]["headers"], json={"name": "Outsider marker"}).json()
        assert outsider.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}


def test_suggested_action_recipient_and_exact_stored_action_authorization(client, app, accounts, agents, agent_group):
    target = accounts["member"]["user"]["id"]
    mid = suggested_message(client, agent_group, agents, to=[target])
    route = f"/api/messages/{mid}/actions"
    before = len(app.state.protocol_fixture["activities"])
    for headers, body, expected in [
        (accounts["outsider"]["headers"], {"action": SUGGESTED[0]}, 403),
        (accounts["owner"]["headers"], {"action": SUGGESTED[0]}, 403),
        (accounts["member"]["headers"], {"action": {**SUGGESTED[0], "value": "forged"}}, 403),
        (accounts["member"]["headers"], {"action": SUGGESTED[0], "inputs": {"spoof": "value"}}, 422),
    ]:
        assert client.post(route, headers=headers, json=body).status_code == expected
    assert len(app.state.protocol_fixture["activities"]) == before
    assert client.get(f"/api/messages/{mid}", headers=accounts["member"]["headers"]).json()["suggestedActions"]["actions"] == SUGGESTED
    assert client.post(route, headers=accounts["member"]["headers"], json={"action": SUGGESTED[0]}).status_code == 200


@pytest.mark.parametrize("action", [
    {"type": "imBack", "title": "Invalid", "value": {}},
    {"type": "postBack", "title": "Invalid", "value": 1},
    {"type": "imBack", "title": "Invalid", "value": "x" * 28001},
    {"type": "signin", "title": "Unsupported", "value": "https://example.test"},
    {"type": "openUrl", "title": "Unsafe", "value": "javascript:alert(1)"},
    {"type": "openUrl", "title": "Unsafe", "value": "https://user:password@example.test"},
    {"type": "openUrl", "title": "Unsafe", "value": "https://[invalid"},
])
def test_invalid_suggested_actions_fail_explicitly_before_persistence(client, accounts, agents, agent_group, action):
    route = f"/v3/conversations/{agent_group['conversationId']}/activities"
    before = client.get(f"/api/conversations/{agent_group['conversationId']}/messages",
                        headers=accounts["member"]["headers"]).json()
    response = client.post(route, headers=agents["A"]["headers"],
                           json={"type": "message", "text": "Invalid", "suggestedActions": {"actions": [action]}})
    assert response.status_code == 422
    assert client.get(f"/api/conversations/{agent_group['conversationId']}/messages",
                      headers=accounts["member"]["headers"]).json() == before


def test_safe_openurl_is_preserved_but_never_dispatched_by_the_server(client, app, accounts, agents, agent_group):
    action = {"type": "openUrl", "title": "Docs", "value": "https://example.test/docs?q=one#section"}
    mid = suggested_message(client, agent_group, agents, [action])
    value = client.get(f"/api/messages/{mid}", headers=accounts["member"]["headers"]).json()
    assert value["suggestedActions"]["actions"] == [action]
    before = len(app.state.protocol_fixture["activities"])
    assert client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                       json={"action": action}).status_code == 422
    assert len(app.state.protocol_fixture["activities"]) == before


def test_suggested_reply_delivery_failure_preserves_committed_visible_state(client, app, accounts, agents, agent_group):
    mid = suggested_message(client, agent_group, agents)
    app.state.protocol_fixture["deliveryStatus"] = 503
    response = client.post(f"/api/messages/{mid}/actions", headers=accounts["member"]["headers"],
                           json={"action": SUGGESTED[0]})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["resourceCommitted"] is True
    assert detail["failures"][0]["delivery"]["status"] == "failed"
    assert client.get(f"/api/messages/{mid}", headers=accounts["member"]["headers"]).json()["suggestedActions"] is None
    assert client.get(f"/api/messages/{detail['resourceId']}",
                      headers=accounts["member"]["headers"]).json()["text"] == "Visible reply"


def test_channel_suggested_reply_and_group_adaptive_action_are_not_targeted_private(
    client, app, accounts, agents, installed, agent_group
):
    thread = f"{installed['conversation']};messageid={installed['message']['id']}"
    response = client.post(f"/v3/conversations/{thread}/activities", headers=agents["A"]["headers"], json={
        "type": "message", "text": "Thread replies", "suggestedActions": {"actions": SUGGESTED},
    })
    assert response.status_code == 200
    assert client.post(f"/api/messages/{response.json()['id']}/actions", headers=accounts["member"]["headers"],
                       json={"action": SUGGESTED[0]}).status_code == 200
    activity = app.state.protocol_fixture["activities"][-1]
    assert activity["conversation"]["id"] == thread and activity["recipient"]["isTargeted"] is False
    response = client.post(f"/v3/conversations/{agent_group['conversationId']}/activities",
                           headers=agents["A"]["headers"],
                           json={"type": "message", "attachments": [{"contentType": ADAPTIVE_CARD, "content": CARD}]})
    assert response.status_code == 200
    assert client.post(f"/api/messages/{response.json()['id']}/actions", headers=accounts["member"]["headers"],
                       json={"action": EXECUTE}).status_code == 200
    activity = app.state.protocol_fixture["activities"][-1]
    assert activity["type"] == "invoke" and activity["recipient"]["isTargeted"] is False


@pytest.mark.parametrize("choice", [0, 1])
def test_native_document_submit_wire_value_exact_match_consumption_and_hidden_state(
    client, app, issuer, accounts, agents, agent_group, choice
):
    mid = suggested_message(client, agent_group, agents, DOCUMENT_SUGGESTED)
    headers = accounts["member"]["headers"]
    before = client.get(f"/api/messages/{mid}", headers=headers).json()
    assert before["suggestedActions"] == {"to": [], "actions": DOCUMENT_SUGGESTED}
    assert before["activityAttachments"] == []
    action = DOCUMENT_SUGGESTED[choice]
    count = len(app.state.protocol_fixture["activities"])
    with connect(client, issuer, "member") as socket:
        authenticate(socket, issuer, "member")
        response = client.post(f"/api/messages/{mid}/actions", headers=headers, json={"action": action})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["invokeResponse"] is None and result["submittedMessage"] is None
        assert result["message"]["suggestedActions"] is None
        assert result["message"]["snapshotRevision"] > before["snapshotRevision"]
        event = socket.receive_json()
        assert event["type"] == "message.updated" and event["message"] == result["message"]
        marker = client.post("/api/teams", headers=headers, json={"name": "No hidden document-action bubble"}).json()
        assert socket.receive_json() == {"type": "membership.changed", "teamId": marker["id"]}
    activities = app.state.protocol_fixture["activities"][count:]
    assert len(activities) == 1
    activity = activities[0]
    assert activity["type"] == "message" and "name" not in activity
    assert activity["text"] == "" and activity["value"] == action["value"]
    assert activity["recipient"]["id"] == f"28:{AGENT_A}" and activity["recipient"]["isTargeted"] is False
    assert activity["from"]["id"] == accounts["member"]["user"]["id"]
    assert activity["replyToId"] == mid and activity["conversation"]["id"] == agent_group["conversationId"]
    assert activity["channelData"]["clientAction"]["type"] == "Action.Submit"
    hidden = client.get(f"/api/messages/{result['activityId']}", headers=headers).json()
    assert hidden["isHidden"] is True and hidden["text"] == ""
    history = client.get(f"/api/conversations/{agent_group['conversationId']}/messages", headers=headers).json()
    assert result["activityId"] not in {m["id"] for m in history}
    count = len(app.state.protocol_fixture["activities"])
    assert client.post(f"/api/messages/{mid}/actions", headers=headers, json={"action": action}).status_code == 403
    assert len(app.state.protocol_fixture["activities"]) == count
    assert client.get(f"/api/messages/{mid}", headers=headers).json() == result["message"]


def test_native_document_submit_modified_stale_inputs_and_recipient_authorization(
    client, app, accounts, agents, agent_group
):
    mid = suggested_message(client, agent_group, agents, DOCUMENT_SUGGESTED, to=[accounts["member"]["user"]["id"]])
    route = f"/api/messages/{mid}/actions"
    member = accounts["member"]["headers"]
    original = client.get(f"/api/messages/{mid}", headers=member).json()
    action = DOCUMENT_SUGGESTED[0]
    modified = [
        {**action, "title": "Different title"},
        {**action, "value": {**action["value"], "documentActionToken": "modified-document-action"}},
        {**action, "value": {**action["value"], "documentActionChoice": "modified-choice"}},
        {**action, "data": {"documentActionChoice": "approve"}},
        {"type": "Action.Submit", "title": action["title"]},
    ]
    count = len(app.state.protocol_fixture["activities"])
    for forged in modified:
        assert client.post(route, headers=member, json={"action": forged}).status_code == 403
    assert client.post(route, json={"action": action}).status_code == 401
    assert client.post(route, headers=accounts["outsider"]["headers"], json={"action": action}).status_code == 403
    assert client.post(route, headers=accounts["owner"]["headers"], json={"action": action}).status_code == 403
    assert client.post(route, headers=member, json={"action": action, "inputs": {"forged": "value"}}).status_code == 422
    assert len(app.state.protocol_fixture["activities"]) == count
    assert client.get(f"/api/messages/{mid}", headers=member).json() == original
    replacement = copy.deepcopy(DOCUMENT_SUGGESTED)
    replacement[0]["value"]["documentActionToken"] = "replacement-document-action"
    assert client.put(f"/v3/conversations/{agent_group['conversationId']}/activities/{mid}", headers=agents["A"]["headers"],
                      json={"type": "message", "text": "Replaced decision", "suggestedActions": {
                          "to": [accounts["member"]["user"]["id"]], "actions": replacement,
                      }}).status_code == 200
    revised = client.get(f"/api/messages/{mid}", headers=member).json()
    assert revised["snapshotRevision"] > original["snapshotRevision"]
    assert client.post(route, headers=member, json={"action": action}).status_code == 403
    assert client.get(f"/api/messages/{mid}", headers=member).json() == revised
    assert client.post(route, headers=member, json={"action": replacement[0]}).status_code == 200


@pytest.mark.parametrize("action", [
    {"type": "Action.Submit", "title": "Invalid", "value": {}},
    {"type": "Action.Submit", "title": "Invalid", "value": {"documentActionToken": "token"}},
    {"type": "Action.Submit", "title": "Invalid", "value": {"documentActionToken": "token", "documentActionChoice": 1}},
    {"type": "Action.Submit", "title": "Invalid", "value": {"documentActionToken": "", "documentActionChoice": "approve"}},
    {"type": "Action.Submit", "title": "Invalid", "value": {"documentActionToken": "token", "documentActionChoice": "approve",
                                                         "extra": "unsupported"}},
    {"type": "Action.Submit", "title": "Invalid", "data": {"documentActionToken": "token", "documentActionChoice": "approve"}},
    {**DOCUMENT_SUGGESTED[0], "displayText": "Unsupported extension"},
    {**DOCUMENT_SUGGESTED[0], "title": ""},
    {**DOCUMENT_SUGGESTED[0], "type": "Action.Execute"},
])
def test_native_document_submit_alias_is_narrowly_validated_before_persistence(
    client, accounts, agents, agent_group, action
):
    before = client.get(f"/api/conversations/{agent_group['conversationId']}/messages",
                        headers=accounts["member"]["headers"]).json()
    response = client.post(f"/v3/conversations/{agent_group['conversationId']}/activities", headers=agents["A"]["headers"],
                           json={"type": "message", "text": "Invalid nonstandard suggestion",
                                 "suggestedActions": {"to": [], "actions": [action]}})
    assert response.status_code == 422
    assert client.get(f"/api/conversations/{agent_group['conversationId']}/messages",
                      headers=accounts["member"]["headers"]).json() == before


def test_native_document_submit_is_not_adaptive_submit_data_even_with_both_on_same_message(
    client, app, accounts, agents, agent_group
):
    response = client.post(f"/v3/conversations/{agent_group['conversationId']}/activities", headers=agents["A"]["headers"],
                           json={"type": "message", "text": "Separate card and document actions",
                                 "attachments": [{"contentType": ADAPTIVE_CARD, "content": CARD}],
                                 "suggestedActions": {"to": [], "actions": DOCUMENT_SUGGESTED}})
    assert response.status_code == 200, response.text
    mid = response.json()["id"]
    route = f"/api/messages/{mid}/actions"
    headers = accounts["member"]["headers"]
    adaptive = client.post(route, headers=headers, json={"action": SUBMIT})
    assert adaptive.status_code == 200, adaptive.text
    assert app.state.protocol_fixture["activities"][-1]["value"] == SUBMIT["data"]
    assert adaptive.json()["message"]["suggestedActions"]["actions"] == DOCUMENT_SUGGESTED
    native = client.post(route, headers=headers, json={"action": DOCUMENT_SUGGESTED[0]})
    assert native.status_code == 200, native.text
    assert app.state.protocol_fixture["activities"][-1]["value"] == DOCUMENT_SUGGESTED[0]["value"]
    assert native.json()["message"]["suggestedActions"] is None
    assert native.json()["message"]["activityAttachments"] == [{"contentType": ADAPTIVE_CARD, "content": CARD}]


def test_native_document_submit_requires_agent_membership_and_consumes_before_failed_delivery(
    client, app, accounts, agents, agent_group
):
    headers = accounts["member"]["headers"]
    mid = suggested_message(client, agent_group, agents, DOCUMENT_SUGGESTED)
    app.state.protocol_fixture["deliveryStatus"] = 500
    response = client.post(f"/api/messages/{mid}/actions", headers=headers, json={"action": DOCUMENT_SUGGESTED[0]})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["resourceCommitted"] is True
    assert client.get(f"/api/messages/{mid}", headers=headers).json()["suggestedActions"] is None
    assert client.get(f"/api/messages/{detail['resourceId']}", headers=headers).json()["isHidden"] is True
    app.state.protocol_fixture["deliveryStatus"] = 202
    another = suggested_message(client, agent_group, agents, DOCUMENT_SUGGESTED)
    assert client.delete(f"/api/chats/{agent_group['id']}/members/{agents['A']['userId']}", headers=headers).status_code == 204
    count = len(app.state.protocol_fixture["activities"])
    assert client.post(f"/api/messages/{another}/actions", headers=headers,
                       json={"action": DOCUMENT_SUGGESTED[0]}).status_code == 403
    assert len(app.state.protocol_fixture["activities"]) == count
