"""Correlate actual simulator downloads with authenticated native and Foundry evidence."""

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlparse
import uuid

from network_probe import check


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(url, headers, label, body=None):
    request = urllib.request.Request(
        url, headers=headers, data=json.dumps(body).encode() if body is not None else None,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            data = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{label} failed HTTP {error.code}.") from None
    except urllib.error.URLError as error:
        raise RuntimeError(f"{label} transport failed: {type(error.reason).__name__}.") from None
    if len(data) > 1024 * 1024:
        raise ValueError(f"{label} response exceeds the metadata limit.")
    return json.loads(data)


def select_delivery(payload, expected, now):
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("Gateway delivery evidence has no event list.")
    matches = [
        event for event in events if isinstance(event, dict)
        and event.get("conversationId") == expected["conversationId"]
        and event.get("requestActivityId") == expected["requestActivityId"]
        and event.get("activityId") == expected["replyActivityId"]
        and event.get("name") == expected["filename"]
    ]
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous exact request/reply Activity delivery evidence.")
    event = matches[0]
    if event.get("sha256") != expected["sha256"] or event.get("size") != expected["size"]:
        raise ValueError("Native delivery bytes do not match the actual browser download.")
    timestamp = event.get("ts")
    if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
            or timestamp < expected["requestedAt"] - 30 or not -30 <= now - timestamp <= 600):
        raise ValueError("Native delivery evidence is expired or predates this request.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", event.get("nativeRunId") or ""):
        raise ValueError("Native delivery has no valid run identity.")
    if not re.fullmatch(r"[0-9a-f]{32}", event.get("traceId") or ""):
        raise ValueError("Native delivery has no valid trace identity.")
    return {key: event[key] for key in (
        "conversationId", "requestActivityId", "activityId", "name", "size", "sha256",
        "nativeRunId", "traceId", "ts",
    )}


def select_widget_turn(payload, expected, now):
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("Gateway widget evidence has no event list.")
    scoped = [
        item for item in events if isinstance(item, dict)
        and item.get("conversationId") == expected["conversationId"]
    ]
    replies = [
        item for item in scoped if item.get("event") == "turnReplySent"
        and item.get("activityId") == expected["replyActivityId"]
    ]
    if len(replies) != 1:
        raise ValueError("Missing or ambiguous exact widget reply Activity evidence.")
    turn = replies[0]
    linkage = {}
    if expected["turn"] == "producing":
        if (turn.get("requestActivityId") != expected["requestActivityId"]
                or turn.get("replyToId") is not None or turn.get("interactionId") is not None):
            raise ValueError("Widget-producing reply does not match the exact browser request.")
    elif expected["turn"] == "followup":
        submissions = [
            item for item in scoped if item.get("event") == "widgetSubmissionAccepted"
            and item.get("replyToId") == expected["widgetActivityId"]
        ]
        accepted = [item for item in submissions if item.get("status") == "accepted"]
        if len(accepted) != 1:
            raise ValueError("Missing or ambiguous actual accepted widget invoke identity.")
        invoke = accepted[0]
        interaction = invoke.get("interactionId")
        if (not isinstance(interaction, str) or not interaction
                or turn.get("requestActivityId") != invoke.get("requestActivityId")
                or turn.get("replyToId") != expected["widgetActivityId"]
                or turn.get("interactionId") != interaction):
            raise ValueError("Native follow-up is not linked to the exact accepted internal invoke.")
        replays = [
            item for item in submissions if item.get("status") == "already_submitted"
            and item.get("interactionId") == interaction
            and item.get("requestActivityId") != invoke.get("requestActivityId")
        ]
        if len(replays) != 1 or any(
            item.get("event") == "turnReplySent"
            and item.get("requestActivityId") == replays[0].get("requestActivityId")
            for item in scoped
        ):
            raise ValueError("Actual same-token replay rejection without a native follow-up is unverified.")
        for item in (invoke, replays[0]):
            timestamp = item.get("ts")
            if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
                    or timestamp < expected["requestedAt"] - 30 or not -30 <= now - timestamp <= 600):
                raise ValueError("Widget invoke/replay evidence is stale.")
        linkage = {
            "acceptedInvokeId": invoke["requestActivityId"],
            "replayInvokeId": replays[0]["requestActivityId"],
            "interactionId": interaction, "replayStatus": "already_submitted",
        }
    else:
        raise ValueError("Unsupported widget turn evidence mode.")
    timestamp = turn.get("ts")
    if (type(timestamp) not in (int, float) or not math.isfinite(timestamp)
            or timestamp < expected["requestedAt"] - 30 or not -30 <= now - timestamp <= 600):
        raise ValueError("Widget native reply evidence is stale.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", turn.get("nativeRunId") or ""):
        raise ValueError("Widget reply has no valid native run identity.")
    if not re.fullmatch(r"[0-9a-f]{32}", turn.get("traceId") or ""):
        raise ValueError("Widget reply has no valid native trace identity.")
    return {**{key: turn.get(key) for key in (
        "conversationId", "requestActivityId", "activityId", "replyToId",
        "interactionId", "nativeRunId", "traceId", "ts",
    )}, **linkage}


