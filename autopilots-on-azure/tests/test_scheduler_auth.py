import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from bridge.scheduler_auth import validate_scheduler_claims


class SchedulerAuthTests(unittest.TestCase):
    def test_scheduler_claims_require_allowed_identity_and_role(self):
        environment = {
            "SCHEDULED_LEARNING_ALLOWED_CLIENT_IDS": "client-1",
            "SCHEDULED_LEARNING_ALLOWED_OBJECT_IDS": "object-1",
        }
        with patch.dict(os.environ, environment, clear=False):
            claims = validate_scheduler_claims(
                {
                    "azp": "client-1",
                    "oid": "object-1",
                    "roles": ["ScheduledLearning.Run.All"],
                }
            )
            self.assertEqual(claims["oid"], "object-1")

            with self.assertRaises(HTTPException) as missing_role:
                validate_scheduler_claims(
                    {
                        "azp": "client-1",
                        "oid": "object-1",
                        "roles": [],
                    }
                )
            self.assertEqual(missing_role.exception.status_code, 403)

            with self.assertRaises(HTTPException) as wrong_client:
                validate_scheduler_claims(
                    {
                        "azp": "client-2",
                        "oid": "object-1",
                        "roles": ["ScheduledLearning.Run.All"],
                    }
                )
            self.assertEqual(wrong_client.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
