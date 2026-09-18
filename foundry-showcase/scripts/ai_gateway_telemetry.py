from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlparse

from ai_gateway_runtime import Gateway, azure_token, request


def trace_filter(trace_id: str | None) -> str:
    if trace_id is None:
        return ""
    if len(trace_id) != 32 or any(char not in "0123456789abcdefABCDEF" for char in trace_id):
        raise ValueError("A trace ID must contain exactly 32 hexadecimal characters.")
    return f' and TraceId == "{trace_id.lower()}"'


def query_rows(workspace_id: str, token: str, query: str) -> list[dict[str, Any]]:
    result = request(
        "POST", f"https://api.loganalytics.azure.com/v1/workspaces/{workspace_id}/query",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        {"query": query},
    ).require(200).body
    if result.get("error"):
        raise RuntimeError(f"Log Analytics returned an incomplete result: {result['error']}")
    table = result["tables"][0]
    columns = [column["name"] for column in table["columns"]]
    return [dict(zip(columns, row, strict=True)) for row in table["rows"]]


def metric_sample(endpoint: str, token: str, name: str) -> dict[str, Any]:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".prometheus.monitor.azure.com"):
        raise ValueError("Refusing to send a monitoring token to an unexpected endpoint.")
    query = urlencode({"query": f'sum(last_over_time({{__name__="{name}"}}[30m]))'})
    response = request("GET", f"{endpoint.rstrip('/')}/api/v1/query?{query}",
                       {"Authorization": f"Bearer {token}"}).require(200).body
    if response.get("status") != "success":
        raise RuntimeError(f"Prometheus rejected the metric query: {response.get('error')}")
    result = response["data"]["result"]
    if not result:
        raise RuntimeError(f"No recent samples for {name}; allow time for native metric ingestion.")
    sample = result[0]
    if "value" in sample:
        observed = {"timestamp": sample["value"][0], "value": sample["value"][1]}
    elif "histogram" in sample:
        timestamp, histogram = sample["histogram"]
        observed = {"timestamp": timestamp, "count": histogram["count"], "sum": histogram["sum"]}
    else:
        raise RuntimeError(f"Unsupported Prometheus sample shape for {name}.")
    return {"name": name, "recent_sample_aggregate": observed}


def telemetry(gateway: Gateway, trace_id: str | None = None) -> dict[str, Any]:
    selected_trace = trace_filter(trace_id)
    scope = gateway.resource_id.rsplit("/providers/", 1)[0]
    gateway_name = gateway.resource_id.rsplit("/", 1)[1]
    insights_id = f"{scope}/providers/Microsoft.Insights/components/appi-{gateway_name}"
    insights = gateway.arm("GET", insights_id, api_version="2020-02-02").require(200).body["properties"]
    dcr_id = insights["DataCollectionRuleResourceId"]
    dcr = gateway.arm("GET", dcr_id, api_version="2023-03-11").require(200).body["properties"]
    destinations = dcr["destinations"]
    workspace_id = destinations["logAnalytics"][0]["workspaceId"]
    monitor_id = destinations["monitoringAccounts"][0]["accountResourceId"]
    monitor = gateway.arm("GET", monitor_id, api_version="2023-04-03").require(200).body["properties"]
    log_token = azure_token(gateway.subscription_id, "https://api.loganalytics.io")
    spans = query_rows(workspace_id, log_token, (
        f'OTelSpans | where TimeGenerated > ago(30m) and ServiceName == "apim-gateway"{selected_trace}'
        ' | summarize Spans=count(), Failures=countif(StatusCode == "Error") by Name'
        ' | top 30 by Spans desc'
    ))
    logs = query_rows(workspace_id, log_token, (
        f'OTelLogs | where TimeGenerated > ago(30m) and ServiceName == "apim-gateway"{selected_trace}'
        ' | summarize Records=count() by SeverityText'
    ))
    if not spans or not logs:
        raise RuntimeError("No matching gateway logs or spans yet; allow a few minutes for OTLP ingestion.")
    metric_token = azure_token(gateway.subscription_id, "https://prometheus.monitor.azure.com")
    metrics = [
        metric_sample(monitor["metrics"]["prometheusQueryEndpoint"], metric_token, name)
        for name in ("azure.ai_gateway.client.token.usage", "azure.ai_gateway.client.token.cost")
    ]
    return {
        "application_insights": insights_id,
        "log_workspace": destinations["logAnalytics"][0]["workspaceResourceId"],
        "trace_filter": trace_id,
        "window": "Last 30 minutes",
        "spans": spans,
        "logs": logs,
        "metrics": metrics,
        "metric_scope": "Gateway-wide recent samples, not trace-specific totals or an Azure bill.",
    }
