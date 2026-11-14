from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid


AGENT_NAME = "step-09-foundry-agent-optimizer"
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


def validate_skills(user_id: str) -> dict[str, str]:
    support = invoke_responses(
        (
            "Use the support-style Agent Skill to write a complete customer-facing "
            "Contoso Outdoors reply for a customer whose return shipment is delayed. "
            "Keep it concise."
        ),
        user_id,
    )
    require_contains(support, "STYLE-CANARY-3318", "support-style skill")

    escalation = invoke_responses(
        (
            "A Contoso Outdoors customer reports that a product injured them. "
            "Classify the escalation and give the one-sentence internal response."
        ),
        user_id,
    )
    require_contains(escalation, "urgent-escalation", "escalation classification")
    require_contains(escalation, "ESC-CANARY-7742", "escalation resource")

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
            "What is the Contoso Outdoors return policy for an unused backpack? "
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
        f"Remember that my Step 09 validation codename is {marker}.",
        user_id,
    )
    time.sleep(wait_seconds)
    recalled = invoke_responses(
        "What is my Step 09 validation codename? Answer only the codename.",
        user_id,
    )
    require_contains(recalled, marker, "cross-session Foundry Memory")
    return marker


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the deployed Step 09 Hosted Agent and optimizer baseline."
    )
    parser.add_argument("--memory-wait-seconds", type=int, default=35)
    parser.add_argument("--skip-memory", action="store_true")
    args = parser.parse_args()

    user_id = f"step09-deployed-smoke-{uuid.uuid4()}"
    responses = invoke_responses("Reply with exactly: step 09 responses ok", user_id)
    require_contains(responses, "step 09 responses ok", "Responses protocol")
    skills = validate_skills(user_id)
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
