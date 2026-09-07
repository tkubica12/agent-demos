import asyncio
import copy
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from jsonschema import Draft202012Validator

from bridge import app as bridge_app
from tests import test_hermes_runtime as runtime_tests


class CollectiveLearningRouteTests(unittest.TestCase):
    def test_published_provenance_schema_matches_current_runtime_contract(self):
        schema_path = Path(__file__).resolve().parents[1] / "blueprints" / "junior-project-manager" / "schemas" / "skill-provenance.schema.json"
        schema = json.loads(schema_path.read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        artifact = "skills/candidates/deadline-verification"
        record = {
            **runtime_tests.HermesRuntimeTests()._provenance(artifact),
            "schemaVersion": "3.0", "recordId": "lr-example", "createdAt": "2026-09-06T00:00:00Z",
            "artifact": {"path": artifact, "beforeHash": None, "afterHash": "a" * 64,
                         "changedFiles": [artifact + "/SKILL.md"]},
            "roleRelease": {"roleBlueprint": "junior-project-manager", "release": "3.2.0", "commit": "b" * 40},
            "worker": {"workerId": "worker", "assignmentScope": "team"},
            "privacy": {"redactionStatus": "passed", "warnings": []},
        }
        validator.validate(record)
        for field, value in (("schemaVersion", "2.0"), ("action", "delete"), ("agentProposedScenarios", [])):
            with self.subTest(field=field):
                self.assertFalse(validator.is_valid({**record, field: value}))
        unsafe = copy.deepcopy(record)
        unsafe["agentProposedScenarios"][0]["command"] = "arbitrary executable command"
        self.assertFalse(validator.is_valid(unsafe))
        del record["agentProposedScenarios"]
        self.assertFalse(validator.is_valid(record))

    def test_operator_routes_forward_to_runtime_and_reject_missing_authentication(self):
        adapter = SimpleNamespace(
            runtime_kind="hermes",
            pending_collective_learning=AsyncMock(return_value={"packetDigest": "a" * 64}),
            prepare_refresh_rejection=AsyncMock(return_value={"dispositionDigest": "b" * 64}),
            reject_and_refresh=AsyncMock(return_value={"accepted": True}),
        )
        body = {"dispositionDigest": "b" * 64, "rejectedBy": "operator", "reason": "Not reusable"}

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge_app.app), base_url="http://bridge") as client:
                for method, path, payload in (
                    ("GET", "pending", None), ("POST", "prepare-rejection", {}),
                    ("POST", "reject", body),
                ):
                    url = "/internal/collective-learning/" + path
                    denied = await client.request(method, url, json=payload)
                    self.assertEqual(denied.status_code, 401)
                    allowed = await client.request(method, url, json=payload, headers={"X-Autopilot-Key": "operator-key"})
                    self.assertEqual(allowed.status_code, 200, allowed.text)
                adapter.reject_and_refresh.side_effect = ValueError("Disposition changed")
                conflict = await client.post("/internal/collective-learning/reject", json=body,
                                             headers={"X-Autopilot-Key": "operator-key"})
                self.assertEqual(conflict.status_code, 409)
                adapter.runtime_kind = "openclaw"
                unsupported = await client.get("/internal/collective-learning/pending",
                                               headers={"X-Autopilot-Key": "operator-key"})
                self.assertEqual(unsupported.status_code, 409)

        with patch.dict(os.environ, {"API_SERVER_KEY": "operator-key"}), patch.object(bridge_app, "runtime_adapter", return_value=adapter):
            asyncio.run(run())
        adapter.pending_collective_learning.assert_awaited_once()
        adapter.prepare_refresh_rejection.assert_awaited_once()
        self.assertEqual(adapter.reject_and_refresh.await_count, 2)
        adapter.reject_and_refresh.assert_awaited_with(
            disposition_digest=body["dispositionDigest"], rejected_by=body["rejectedBy"], reason=body["reason"],
        )
