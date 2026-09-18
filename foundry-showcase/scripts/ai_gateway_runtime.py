from __future__ import annotations

import json
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from deploy_phase2 import run_json


GATEWAY_API = "2025-09-01-preview"


def azure_token(subscription_id: str, resource: str) -> str:
    return run_json([
        "az", "account", "get-access-token", "--subscription", subscription_id,
        "--resource", resource, "--output", "json",
    ])["accessToken"]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "Redirect refused to protect credentials.", headers, fp)


def decode_response(content: str, content_type: str) -> dict[str, Any]:
    if not content:
        return {}
    if "text/event-stream" in content_type:
        messages = []
        for event in content.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
            if data and data != "[DONE]":
                messages.append(json.loads(data))
        replies = [message for message in messages if "result" in message or "error" in message]
        if len(replies) != 1:
            raise ValueError("Expected exactly one MCP response in the event stream.")
        return replies[0]
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("Expected an object response.")
    return result


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: dict[str, Any]

    def require(self, *statuses: int) -> Response:
        if self.status not in statuses:
            error = self.body.get("error", self.body)
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message", "") if isinstance(error, dict) else ""
            raise RuntimeError(f"Expected HTTP {statuses}; received {self.status}, error {code!r}: {message}")
        return self


def request(method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None = None) -> Response:
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=payload, method=method, headers=headers)
    try:
        response = build_opener(NoRedirect).open(req, timeout=90)
    except HTTPError as error:
        response = error
    with response:
        content = response.read().decode("utf-8")
        response_headers = {key.lower(): value for key, value in response.headers.items()}
        return Response(
            response.status,
            response_headers,
            decode_response(content, response_headers.get("content-type", "")),
        )


class Gateway:
    def __init__(self, subscription_id: str, resource_id: str):
        token = azure_token(subscription_id, "https://management.azure.com")
        self.subscription_id = subscription_id
        self.resource_id = resource_id
        self._arm_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.url = self.arm("GET", resource_id).require(200).body["properties"]["gatewayUrl"].rstrip("/")

    def arm(self, method: str, resource_id: str, body: dict[str, Any] | None = None,
            etag: str | None = None, api_version: str = GATEWAY_API) -> Response:
        headers = dict(self._arm_headers)
        if etag is not None:
            headers["If-Match"] = etag
        return request(method, f"https://management.azure.com{resource_id}?api-version={api_version}", headers, body)

    def runtime_key(self, name: str = "master") -> str:
        data = self.arm("POST", f"{self.resource_id}/apiKeys/{name}/listSecrets", {}).require(200).body
        key = data.get("primaryKey")
        if not isinstance(key, str) or not key:
            raise RuntimeError("The gateway did not return a primary runtime key.")
        return key

    def call(self, path: str, body: dict[str, Any], key: str,
             headers: dict[str, str] | None = None) -> Response:
        return request("POST", f"{self.url}{path}", {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "api-key": key,
            **(headers or {}),
        }, body)

    def call_new_key(self, path: str, body: dict[str, Any], key: str,
                     headers: dict[str, str] | None = None) -> Response:
        for attempt in range(11):
            response = self.call(path, body, key, headers)
            if response.status != 401 or attempt == 10:
                return response
            if attempt == 0:
                print("Waiting up to 30s for the temporary key to propagate to the runtime.", flush=True)
            time.sleep(3)
        raise RuntimeError("The temporary key did not reach the runtime within the bounded wait.")

    @contextmanager
    def temporary_key(self) -> Iterator[tuple[str, str]]:
        name = f"showcase-budget-{uuid4().hex[:12]}"
        resource_id = f"{self.resource_id}/apiKeys/{name}"
        print(f"Temporary budget-probe key: {resource_id} (value remains in memory).", flush=True)
        self.arm("PUT", resource_id, {"properties": {"displayName": "Temporary showcase budget probe"}}).require(200, 201)
        try:
            yield resource_id, self.runtime_key(name)
        finally:
            self.arm("DELETE", resource_id).require(200, 202, 204, 404)
            for attempt in range(6):
                result = self.arm("GET", resource_id)
                if result.status == 404:
                    print("Temporary budget-probe key revoked.", flush=True)
                    break
                result.require(200)
                if attempt == 5:
                    raise RuntimeError(f"Key revocation is still pending: {resource_id}")
                time.sleep(2)


def evidence_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        name: value for name, value in headers.items()
        if name == "retry-after" or "budget" in name or "cost" in name or "ratelimit" in name or name.endswith("request-id")
    }


