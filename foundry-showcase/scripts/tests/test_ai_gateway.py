import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_gateway import require_non_destructive_plan, terraform_variables
from ai_gateway_runtime import (
    Response, budget_policies, decode_response, evidence_headers, require_rpc_result, require_tool_denial,
)
from ai_gateway_telemetry import trace_filter


class GatewayConfigurationTests(unittest.TestCase):
    def test_maps_non_secret_configuration_to_terraform(self):
        variables = terraform_variables({
            "azure": {"subscription_id": "subscription", "tenant_id": "tenant",
                      "resource_group_name": "showcase", "location": "swedencentral"},
            "gateway": {"name": "showcase-gateway", "publisher_name": "Demo",
                        "publisher_email": "demo@example.org"},
            "foundry": {"name": "showcase-models", "model_name": "gpt-5.4-mini",
                        "model_version": "2026-03-17", "model_capacity": 10},
        })
        self.assertEqual(variables["gateway_name"], "showcase-gateway")
        self.assertEqual(variables["foundry_name"], "showcase-models")
        self.assertEqual(variables["model_capacity"], 10)
        self.assertNotIn("api_key", variables)

    def test_rejects_both_replacement_orders_and_deletion(self):
        for actions in (["delete", "create"], ["create", "delete"], ["delete"]):
            with self.subTest(actions=actions), self.assertRaisesRegex(ValueError, "gateway"):
                require_non_destructive_plan({
                    "resource_changes": [{"address": "gateway", "change": {"actions": actions}}],
                })

    def test_accepts_in_place_updates_and_new_backend_resources(self):
        require_non_destructive_plan({
            "resource_changes": [
                {"address": "gateway", "change": {"actions": ["update"]}},
                {"address": "backend", "change": {"actions": ["create"]}},
            ],
        })

    def test_decodes_mcp_json_and_multiline_event_stream(self):
        expected = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        self.assertEqual(decode_response(
            '{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}', "application/json",
        ), expected)
        self.assertEqual(decode_response(
            'event: message\r\ndata: {"jsonrpc":"2.0",\r\ndata: "id":1,"result":{"tools":[]}}\r\n\r\n',
            "text/event-stream",
        ), expected)

    def test_rejects_missing_or_ambiguous_mcp_event_results(self):
        for body in ("event: ping\n\n", 'data: {"result":{}}\n\ndata: {"result":{}}\n\n'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                decode_response(body, "text/event-stream")

    def test_does_not_treat_rpc_tool_errors_as_success(self):
        for body in ({"error": {"code": -32601}}, {"result": {"isError": True}}):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                require_rpc_result(Response(200, {}, body))

    def test_evidence_excludes_authentication_and_session_headers(self):
        self.assertEqual(evidence_headers({
            "api-key": "never-print", "authorization": "never-print",
            "set-cookie": "never-print", "mcp-session-id": "never-print",
            "retry-after": "60", "x-budget-remaining": "0.04",
        }), {"retry-after": "60", "x-budget-remaining": "0.04"})

    def test_budget_probe_preserves_unrelated_policy_and_override_changes(self):
        policies = [
            {"type": "tokenLimit", "count": 5000},
            {"type": "costLimit", "id": "showcase-daily-budget", "counterKey": "Identity", "amount": 0.05,
             "overrides": [{"apiKeyResourceId": "/other-key", "amount": 0.02}]},
        ]
        changed = budget_policies(policies, "/probe-key", 0.000001)
        self.assertEqual(len(policies[1]["overrides"]), 1)
        self.assertEqual(changed[0], policies[0])
        changed[1]["amount"] = 0.04
        restored = budget_policies(changed, "/probe-key", None)
        self.assertEqual(restored[1]["amount"], 0.04)
        self.assertEqual(restored[1]["overrides"], policies[1]["overrides"])

    def test_removes_empty_probe_overrides_without_resetting_normal_budget(self):
        baseline = [{"type": "costLimit", "id": "showcase-daily-budget", "counterKey": "Identity", "amount": 0.05}]
        self.assertEqual(budget_policies(budget_policies(baseline, "/probe", 0.000001), "/probe", None), baseline)

    def test_distinguishes_mcp_block_from_unpublished_http_rejection(self):
        require_tool_denial(Response(200, {}, {"result": {
            "isError": True, "_meta": {"com.microsoft.azure.ai.gateway/denial": {"code": "ToolNotAvailable"}},
        }}), blocked=True)
        require_tool_denial(Response(404, {}, {"statusCode": 404}), blocked=False)
        with self.assertRaises(RuntimeError):
            require_tool_denial(Response(200, {}, {"result": {"content": []}}), blocked=False)
        with self.assertRaises(RuntimeError):
            require_tool_denial(Response(200, {}, {"result": {"isError": True}}), blocked=True)

    def test_telemetry_trace_ids_cannot_inject_kql(self):
        self.assertEqual(trace_filter(None), "")
        self.assertIn("a" * 32, trace_filter("A" * 32))
        for invalid in ('" | take 1', "g" * 32, "a" * 31):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                trace_filter(invalid)


if __name__ == "__main__":
    unittest.main()
