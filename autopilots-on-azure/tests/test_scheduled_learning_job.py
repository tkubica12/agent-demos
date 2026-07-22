import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from scripts.scheduled_learning_job import run_scheduled_learning_job


class FakeClient:
    def __init__(self, responses, **kwargs):
        self.responses = responses
        self.kwargs = kwargs
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


class ScheduledLearningJobTests(unittest.TestCase):
    def test_job_uses_managed_identity_and_retries_429(self):
        delays = []
        request = httpx.Request(
            "POST",
            "https://bridge.example/internal/scheduled-learning/run-managed",
        )
        responses = [
            httpx.Response(429, headers={"Retry-After": "4"}, request=request),
            httpx.Response(
                200,
                json={"workerId": "worker-1", "packet": None},
                request=request,
            ),
        ]
        client = FakeClient(responses, timeout=1)
        credential = SimpleNamespace(
            get_token=lambda scope: SimpleNamespace(token="managed-token")
        )
        environment = {
            "SCHEDULED_LEARNING_BRIDGE_URL": "https://bridge.example",
            "SCHEDULED_LEARNING_AUDIENCE": "api://scheduler-api",
            "SCHEDULED_LEARNING_JOB_RETRY_LIMIT": "2",
            "SCHEDULED_LEARNING_JOB_RETRY_BACKOFF_SECONDS": "3",
            "SCHEDULED_LEARNING_JOB_TIMEOUT_SECONDS": "600",
        }

        with patch.dict(os.environ, environment, clear=False):
            result = run_scheduled_learning_job(
                credential=credential,
                client_factory=lambda **kwargs: client,
                sleep=delays.append,
            )

        self.assertEqual(result["workerId"], "worker-1")
        self.assertEqual(delays, [4])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(
            client.calls[0]["headers"]["Authorization"],
            "Bearer managed-token",
        )


if __name__ == "__main__":
    unittest.main()
