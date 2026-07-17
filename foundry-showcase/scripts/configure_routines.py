from __future__ import annotations

import argparse
import json
import time
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    InvokeAgentInvocationsApiRoutineAction,
    ScheduleRoutineTrigger,
    TimerRoutineTrigger,
)
from azure.identity import DefaultAzureCredential


DAILY_ROUTINE = "daily-support-quality-review"
FOLLOW_UP_ROUTINE = "case-follow-up-reminder"


def as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported SDK result type: {type(value).__name__}")


def wait_for_dispatch(
    client: AIProjectClient,
    routine_name: str,
    dispatch_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for run in client.beta.routines.list_runs(routine_name, limit=20):
            if run.dispatch_id != dispatch_id:
                continue
            value = as_dict(run)
            phase = str(value.get("phase", "")).lower()
            if phase in {"completed", "failed", "cancelled"}:
                if phase != "completed":
                    raise RuntimeError(
                        f"Routine {routine_name} dispatch {dispatch_id} "
                        f"finished in phase {phase}: {value.get('error_message')}"
                    )
                return value
        time.sleep(10)
    raise TimeoutError(
        f"Routine {routine_name} dispatch {dispatch_id} did not finish "
        f"within {timeout_seconds} seconds."
    )


def wait_for_timer_run(
    client: AIProjectClient,
    routine_name: str,
    known_run_ids: set[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for run in client.beta.routines.list_runs(routine_name, limit=20):
            if run.id in known_run_ids:
                continue
            value = as_dict(run)
            if value.get("attempt_source") != "timer_delivery":
                continue
            phase = str(value.get("phase", "")).lower()
            if phase in {"completed", "failed", "cancelled"}:
                if phase != "completed":
                    raise RuntimeError(
                        f"Routine {routine_name} timer run finished in phase "
                        f"{phase}: {value.get('error_message')}"
                    )
                return value
        time.sleep(10)
    raise TimeoutError(
        f"Routine {routine_name} did not fire within {timeout_seconds} seconds."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create and validate the Foundry Showcase Routines."
    )
    parser.add_argument("--project-endpoint", required=True)
    parser.add_argument("--agent-name", default="foundry-showcase-main")
    parser.add_argument("--daily-cron", default="0 7 * * 1-5")
    parser.add_argument("--timer-at", default="10m")
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--wait-for-timer", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    client = AIProjectClient(
        endpoint=args.project_endpoint,
        credential=DefaultAzureCredential(),
    )
    existing_names = {routine.name for routine in client.beta.routines.list()}
    if DAILY_ROUTINE in existing_names:
        client.beta.routines.delete(DAILY_ROUTINE)
    daily = client.beta.routines.create_or_update(
        routine_name=DAILY_ROUTINE,
        description="Read-only weekday support quality digest.",
        enabled=True,
        triggers={
            "weekday-morning": ScheduleRoutineTrigger(
                cron_expression=args.daily_cron,
                time_zone="UTC",
            )
        },
        action=InvokeAgentInvocationsApiRoutineAction(
            agent_name=args.agent_name,
            input={"action": "daily_support_quality_review"},
        ),
    )

    if FOLLOW_UP_ROUTINE in existing_names:
        client.beta.routines.delete(FOLLOW_UP_ROUTINE)
    timer = client.beta.routines.create_or_update(
        routine_name=FOLLOW_UP_ROUTINE,
        description="One-time read-only recommendation for CASE-1001.",
        enabled=True,
        triggers={
            "follow-up": TimerRoutineTrigger(
                at=args.timer_at,
            )
        },
        action=InvokeAgentInvocationsApiRoutineAction(
            agent_name=args.agent_name,
            input={
                "action": "case_follow_up_reminder",
                "caseId": "CASE-1001",
            },
        ),
    )

    output: dict[str, Any] = {
        "daily": as_dict(daily),
        "followUp": as_dict(timer),
    }
    known_timer_run_ids = {
        run.id
        for run in client.beta.routines.list_runs(FOLLOW_UP_ROUTINE, limit=20)
    }
    client.beta.routines.disable(DAILY_ROUTINE)
    output["dailyDisabled"] = as_dict(client.beta.routines.get(DAILY_ROUTINE))
    client.beta.routines.enable(DAILY_ROUTINE)
    output["dailyReEnabled"] = as_dict(client.beta.routines.get(DAILY_ROUTINE))

    if args.dispatch:
        dispatches = {}
        runs = {}
        for routine_name in (DAILY_ROUTINE, FOLLOW_UP_ROUTINE):
            dispatch = client.beta.routines.dispatch(routine_name)
            dispatches[routine_name] = as_dict(dispatch)
            runs[routine_name] = wait_for_dispatch(
                client,
                routine_name,
                dispatch.dispatch_id,
                args.timeout_seconds,
            )
        output["manualDispatches"] = dispatches
        output["manualRuns"] = runs

    if args.wait_for_timer:
        output["scheduledTimerRun"] = wait_for_timer_run(
            client,
            FOLLOW_UP_ROUTINE,
            known_timer_run_ids,
            args.timeout_seconds,
        )
        output["followUpAfterTimer"] = as_dict(
            client.beta.routines.get(FOLLOW_UP_ROUTINE)
        )
        client.beta.routines.disable(FOLLOW_UP_ROUTINE)
        output["followUpDisabled"] = as_dict(
            client.beta.routines.get(FOLLOW_UP_ROUTINE)
        )

    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
