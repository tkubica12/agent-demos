import hashlib
from dataclasses import replace

from fastapi.testclient import TestClient
from microsoft_agents.activity import Activity

from backend.app import create_app
from backend import db


def test_files_identity_sha_headers_attachment_ownership(client, accounts, resources):
    owner = accounts["owner"]["headers"]
    member = accounts["member"]["headers"]
    payload = b"<svg onload='alert(1)'>synthetic bytes</svg>\x00\xff"
    cid = resources["conversation"]
    response = client.post(f"/api/conversations/{cid}/files", headers=owner,
                           files={"file": ("../../danger.svg", payload, "image/svg+xml")})
    assert response.status_code == 201
    file = response.json()
    assert file["name"] == "../../danger.svg"
    assert file["size"] == len(payload)
    assert file["sha256"] == hashlib.sha256(payload).hexdigest()
    response = client.get(file["downloadUrl"], headers=member)
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-type"] == "application/octet-stream"
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert client.get(f"/api/files/{file['id']}", headers=member).json() == file
    assert file in client.get(f"/api/conversations/{cid}/files", headers=member).json()
    assert client.post(f"/api/conversations/{cid}/messages", headers=member, json={
        "text": "Another author's unattached upload", "attachmentIds": [file["id"]],
    }).status_code == 403
    assert client.post(f"/api/conversations/{cid}/messages", headers=owner, json={
        "text": "duplicate", "attachmentIds": [file["id"], file["id"]],
    }).status_code == 422
    other = resources["chat"]["conversationId"]
    assert client.post(f"/api/conversations/{other}/messages", headers=owner, json={
        "text": "cross-conversation upload", "attachmentIds": [file["id"]],
    }).status_code == 403
    response = client.post(f"/api/conversations/{cid}/messages", headers=owner, json={
        "text": "", "attachmentIds": [file["id"]],
    })
    assert response.status_code == 201
    message = response.json()
    assert message["attachments"] == [file]
    assert client.post(f"/api/conversations/{cid}/messages", headers=owner, json={
        "text": "reassignment", "attachmentIds": [file["id"]],
    }).status_code == 409
    activity = next(a for a in client.get("/api/activities/export", headers=owner).json() if a["id"] == message["id"])
    assert Activity.model_validate(activity)
    assert activity["attachments"][0]["content"]["downloadUrl"] == file["downloadUrl"]
    # Personal and group chat uploads use the same authorization/byte-preserving path.
    response = client.post(f"/api/conversations/{other}/files", headers=member,
                           files={"file": ("report.txt", payload, "text/plain")})
    assert response.status_code == 201
    assert client.get(response.json()["downloadUrl"], headers=owner).content == payload
    assert client.get(response.json()["downloadUrl"], headers=accounts["outsider"]["headers"]).status_code == 403


def test_actual_25mb_boundary_and_streamed_overflow_cleanup(client, app, accounts, resources):
    owner = accounts["owner"]["headers"]
    cid = resources["conversation"]
    limit = 25 * 1024 * 1024
    # A real multipart request exercises actual streamed bytes, not a forged Content-Length.
    payload = b"x" * limit
    response = client.post(f"/api/conversations/{cid}/files", headers=owner,
                           files={"file": ("boundary.bin", payload, "application/octet-stream")})
    assert response.status_code == 201
    assert response.json()["size"] == limit
    assert response.json()["sha256"] == hashlib.sha256(payload).hexdigest()
    before = set(app.state.storage.root.iterdir())
    response = client.post(f"/api/conversations/{cid}/files", headers=owner,
                           files={"file": ("too-large.bin", payload + b"x", "application/octet-stream")})
    assert response.status_code == 413
    assert "25 MB" in response.json()["detail"]
    assert set(app.state.storage.root.iterdir()) == before
    assert not list(app.state.storage.root.glob("*.partial"))
    assert len(client.get(f"/api/conversations/{cid}/files", headers=owner).json()) == 2


def test_streaming_tiny_chunks_no_length_and_malformed_multipart(client, app, accounts, resources):
    owner = accounts["owner"]["headers"]
    cid = resources["conversation"]
    boundary = "test-boundary"
    payload = b"abc" * 1024
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"chunks.bin\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()

    def chunks():
        for start in range(0, len(body), 19):
            yield body[start:start + 19]

    response = client.post(f"/api/conversations/{cid}/files",
                           headers={**owner, "Content-Type": f"multipart/form-data; boundary={boundary}"},
                           content=chunks())
    assert response.status_code == 201
    assert response.json()["sha256"] == hashlib.sha256(payload).hexdigest()
    before = set(app.state.storage.root.iterdir())
    for malformed in (b"not multipart", body[:-24]):
        response = client.post(f"/api/conversations/{cid}/files",
                               headers={**owner, "Content-Type": f"multipart/form-data; boundary={boundary}"},
                               content=malformed)
        assert response.status_code == 422
        assert set(app.state.storage.root.iterdir()) == before
    response = client.post(f"/api/conversations/{cid}/files", headers=owner, files=[
        ("file", ("one.txt", b"one", "text/plain")),
        ("file", ("two.txt", b"two", "text/plain")),
    ])
    assert response.status_code == 422
    assert set(app.state.storage.root.iterdir()) == before


def test_disk_failure_keeps_database_and_storage_consistent(client, app, accounts, resources, monkeypatch):
    before = set(app.state.storage.root.iterdir())
    def fail(_):
        raise OSError("Synthetic disk failure")
    monkeypatch.setattr(app.state.storage, "complete", fail)
    # TestClient normally propagates server exceptions; the transaction must still be rolled back.
    import pytest
    with pytest.raises(OSError, match="Synthetic disk failure"):
        client.post(f"/api/conversations/{resources['conversation']}/files", headers=accounts["owner"]["headers"],
                    files={"file": ("failed.txt", b"cannot persist", "text/plain")})
    assert set(app.state.storage.root.iterdir()) == before
    assert len(client.get(f"/api/conversations/{resources['conversation']}/files",
                          headers=accounts["owner"]["headers"]).json()) == 1
