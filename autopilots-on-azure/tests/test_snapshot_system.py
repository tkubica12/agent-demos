import unittest

from scripts.snapshot_system import redact, safe_name


class SnapshotSystemTests(unittest.TestCase):
    def test_redact_removes_secret_values_recursively(self):
        payload = {
            "clientSecret": "secret-value",
            "nested": {
                "accessToken": "token-value",
                "safe": "visible",
            },
            "items": [{"password": "hidden"}],
        }

        self.assertEqual(
            redact(payload),
            {
                "clientSecret": "<redacted>",
                "nested": {
                    "accessToken": "<redacted>",
                    "safe": "visible",
                },
                "items": [{"password": "<redacted>"}],
            },
        )

    def test_safe_name_replaces_path_hostile_characters(self):
        self.assertEqual(safe_name("app/name:with spaces"), "app_name_with_spaces")


if __name__ == "__main__":
    unittest.main()
