"""Stored-message authorization, frame isolation and Teams widget invoke contracts."""

import asyncio
import copy
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import select

from backend import db, domain as d
from backend.html_widgets import FRAME_CSP, widgets
from tests.test_agent_channel import app, client, agents, bearer, CHANNEL_ID


PAYLOAD = {
    "type": "widget/mcp-ui", "name": "Planner", "html": "<!doctype html><script>parent.postMessage({},'*')</script>",
    "domain": "https://teams.microsoft.com",
    "securityPolicy": {"connectDomains": [], "resourceDomains": [], "frameDomains": [], "baseUriDomains": []},
    "mcpUrl": "https://example.invalid/mcp", "correlationId": "stored-correlation",
}
RESULT = {"responseType": "htmlwidget/calltoolresult", "callToolResult": {
    "content": [{"type": "text", "text": "Accepted"}], "structuredContent": {"status": "accepted"}, "isError": False,
}}
FRAME_HEADERS = {"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Site": "same-origin"}


def text(payload=PAYLOAD):
    return "Fallback **planner**.\n\n```html-widget\n" + json.dumps(payload) + "\n```\nAfter."


@pytest.fixture
def widget(client, app, accounts, agents):
    owner = accounts["owner"]["headers"]
    chat = client.post("/api/chats", headers=owner, json={"memberIds": [agents["A"]["userId"]]}).json()
    cid = chat["conversationId"]
    posted = client.post(f"/v3/conversations/{quote(cid)}/activities", headers=agents["A"]["headers"],
                         json={"type": "message", "textFormat": "extendedmarkdown", "text": text()})
    assert posted.status_code == 200, posted.text
    mid = posted.json()["id"]
    app.state.protocol_fixture["invokeResponse"] = copy.deepcopy(RESULT)
    return {"id": mid, "cid": cid, "agent": agents["A"], "path": f"/api/messages/{mid}/widgets/0"}


def test_parser_is_gated_and_malformed_fences_are_inert():
    activity = {"type": "message", "textFormat": "extendedmarkdown", "text": text()}
    assert widgets(activity) == {0: PAYLOAD}
    for changed in [{"textFormat": "markdown"}, {"type": "typing"}, {"text": text({"type": "wrong"})},
                    {"text": "````text\n" + text() + "\n````"},
                    {"text": text({**PAYLOAD, "domain": "https://user:password@example.invalid"})},
                    {"text": text({**PAYLOAD, "html": "x" * 100_000})}]:
        assert widgets({**activity, **changed}) == {}
    assert widgets({**activity, "text": '```html-widget\n{"type":"widget/mcp-ui","type":"wrong"}\n```'}) == {}
    unicode = {**PAYLOAD, "name": "Planner\u2028next\u0085line"}
    assert widgets({**activity, "text": text(unicode)}) == {0: unicode}
    assert widgets({**activity, "text": "\u2028```html-widget\n" + json.dumps(PAYLOAD) + "\n```"}) == {}