def require_rpc_result(response: Response) -> dict[str, Any]:
    response.require(200)
    if "error" in response.body:
        raise RuntimeError(f"MCP failed: {response.body['error']}")
    result = response.body.get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise RuntimeError(f"MCP did not return a successful result: {result}")
    return result


def require_tool_denial(response: Response, *, blocked: bool) -> None:
    if blocked:
        response.require(200)
        result = response.body.get("result", {})
        denial = result.get("_meta", {}).get("com.microsoft.azure.ai.gateway/denial", {})
        if result.get("isError") is not True or denial.get("code") != "ToolNotAvailable":
            raise RuntimeError(f"Expected an explicit MCP gateway block, received {response.body}")
    else:
        response.require(404)
        if response.body.get("statusCode") != 404:
            raise RuntimeError(f"Expected the unpublished-tool rejection, received {response.body}")


def mcp_demo(gateway: Gateway, key: str, trace_headers: dict[str, str] | None = None) -> dict[str, Any]:
    path = "/default/toolservers/microsoft-learn/mcp"
    initialized = gateway.call(path, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "foundry-showcase", "version": "1.0"}},
    }, key, trace_headers)
    initialization = require_rpc_result(initialized)
    headers = {**(trace_headers or {}), "MCP-Protocol-Version": initialization["protocolVersion"]}
    if session := initialized.headers.get("mcp-session-id"):
        headers["Mcp-Session-Id"] = session
    gateway.call(path, {"jsonrpc": "2.0", "method": "notifications/initialized"}, key, headers).require(202, 204)
    listed = require_rpc_result(gateway.call(path, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    }, key, headers))
    names = sorted(tool["name"] for tool in listed["tools"])
    if names != ["microsoft_docs_fetch", "microsoft_docs_search"]:
        raise RuntimeError(f"Unexpected published tool set: {names}")
    search = require_rpc_result(gateway.call(path, {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "microsoft_docs_search", "arguments": {"query": "Azure AI Gateway managed identity"}},
    }, key, headers))
    if not search.get("content"):
        raise RuntimeError("The allowed documentation search returned no content.")
    denied = {}
    for request_id, name, arguments in (
        (4, "microsoft_docs_fetch", {"url": "https://learn.microsoft.com/training/support/mcp"}),
        (5, "microsoft_code_sample_search", {"query": "Azure managed identity", "language": "python"}),
    ):
        result = gateway.call(path, {
            "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }, key, headers)
        require_tool_denial(result, blocked=name == "microsoft_docs_fetch")
        denied[name] = {"http_status": result.status, "error": result.body}
    return {"published_tools": names, "search_content_blocks": len(search["content"]), "denied": denied}


def model_success(gateway: Gateway, path: str, body: dict[str, Any], key: str,
                  headers: dict[str, str]) -> Response:
    for attempt in range(2):
        response = gateway.call(path, body, key, headers)
        if attempt == 0 and response.status == 429 and response.body.get("statusCode") == 429:
            delay = int(response.headers.get("retry-after", "0"))
            if 0 < delay <= 65 and response.body.get("message", "").startswith("Rate limit is exceeded."):
                wait = max(60, delay) + 1
                print(f"Rate window already used; waiting a full {wait}s for the positive demonstration.", flush=True)
                time.sleep(wait)
                continue
        return response.require(200)
    raise RuntimeError("The positive request could not complete within the bounded retry.")


def demo(gateway: Gateway) -> dict[str, Any]:
    key = gateway.runtime_key()
    trace_id = uuid4().hex
    trace_headers = {"traceparent": f"00-{trace_id}-{uuid4().hex[:16]}-01"}
    print(f"Gateway demonstration trace: {trace_id}", flush=True)
    responses = model_success(gateway, "/default/models/openai/v1/responses", {
        "model": "showcase-chat", "input": "Reply exactly GATEWAY_OK.",
        "max_output_tokens": 256, "reasoning": {"effort": "low"},
    }, key, trace_headers)
    text = "".join(
        content.get("text", "")
        for item in responses.body.get("output", [])
        for content in item.get("content", [])
        if content.get("type") == "output_text"
    )
    if "GATEWAY_OK" not in text:
        raise RuntimeError("The Responses request did not return the requested text.")
    chat_body = {"model": "showcase-chat", "messages": [{"role": "user", "content": "Reply exactly GATEWAY_OK."}],
                 "max_completion_tokens": 256, "reasoning_effort": "low"}
    chat = model_success(gateway, "/default/models/openai/v1/chat/completions", chat_body, key, trace_headers)
    if "GATEWAY_OK" not in (chat.body["choices"][0]["message"].get("content") or ""):
        raise RuntimeError("The chat request did not return the requested text.")
    throttled = None
    for attempt in range(6):
        reply = gateway.call("/default/models/openai/v1/chat/completions", chat_body, key, trace_headers)
        if reply.status == 429:
            if (not reply.headers.get("retry-after") or reply.body.get("statusCode") != 429
                    or not reply.body.get("message", "").startswith("Rate limit is exceeded.")):
                raise RuntimeError(f"Expected the gateway request-rate rejection, received {reply.body}")
            throttled = {
                "http_status": reply.status, "error": reply.body, "headers": evidence_headers(reply.headers),
                "burst_request": attempt + 1, "configured_requests_per_minute": 5,
                "precision": "Approximate preview limiter; do not promise rejection on exactly request six.",
            }
            break
        reply.require(200)
    if throttled is None:
        raise RuntimeError("The five-request-per-minute policy did not reject the bounded eight-request burst.")
    return {
        "trace_id": trace_id,
        "responses": {"text": text, "usage": responses.body["usage"], "headers": evidence_headers(responses.headers)},
        "chat": {"usage": chat.body["usage"], "headers": evidence_headers(chat.headers)},
        "rate_limit": throttled,
        "mcp": mcp_demo(gateway, key, trace_headers),
        "credentials": "Existing gateway key retrieved with operator Entra identity; held only in process memory.",
    }


def budget_policies(policies: list[dict[str, Any]], key_id: str, amount: float | None) -> list[dict[str, Any]]:
    updated = deepcopy(policies)
    budgets = [policy for policy in updated if policy.get("type") == "costLimit"
               and policy.get("id") == "showcase-daily-budget" and policy.get("counterKey") == "Identity"]
    if len(budgets) != 1:
        raise ValueError("Expected exactly one showcase per-identity budget policy.")
    budget = budgets[0]
    overrides = [override for override in budget.get("overrides", [])
                 if override.get("apiKeyResourceId", "").lower() != key_id.lower()]
    if amount is not None:
        if not 0 < amount <= 0.05:
            raise ValueError("The probe override must be positive and no larger than the normal showcase budget.")
        overrides.append({"apiKeyResourceId": key_id, "amount": amount})
    if overrides:
        budget["overrides"] = overrides
    else:
        budget.pop("overrides", None)
    return updated


def set_budget_override(gateway: Gateway, key_id: str, amount: float | None) -> None:
    resource_id = f"{gateway.resource_id}/workspaces/default/modelProviders/showcase-foundry/models/showcase-chat"
    for _ in range(3):
        current = gateway.arm("GET", resource_id).require(200)
        etag = current.headers.get("etag")
        if not etag:
            raise RuntimeError("Refusing to edit the budget without a current ETag.")
        policies = budget_policies(current.body["properties"]["policies"], key_id, amount)
        changed = gateway.arm("PATCH", resource_id, {"properties": {"policies": policies}}, etag=etag)
        if changed.status == 412:
            continue
        changed.require(200)
        verified = gateway.arm("GET", resource_id).require(200).body["properties"]["policies"]
        if budget_policies(verified, key_id, amount) != verified:
            raise RuntimeError("The per-key budget change was not retained.")
        return
    raise RuntimeError("Concurrent model-policy changes prevented a safe per-key budget update.")


def budget_demo(gateway: Gateway) -> dict[str, Any]:
    trace_id = uuid4().hex
    trace_headers = {"traceparent": f"00-{trace_id}-{uuid4().hex[:16]}-01"}
    with gateway.temporary_key() as (key_id, key):
        try:
            set_budget_override(gateway, key_id, 0.000001)
            outcomes = []
            for attempt in range(4):
                call = gateway.call_new_key if attempt == 0 else gateway.call
                result = call("/default/models/openai/v1/chat/completions", {
                    "model": "showcase-chat", "messages": [{"role": "user", "content": "Reply OK."}],
                    "max_completion_tokens": 64, "reasoning_effort": "low",
                }, key, trace_headers)
                outcomes.append({
                    "http_status": result.status, "headers": evidence_headers(result.headers),
                    "error": result.body if result.status != 200 else None,
                })
                if (result.status == 403 and result.body.get("statusCode") == 403
                        and result.body.get("message", "").startswith("LLM cost quota is exceeded.")):
                    return {"trace_id": trace_id, "override_usd": 0.000001, "outcomes": outcomes,
                            "scope": "Only this temporary key; normal $0.05/day policy remains unchanged."}
                result.require(200)
                time.sleep(2)
            raise RuntimeError(f"The per-key budget did not reject the bounded four-request probe: {outcomes}")
        finally:
            set_budget_override(gateway, key_id, None)
