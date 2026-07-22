import unittest

from scripts.setup_scheduled_learning_identity import (
    SCHEDULER_APP_ROLE,
    ensure_scheduler_api,
)


class FakeGraph:
    dry_run = False

    def __init__(self):
        self.calls = []

    def request(self, method, path, body=None, empty_ok=False):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("/applications?$filter="):
            return {"value": []}
        if method == "POST" and path == "/applications":
            return {
                "id": "app-object-1",
                "appId": "app-client-1",
                "appRoles": body["appRoles"],
            }
        if method == "GET" and path.startswith("/servicePrincipals?$filter="):
            return {"value": []}
        if method == "POST" and path == "/servicePrincipals":
            return {"id": "service-principal-1"}
        return {}


class ScheduledLearningIdentityTests(unittest.TestCase):
    def test_scheduler_api_exposes_application_role(self):
        graph = FakeGraph()

        state = ensure_scheduler_api(
            graph,
            {},
            display_name="Autopilots Scheduled Learning Bridge",
        )

        self.assertEqual(state["audience"], "api://app-client-1")
        self.assertEqual(state["servicePrincipalObjectId"], "service-principal-1")
        self.assertEqual(state["appRole"], SCHEDULER_APP_ROLE)
        patch = next(
            call
            for call in graph.calls
            if call[0] == "PATCH" and call[1] == "/applications/app-object-1"
        )
        self.assertEqual(
            patch[2]["identifierUris"],
            ["api://app-client-1"],
        )


if __name__ == "__main__":
    unittest.main()
