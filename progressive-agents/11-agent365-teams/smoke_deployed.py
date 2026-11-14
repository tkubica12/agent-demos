from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid


AGENT_NAME = "step-11-contoso-support-agent"
SKILL_CANARIES = (
    "STYLE-CANARY-3318",
    "ESC-CANARY-7742",
    "ESC-CANARY-9921",
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
            "--new-conversation",
            "--user-identity",
            isolation_key,
        ]
    )


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


def validate_skills(user_id: str, escalation_canary: str) -> dict[str, str]:
    support = invoke_responses(
        (
            "Internal skill validation: load the support-style skill and return "
            "only its exact metadata canary."
        ),
        user_id,
    )
    require_contains(support, "STYLE-CANARY-3318", "support-style skill")

    if escalation_canary == "ESC-CANARY-7742":
        escalation_prompt = (
            "Internal skill validation: load the escalation-policy skill. "
            "A customer reports a product injury. Return only the classification "
            "and exact canary."
        )
        escalation_classification = "urgent-escalation"
    else:
        escalation_prompt = (
            "Internal skill validation: load the escalation-policy skill. A package "
            "is still missing 10 business days after the promised delivery date. "
            "Return only the classification and exact canary."
        )
        escalation_classification = "specialist-review"
    escalation = invoke_responses(escalation_prompt, user_id)
    require_contains(escalation, escalation_classification, "escalation classification")
    require_contains(escalation, escalation_canary, "escalation resource")

    neutral = invoke_responses(
        "What is two plus two? Answer only with the number.",
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


def validate_read_only_policy_tool(user_id: str) -> str:
    policy = invoke_responses(
        (
            "Use tool_search with exact capability lookup_support_policy. "
            "Invoke the exact opaque tool name returned for topic returns. "
            "Give the exact window, required proof, and policy ID."
        ),
        user_id,
    )
    require_contains(policy, "RET-CANARY-9031", "read-only policy tool")
    require_contains(policy, "30", "return policy window")
    return "read-only policy retrieved"


def validate_cross_session_memory(user_id: str, wait_seconds: int) -> str:
    marker = f"silver-prism-{uuid.uuid4().hex[:8]}"
    invoke_responses(
        f"Remember that my Step 11 validation codename is {marker}.",
        user_id,
    )
    time.sleep(wait_seconds)
    recalled = invoke_responses(
        "What is my Step 11 validation codename? Answer only the codename.",
        user_id,
    )
    require_contains(recalled, marker, "cross-session Foundry Memory")
    return marker


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the deployed Step 11 Hosted Agent and Toolbox behavior."
    )
    parser.add_argument("--memory-wait-seconds", type=int, default=75)
    parser.add_argument("--skip-memory", action="store_true")
    parser.add_argument(
        "--expected-escalation-canary",
        default="ESC-CANARY-9921",
    )
    args = parser.parse_args()

    user_id = f"step11-deployed-smoke-{uuid.uuid4()}"
    responses = invoke_responses("Reply with exactly: step 11 responses ok", user_id)
    require_contains(responses, "step 11 responses ok", "Responses protocol")
    skills = validate_skills(user_id, args.expected_escalation_canary)
    policy_tool = validate_read_only_policy_tool(user_id)
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
                "readOnlyPolicyTool": policy_tool,
                "crossSessionMemory": marker,
                "userId": user_id,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