def test_signed_frame_is_single_use_bound_and_has_independent_http_csp(client, app, accounts, widget):
    assert client.post(widget["path"] + "/frame").status_code == 401
    assert client.post(widget["path"] + "/frame", headers=accounts["outsider"]["headers"]).status_code == 403
    minted = client.post(widget["path"] + "/frame", headers=accounts["owner"]["headers"]).json()
    url = minted["url"]
    assert client.get(url).status_code == 403
    assert client.get(url, headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "same-origin"}).status_code == 403
    assert client.get(url, headers={**FRAME_HEADERS, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get(url + "tamper", headers=FRAME_HEADERS).status_code == 403
    response = client.get(url, headers=FRAME_HEADERS)
    assert response.status_code == 200
    assert response.text == PAYLOAD["html"]
    assert response.headers["content-security-policy"] == FRAME_CSP
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["cache-control"] == "no-store"
    assert client.get(url, headers=FRAME_HEADERS).status_code == 403
    assert "frame-src 'self'" in client.get("/api/health").headers["content-security-policy"]


def test_frames_revalidate_current_content_membership_and_expiry(client, app, accounts, widget, monkeypatch):
    owner = accounts["owner"]["headers"]
    url = client.post(widget["path"] + "/frame", headers=owner).json()["url"]
    with app.state.sessions() as session:
        message = session.scalar(select(db.Message).where(db.Message.id == widget["id"]))
        message.activity = {**message.activity, "text": text({**PAYLOAD, "html": "<p>Changed</p>"})}
        session.commit()
    assert client.get(url, headers=FRAME_HEADERS).status_code == 409
    url = client.post(widget["path"] + "/frame", headers=owner).json()["url"]
    now = time.time()
    monkeypatch.setattr("backend.html_widgets.time.time", lambda: now + 61)
    assert client.get(url, headers=FRAME_HEADERS).status_code == 403


def test_tool_envelope_uses_real_user_personal_conversation_and_host_metadata(client, app, accounts, widget):
    result = client.post(widget["path"] + "/tools", headers=accounts["owner"]["headers"], json={
        "name": "autopilot.widget.submit", "arguments": {"token": "opaque", "values": {"budget": "7"},
                                                      "__msteams__": {"mcpUrl": "https://attacker.invalid"}},
    })
    assert result.status_code == 200, result.text
    assert result.json() == RESULT
    activity = app.state.protocol_fixture["activities"][-1]
    assert activity["type"] == "invoke" and activity["name"] == "htmlwidget/calltool"
    assert activity["from"]["id"] == accounts["owner"]["user"]["id"]
    assert activity["from"]["aadObjectId"]
    assert activity["conversation"]["id"] == widget["cid"]
    assert activity["conversation"]["conversationType"] == "personal"
    assert activity["recipient"]["id"] == "28:" + widget["agent"]["clientId"]
    assert activity["value"]["arguments"]["__msteams__"] == {
        "mcpUrl": PAYLOAD["mcpUrl"], "correlationId": PAYLOAD["correlationId"],
    }
    assert activity["replyToId"] == widget["id"]


@pytest.mark.parametrize("response,status", [
    ({}, 200), ({**RESULT, "responseType": "wrong"}, 200),
    ({**RESULT, "callToolResult": {**RESULT["callToolResult"], "isError": "false"}}, 200),
    ({**RESULT, "callToolResult": {**RESULT["callToolResult"], "content": "wrong"}}, 200),
    (RESULT, 500), (RESULT, 202),
    ({**RESULT, "callToolResult": {**RESULT["callToolResult"], "structuredContent": {"x": "a" * 70_000}}}, 200),
])
def test_malformed_or_failed_agent_results_are_explicit(client, app, accounts, widget, response, status):
    app.state.protocol_fixture.update(invokeResponse=response, invokeStatus=status)
    result = client.post(widget["path"] + "/tools", headers=accounts["owner"]["headers"],
                         json={"name": "autopilot.widget.submit", "arguments": {}})
    assert result.status_code == 502
    with app.state.sessions() as session:
        delivery = session.scalars(select(db.AgentDelivery).where(db.AgentDelivery.activity["type"].as_string() == "invoke")).one()
        assert delivery.status == "failed"
        assert delivery.error


def test_iserror_is_returned_to_widget_and_calls_are_rate_limited(client, app, accounts, widget):
    app.state.protocol_fixture["invokeResponse"]["callToolResult"]["isError"] = True
    for _ in range(6):
        response = client.post(widget["path"] + "/tools", headers=accounts["owner"]["headers"],
                               json={"name": "autopilot.widget.submit", "arguments": {}})
        assert response.status_code == 200
        assert response.json()["callToolResult"]["isError"] is True
    assert client.post(widget["path"] + "/tools", headers=accounts["owner"]["headers"],
                       json={"name": "autopilot.widget.submit", "arguments": {}}).status_code == 429


def test_actions_reject_idor_bad_index_human_messages_and_oversize(client, app, accounts, widget):
    body = {"name": "autopilot.widget.submit", "arguments": {}}
    assert client.post(widget["path"] + "/tools", headers=accounts["outsider"]["headers"], json=body).status_code == 403
    assert client.post(widget["path"].rsplit("/", 1)[0] + "/99/tools",
                       headers=accounts["owner"]["headers"], json=body).status_code == 422
    assert client.post(widget["path"] + "/tools", headers=accounts["owner"]["headers"],
                       json={**body, "name": "unknown"}).status_code == 422
    assert client.post(widget["path"] + "/tools", headers=accounts["owner"]["headers"],
                       json={**body, "arguments": {"x": "a" * 20_000}}).status_code == 413
    human = client.post(f"/api/conversations/{quote(widget['cid'])}/messages",
                        headers=accounts["owner"]["headers"], json={"text": text()}).json()
    assert client.post(f"/api/messages/{human['id']}/widgets/0/frame",
                       headers=accounts["owner"]["headers"]).status_code == 422


def test_only_valid_widget_messages_get_larger_utf8_byte_limit(client, accounts, widget):
    route = f"/v3/conversations/{quote(widget['cid'])}/activities"
    headers = widget["agent"]["headers"]
    for format, source, status in [
        ("extendedmarkdown", text({**PAYLOAD, "html": "<p>" + "a" * 35_000 + "</p>"}), 200),
        ("markdown", "a" * 35_000, 422), ("extendedmarkdown", "a" * 35_000, 422),
        ("extendedmarkdown", text({**PAYLOAD, "html": "\u010d" * 60_000}), 422),
    ]:
        assert client.post(route, headers=headers, json={
            "type": "message", "textFormat": format, "text": source,
        }).status_code == status


def test_concurrent_calls_are_rejected_and_timeouts_report_unknown_gateway_outcome(client, app, accounts, widget, monkeypatch):
    entered = threading.Event()

    async def slow(activity):
        if activity["type"] == "invoke":
            entered.set()
            await asyncio.sleep(.3)

    app.state.protocol_fixture["onActivity"] = slow
    owner = accounts["owner"]["headers"]
    body = {"name": "autopilot.widget.submit", "arguments": {}}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, widget["path"] + "/tools", headers=owner, json=body)
        assert entered.wait(5)
        assert client.post(widget["path"] + "/tools", headers=owner, json=body).status_code == 409
        assert pending.result().status_code == 200
    original_timeout = asyncio.timeout
    monkeypatch.setattr("backend.html_widgets.asyncio.timeout", lambda seconds: original_timeout(.03))
    response = client.post(widget["path"] + "/tools", headers=owner, json=body)
    assert response.status_code == 504
    assert "outcome unknown" in response.json()["detail"]
    with app.state.sessions() as session:
        deliveries = session.scalars(select(db.AgentDelivery).where(
            db.AgentDelivery.activity["type"].as_string() == "invoke",
        )).all()
        assert sorted(value.status for value in deliveries) == ["delivered", "failed"]


def test_deleted_widget_cannot_refresh_or_call(client, app, accounts, widget):
    owner = accounts["owner"]["headers"]
    url = client.post(widget["path"] + "/frame", headers=owner).json()["url"]
    with app.state.sessions() as session:
        message = session.scalar(select(db.Message).where(db.Message.id == widget["id"]))
        message.deleted = True
        session.commit()
    assert client.get(url, headers=FRAME_HEADERS).status_code == 409
    assert client.post(widget["path"] + "/tools", headers=owner,
                       json={"name": "autopilot.widget.submit", "arguments": {}}).status_code == 409
