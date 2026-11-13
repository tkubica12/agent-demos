from __future__ import annotations

import time
import uuid

import httpx


def verify_chat(origin: str, *, access_code: str = "", access_token: str = "",
                client: httpx.Client | None = None, timeout: float = 300) -> dict:
    if not access_code and not access_token:
        raise ValueError("Real chat verification requires a DEMO access code or an assigned user's SPA access token.")
    if client is None:
        with httpx.Client(base_url=origin, timeout=timeout, follow_redirects=False) as owned:
            return verify_chat(origin, access_code=access_code, access_token=access_token,
                               client=owned, timeout=timeout)
    marker = "DEPLOYMENT-" + uuid.uuid4().hex[:16]
    if access_code:
        response = client.post("/api/demo/login", json={
            "accessCode": access_code, "displayName": "Deployment smoke " + marker,
        })
        response.raise_for_status()
        access_token = response.json()["accessToken"]
    headers = {"Authorization": "Bearer " + access_token}
    response = client.get("/api/users", headers=headers)
    response.raise_for_status()
    agents = [user for user in response.json() if user.get("isAgent")]
    if len(agents) != 1:
        raise RuntimeError(f"An isolated stack must register exactly one agent; found {len(agents)}.")
    response = client.post("/api/chats", headers=headers, json={"memberIds": [agents[0]["id"]]})
    response.raise_for_status()
    conversation = response.json()["conversationId"]
    path = "/api/conversations/" + conversation + "/messages"
    response = client.post(path, headers=headers, json={
        "text": "Deployment verification: reply with exactly this text and nothing else: " + marker,
        "textFormat": "plain",
    })
    response.raise_for_status()
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(path, headers=headers)
        response.raise_for_status()
        replies = [message for message in response.json()
                   if message.get("author", {}).get("isAgent") and marker in message.get("text", "")]
        if replies:
            return {"conversation_id": conversation, "reply_id": replies[-1]["id"], "marker": marker,
                    "verified": "simulator login -> message -> bridge -> Hermes/model -> simulator reply"}
        if time.monotonic() >= deadline:
            raise TimeoutError("The real Hermes/model reply did not return through the simulator channel.")
        time.sleep(2)