def validate_run(payload, run_id, *, require_terminal=True):
    if payload.get("runId") != run_id or payload.get("status") != "completed":
        raise ValueError("Evidence is not the exact completed native run.")
    runtime = payload.get("runtime")
    if (payload.get("runtimeMetadataAvailable") is not True or not isinstance(runtime, dict)
            or not runtime.get("provider") or not runtime.get("model")):
        raise ValueError("Actual served native runtime metadata is unavailable.")
    for value in runtime.values():
        if value is not None and (not isinstance(value, str)
                                  or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value)):
            raise ValueError("Invalid native runtime identity metadata.")
    tools = payload.get("tools")
    if not isinstance(tools, list):
        raise ValueError("Native tool evidence is unavailable.")
    starts = {
        item.get("toolCallId") for item in tools if isinstance(item, dict)
        and item.get("event") == "tool.started" and item.get("tool") == "terminal"
        and isinstance(item.get("toolCallId"), str)
    }
    completed = [
        item for item in tools if isinstance(item, dict)
        and item.get("event") == "tool.completed" and item.get("tool") == "terminal"
        and item.get("toolCallId") in starts and item.get("error") is False
        and type(item.get("duration")) in (int, float)
        and math.isfinite(item["duration"]) and item["duration"] >= 0
    ]
    if require_terminal and not completed:
        raise ValueError("No actual successful terminal start/completion with matching tool-call identity.")
    return {
        "runId": run_id, "status": "completed",
        "runtime": {key: runtime.get(key) for key in ("provider", "model", "route_source")},
        "usage": {key: value for key, value in payload.get("usage", {}).items()
                  if key in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
                  and type(value) is int and value >= 0},
        "terminalCompletions": [
            {key: item[key] for key in ("tool", "toolCallId", "duration", "error")} for item in completed
        ],
    }


def foundry_trace(payload):
    rows = []
    for table in payload.get("tables", []):
        names = [column["name"] for column in table["columns"]]
        rows.extend(dict(zip(names, row, strict=True)) for row in table["rows"])
    chats = [
        row for row in rows if row.get("name") == "chat" and row.get("success") is True
        and isinstance(row.get("responseModel"), str) and row["responseModel"]
        and type(row.get("inputTokens")) is int and row["inputTokens"] > 0
        and type(row.get("outputTokens")) is int and row["outputTokens"] > 0
    ]
    hosts = []
    for row in rows:
        if row.get("type") != "HTTP":
            continue
        if row.get("success") is not True or not str(row.get("resultCode", "")).isdigit():
            continue
        if not 200 <= int(row["resultCode"]) < 300:
            continue
        target = row.get("target")
        if not isinstance(target, str):
            continue
        parsed = urlparse(target if "://" in target else f"https://{target}")
        host = parsed.hostname or ""
        if not parsed.username and not parsed.password and host.endswith(
            (".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com")
        ):
            hosts.append(host)
    if not chats or not hosts:
        return None
    return {
        "observedFoundryHost": hosts[0],
        "servedModel": chats[0]["responseModel"],
        "inputTokens": chats[0]["inputTokens"], "outputTokens": chats[0]["outputTokens"],
        "proof": "successful-foundry-http-dependency-and-served-model-usage-in-exact-trace",
    }


