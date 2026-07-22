from __future__ import annotations

import json
import os
import time
from typing import Any, Callable

import httpx
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential


RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def retry_delay(response: httpx.Response, attempt: int, base_seconds: int) -> int:
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after.isdigit():
        return min(int(retry_after), 3_600)
    return min(base_seconds * (2**attempt), 3_600)


def run_scheduled_learning_job(
    *,
    credential: TokenCredential,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    bridge_url = required_env("SCHEDULED_LEARNING_BRIDGE_URL").rstrip("/")
    audience = required_env("SCHEDULED_LEARNING_AUDIENCE")
    retry_limit = int(os.getenv("SCHEDULED_LEARNING_JOB_RETRY_LIMIT", "3"))
    backoff_seconds = int(os.getenv("SCHEDULED_LEARNING_JOB_RETRY_BACKOFF_SECONDS", "30"))
    timeout_seconds = int(os.getenv("SCHEDULED_LEARNING_JOB_TIMEOUT_SECONDS", "1800"))
    token = credential.get_token(f"{audience}/.default").token

    with client_factory(timeout=timeout_seconds) as client:
        for attempt in range(retry_limit + 1):
            try:
                response = client.post(
                    f"{bridge_url}/internal/scheduled-learning/run-managed",
                    headers={"Authorization": f"Bearer {token}"},
                    json={},
                )
            except httpx.TransportError:
                if attempt >= retry_limit:
                    raise
                sleep(min(backoff_seconds * (2**attempt), 3_600))
                continue
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < retry_limit:
                sleep(retry_delay(response, attempt, backoff_seconds))
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Scheduled learning bridge returned a non-object response.")
            return payload
    raise RuntimeError("Scheduled learning Job exhausted retries.")


def main() -> None:
    result = run_scheduled_learning_job(credential=DefaultAzureCredential())
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
