from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from microsoft_agents.activity import Activity

from backend.app import create_app


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    "[open](javascript:alert(1))",
    "<svg onload=alert(1)>",
    '[link](https://example.test/" onmouseover="alert(1))',
])
def test_input_remains_inert_json_and_attachments(client, accounts, payload):
    headers = accounts["owner"]["headers"]
    response = client.post("/api/teams", headers=headers, json={"name": payload})
    assert response.status_code == 201
    assert response.headers["content-type"] == "application/json"
    team = response.json()
    assert team["name"] == payload
    cid = team["channels"][0]["conversationId"]
    response = client.post(f"/api/conversations/{cid}/messages", headers=headers, json={"text": payload})
    assert response.status_code == 201
    assert response.json()["text"] == payload
    filename = (payload + ".svg").replace("\\", "\\\\").replace('"', '\\"')
    multipart = (
        f'--safety-boundary\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: image/svg+xml\r\n\r\n{payload}\r\n--safety-boundary--\r\n"
    ).encode()
    response = client.post(f"/api/conversations/{cid}/files",
                           headers={**headers, "Content-Type": "multipart/form-data; boundary=safety-boundary"},
                           content=multipart)
    assert response.status_code == 201
    file = response.json()
    assert file["name"] == payload + ".svg"
    content = client.get(file["downloadUrl"], headers=headers)
    assert content.headers["content-type"] == "application/octet-stream"
    assert content.headers["content-disposition"].startswith("attachment;")
    assert content.headers["x-content-type-options"] == "nosniff"
    assert content.content == payload.encode()
    for path in ("/api/config", "/api/health", "/api/me", f"/api/teams/{team['id']}"):
        response = client.get(path, headers=headers)
        csp = response.headers["content-security-policy"]
        assert "script-src 'self';" in csp
        assert "style-src 'self' 'unsafe-inline'" in csp
        assert "object-src 'none'" in csp
        assert "base-uri 'none'" in csp
        assert "frame-ancestors 'none'" in csp
        assert response.headers["cache-control"] == "no-store"
    # Browser execution/DOM safety still requires the separate real UI E2E suite.


def test_channel_id_config_and_every_exported_revision_validates(settings, issuer):
    with TestClient(create_app(replace(settings, channel_id="local-collaboration"))) as client:
        headers = {"Authorization": f"Bearer {issuer.mint()}"}
        assert client.get("/api/config").json()["channelId"] == "local-collaboration"
        team = client.post("/api/teams", headers=headers, json={"name": "Configured channel"}).json()
        cid = team["channels"][0]["conversationId"]
        root = client.post(f"/api/conversations/{cid}/messages", headers=headers, json={"text": "root"}).json()
        reply = client.post(f"/api/conversations/{cid}/messages", headers=headers,
                            json={"text": "reply", "replyToId": root["id"]}).json()
        for message in (root, reply):
            mid = message["id"]
            assert client.patch(f"/api/messages/{mid}", headers=headers, json={"text": "edit"}).status_code == 200
            assert client.post(f"/api/messages/{mid}/reactions", headers=headers, json={"emoji": "👍"}).status_code == 200
            assert client.delete(f"/api/messages/{mid}", headers=headers).status_code == 200
        exported = client.get("/api/activities/export", headers=headers).json()
        assert len(exported) == 8
        for activity in exported:
            Activity.model_validate(activity)
            assert activity["channelId"] == "local-collaboration"
        assert {a["type"] for a in exported} == {"message", "messageUpdate", "messageDelete", "messageReaction"}
