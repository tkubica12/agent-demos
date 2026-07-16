from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path


AGENT_NAME = "step-08-skill-system-baseline"
SKILL_CANARIES = (
    "STYLE-CANARY-3318",
    "ESC-CANARY-7742",
    "PROFILE-CANARY-2209",
)


def invoke_responses(prompt: str, isolation_key: str) -> str:
    return run_azd(
        [
            "ai",
            "agent",
            "invoke",
            AGENT_NAME,
            prompt,
            "--protocol",
            "responses",
            "--new-session",
            "--user-identity",
            isolation_key,
        ]
    )


def invoke_structured(payload: dict, user_id: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        encoding="utf-8",
        delete=False,
    ) as request_file:
        json.dump(payload, request_file)
        request_path = Path(request_file.name)

    try:
        return run_azd(
            [
                "ai",
                "agent",
                "invoke",
                AGENT_NAME,
                "--protocol",
                "invocations",
                "--new-session",
                "--user-identity",
                user_id,
                "--input-file",
                str(request_path),
            ]
        )
    finally:
        request_path.unlink(missing_ok=True)


def run_azd(arguments: list[str]) -> str:
    result = subprocess.run(
        ["azd", *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode:
        raise RuntimeError(f"azd exited with {result.returncode}:\n{output}")
    return output


def require_contains(output: str, expected: str, label: str) -> None:
    if expected.casefold() not in output.casefold():
        raise RuntimeError(f"{label} did not contain {expected!r}:\n{output}")


def validate_skills(user_id: str) -> dict[str, str]:
    support = invoke_structured(
        {
            "message": (
                "Write a complete customer-facing Contoso Outdoors reply for a customer "
                "whose return shipment is delayed. Keep it concise."
            ),
            "stream": False,
            "threadId": f"support-{uuid.uuid4()}",
            "user": {"id": user_id},
        },
        user_id,
    )
    require_contains(support, "STYLE-CANARY-3318", "support-style skill")

    escalation = invoke_structured(
        {
            "message": (
                "A Contoso Outdoors customer reports that a product injured them. "
                "Classify the escalation and give the one-sentence internal response."
            ),
            "stream": False,
            "threadId": f"escalation-{uuid.uuid4()}",
            "user": {"id": user_id},
        },
        user_id,
    )
    require_contains(escalation, "urgent-escalation", "escalation classification")
    require_contains(escalation, "ESC-CANARY-7742", "escalation resource")

    neutral = invoke_structured(
        {
            "message": "What is two plus two? Answer only with the number.",
            "stream": False,
            "threadId": f"neutral-{uuid.uuid4()}",
            "user": {"id": user_id},
        },
        user_id,
    )
    require_contains(neutral, "4", "neutral invocation")
    leaked = [canary for canary in SKILL_CANARIES if canary in neutral]
    if leaked:
        raise RuntimeError(f"Neutral request leaked unrelated skills {leaked}:\n{neutral}")

    return {
        "support": "support-style loaded",
        "escalation": "skill and resource loaded",
        "neutral": "unrelated skills not loaded",
    }


def validate_cross_session_memory(user_id: str, wait_seconds: int) -> str:
    marker = f"silver-prism-{uuid.uuid4().hex[:8]}"
    invoke_structured(
        {
            "message": f"Remember that my Step 08 validation codename is {marker}.",
            "stream": False,
            "threadId": f"memory-write-{uuid.uuid4()}",
            "user": {"id": user_id},
        },
        user_id,
    )
    time.sleep(wait_seconds)
    recalled = invoke_structured(
        {
            "message": "What is my Step 08 validation codename? Answer only the codename.",
            "stream": False,
            "threadId": f"memory-read-{uuid.uuid4()}",
            "user": {"id": user_id},
        },
        user_id,
    )
    require_contains(recalled, marker, "cross-session Foundry Memory")
    return marker


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the deployed Step 08 Hosted Agent and its added capability."
    )
    parser.add_argument("--memory-wait-seconds", type=int, default=35)
    parser.add_argument("--skip-memory", action="store_true")
    args = parser.parse_args()

    user_id = f"step08-deployed-smoke-{uuid.uuid4()}"
    responses = invoke_responses("Reply with exactly: step 08 responses ok", user_id)
    require_contains(responses, "step 08 responses ok", "Responses protocol")
    skills = validate_skills(user_id)
    marker = (
        "skipped"
        if args.skip_memory
        else validate_cross_session_memory(user_id, args.memory_wait_seconds)
    )
    print(
        json.dumps(
            {
                "responses": "ok",
                "skills": skills,
                "crossSessionMemory": marker,
                "userId": user_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