def query_trace(credential, app_id, trace_id):
    query = (
        f'dependencies | where operation_Id == "{trace_id}" '
        '| project name, type, success, resultCode, target, '
        'responseModel=tostring(customDimensions["gen_ai.response.model"]), '
        'inputTokens=toint(customDimensions["gen_ai.usage.input_tokens"]), '
        'outputTokens=toint(customDimensions["gen_ai.usage.output_tokens"])'
    )
    token = credential.get_token("https://api.applicationinsights.io/.default").token
    result = read_json(
        f"https://api.applicationinsights.io/v1/apps/{app_id}/query",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        "Application Insights query", {"query": query, "timespan": "PT1H"},
    )
    if result.get("error"):
        raise RuntimeError("Application Insights returned a query error.")
    observed = foundry_trace(result)
    if observed:
        observed["network"] = check(f"https://{observed['observedFoundryHost']}", transport_only=True)
        observed["traceId"] = trace_id
    return observed


def query_trace_in_sandbox(configuration, app_id, trace_id):
    from azure.containerapps.sandbox import SandboxGroupClient, endpoint_for_region
    from azure.identity import AzureCliCredential

    sandbox_id = str(uuid.UUID(configuration["sandbox_id"]))
    app_id = str(uuid.UUID(app_id))
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
        raise ValueError("Invalid exact native trace identity.")
    sources = {
        name: Path(__file__).with_name(name + ".py").read_text(encoding="utf-8")
        for name in ("network_probe", "verify_native_evidence")
    }
    completion_nonce = uuid.uuid4().hex
    payload = base64.b64encode(json.dumps({
        "sources": sources, "app_id": app_id, "trace_id": trace_id,
        "completion_nonce": completion_nonce,
    }).encode()).decode()
    script = (
        "import base64,json,sys,types; "
        f"p=json.loads(base64.b64decode({payload!r})); "
        "\nfor name,source in p['sources'].items():"
        "\n m=types.ModuleType(name);sys.modules[name]=m;exec(compile(source,name,'exec'),m.__dict__)"
        "\nimport os"
        "\nfrom azure.identity import ManagedIdentityCredential"
        "\nfrom network_probe import check"
        "\nfrom verify_native_evidence import query_trace"
        "\nnetwork=check('https://api.applicationinsights.io',transport_only=True)"
        "\nwith ManagedIdentityCredential(client_id=os.environ.get('AZURE_CLIENT_ID')) as credential:"
        "\n observed=query_trace(credential,p['app_id'],p['trace_id'])"
        "\nprint(json.dumps({'queryNetwork':network,'observed':observed,'completionNonce':p['completion_nonce']}))"
    )
    python = configuration.get("python_path", "/app/.venv/bin/python")
    if not isinstance(python, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", python) or ".." in python.split("/"):
        raise ValueError("Configure the attested Sandbox's absolute Python interpreter path.")
    with AzureCliCredential() as credential, SandboxGroupClient(
        endpoint_for_region(configuration.get("region", "swedencentral")), credential,
        subscription_id=str(uuid.UUID(configuration["subscription_id"])),
        resource_group=configuration["resource_group"], sandbox_group=configuration["group"],
    ) as group, group.get_sandbox_client(sandbox_id) as sandbox:
        result = sandbox.exec(f"{shlex.quote(python)} -c {shlex.quote(script)}")
    if type(result.exit_code) is not int or result.exit_code != 0:
        raise RuntimeError(f"In-VNet Sandbox query execution failed exit {result.exit_code}; no credentials or stderr exported.")
    result = json.loads(result.stdout)
    if not isinstance(result, dict) or result.get("completionNonce") != completion_nonce:
        raise ValueError("Sandbox query did not return this invocation's completed output.")
    if result.get("queryNetwork", {}).get("scope") != "private-dns-tls" or result["queryNetwork"].get("status") != "pass":
        raise ValueError("Sandbox query did not prove private App Insights DNS/TLS.")
    result["queryExecution"] = {
        "sandboxId": sandbox_id, "credential": "sandbox-managed-identity",
        "inlineSourceSha256": hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest(),
        "operatorCredentialsExported": False,
        "completionVerified": True,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--runtime-url", required=True)
    parser.add_argument("--key-env", required=True)
    parser.add_argument("--app-insights-resource-id", required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--widget-turn", choices=("producing", "followup"))
    parser.add_argument("--temporary-public", action="store_true")
    parser.add_argument("--credential", choices=("managed-identity", "azure-cli"), default="managed-identity")
    parser.add_argument("--query-sandbox", type=Path)
    args = parser.parse_args()
    from azure.core.exceptions import AzureError
    from azure.identity import AzureCliCredential, ManagedIdentityCredential

    evidence = {"status": "fail"}
    try:
        if not 1 <= args.timeout_seconds <= 600:
            raise ValueError("Evidence timeout must be between 1 and 600 seconds.")
        key = os.environ.get(args.key_env)
        if not key:
            raise ValueError("Missing private evidence key environment variable.")
        if not re.fullmatch(
            r"/subscriptions/[0-9a-f-]{36}/resourceGroups/[a-z0-9_.()-]+/"
            r"providers/Microsoft\.Insights/components/[a-z0-9_.-]+",
            args.app_insights_resource_id, re.I,
        ):
            raise ValueError("Provide the explicitly authorized Application Insights ARM resource ID.")
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        evidence["gatewayNetwork"] = check(args.gateway_url, transport_only=True, temporary_public=args.temporary_public)
        evidence["runtimeNetwork"] = check(args.runtime_url, transport_only=True, temporary_public=args.temporary_public)
        headers = {"X-Autopilot-Key": key}
        route = "turns" if args.widget_turn else "generated-files"
        gateway = read_json(
            f"{args.gateway_url.rstrip('/')}/internal/{route}/evidence?"
            + urlencode({"conversation_id": expected["conversationId"]}), headers, "Gateway evidence",
        )
        if args.widget_turn:
            evidence["activityTarget"] = {key: expected.get(key) for key in (
                "conversationId", "requestActivityId", "replyActivityId", "widgetActivityId", "turn",
            )}
            evidence["observedReplyIdentities"] = [
                {key: item.get(key) for key in (
                    "requestActivityId", "activityId", "nativeRunId", "traceId",
                ) if item.get(key) is None or (
                    isinstance(item.get(key), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,160}", item[key])
                )}
                for item in gateway.get("events", []) if isinstance(item, dict)
                and item.get("conversationId") == expected["conversationId"]
                and item.get("activityId") == expected["replyActivityId"]
            ]
            if expected.get("turn") != args.widget_turn:
                raise ValueError("Widget collector mode differs from the actual browser target.")
            delivery = select_widget_turn(gateway, expected, time.time())
        else:
            delivery = select_delivery(gateway, expected, time.time())
        evidence["turn" if args.widget_turn else "delivery"] = delivery
        native = read_json(
            f"{args.runtime_url.rstrip('/')}/internal/runs/{quote(delivery['nativeRunId'], safe='')}/evidence",
            headers, "Runtime evidence",
        )
        evidence["native"] = validate_run(native, delivery["nativeRunId"], require_terminal=not args.widget_turn)
        query_sandbox = json.loads(args.query_sandbox.read_text()) if args.query_sandbox else None
        if not query_sandbox:
            evidence["queryNetwork"] = check("https://api.applicationinsights.io", transport_only=True)
        credential_type = AzureCliCredential if args.credential == "azure-cli" else ManagedIdentityCredential
        with credential_type() as credential:
            token = credential.get_token("https://management.azure.com/.default").token
            component = read_json(
                f"https://management.azure.com{args.app_insights_resource_id}?api-version=2020-02-02",
                {"Authorization": f"Bearer {token}"}, "Application Insights metadata",
            )
            app_id = str(uuid.UUID(component["properties"]["AppId"]))
            deadline = time.monotonic() + args.timeout_seconds
            while time.monotonic() < deadline:
                if query_sandbox:
                    remote = query_trace_in_sandbox(query_sandbox, app_id, delivery["traceId"])
                    evidence["queryNetwork"] = remote["queryNetwork"]
                    evidence["queryExecution"] = remote["queryExecution"]
                    observed = remote["observed"]
                else:
                    observed = query_trace(credential, app_id, delivery["traceId"])
                if observed:
                    evidence["foundry"] = observed
                    break
                time.sleep(5)
            else:
                raise RuntimeError("Missing actual successful Foundry HTTP/served-model usage in this native trace.")
        evidence["status"] = "pass"
    except (AssertionError, ValueError, RuntimeError, AzureError) as error:
        evidence["errorType"] = type(error).__name__
        evidence["error"] = "Azure credential/query failed." if isinstance(error, AzureError) else str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    if evidence["status"] != "pass":
        raise SystemExit(f"Native evidence failed: {evidence.get('error', 'unverified')}")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
