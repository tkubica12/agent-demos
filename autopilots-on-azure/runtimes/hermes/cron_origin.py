"""Trusted conversation origin for native cron creation."""

from __future__ import annotations

import hashlib
import json
import os
from contextvars import copy_context
from pathlib import Path
from typing import Any, Callable

from cron_runtime import get_delivery_reference, json_transaction, read_json_object, upsert_delivery_reference


def register_origin(
    profile_home: Path, *, session_id: str, delivery_reference: dict[str, Any],
    canonical_session_id: str = "",
) -> dict[str, str]:
    if not session_id or len(session_id) > 256:
        raise ValueError("A native session ID is required.")
    reference = str(delivery_reference.get("referenceKey") or "")
    upsert_delivery_reference(
        profile_home, reference_key=reference,
        conversation=delivery_reference.get("conversation") or {},
        boundary=str(delivery_reference.get("boundary") or ""),
    )
    with json_transaction(profile_home / "local" / "cron-origins.json") as origins:
        keys = [
            hashlib.sha256(identifier.encode("utf-8")).hexdigest()
            for identifier in dict.fromkeys((session_id, canonical_session_id or session_id))
        ]
        if any(key in origins and origins[key] != reference for key in keys):
            raise ValueError("The native session already belongs to another delivery destination.")
        for key in keys:
            origins[key] = reference
    return {"referenceKey": reference}


def tool_execution(
    *, tool_name: str, args: dict[str, Any], next_call: Callable[..., Any],
    session_id: str = "", task_id: str = "", **_: Any,
) -> Any:
    if tool_name != "cronjob_manage":
        return next_call(args)
    scheduling_enabled = os.getenv("USER_SCHEDULING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if not scheduling_enabled or not all(
        os.getenv(name, "").strip()
        for name in ("SCHEDULER_SERVICEBUS_NAMESPACE", "SCHEDULER_SERVICEBUS_QUEUE")
    ):
        return json.dumps({
            "success": False,
            "error": (
                "Scheduling is unavailable in this deployment. It requires USER_SCHEDULING_ENABLED=true, "
                "SCHEDULER_SERVICEBUS_NAMESPACE and SCHEDULER_SERVICEBUS_QUEUE. "
                "Container-local scheduling is not supported."
            ),
        })
    home = Path(os.environ["HERMES_HOME"])
    origins = read_json_object(home / "local" / "cron-origins.json")
    reference = next((
        origins[hashlib.sha256(identifier.encode("utf-8")).hexdigest()]
        for identifier in dict.fromkeys((task_id, session_id))
        if identifier and hashlib.sha256(identifier.encode("utf-8")).hexdigest() in origins
    ), "")
    if not isinstance(reference, str) or not reference or not get_delivery_reference(home, reference):
        return json.dumps({"success": False, "error": "This turn has no trusted proactive delivery origin."})
    if args.get("script") or args.get("no_agent"):
        return json.dumps({"success": False, "error": "Hosted scheduled scripts are not allowed."})

    def invoke() -> Any:
        from gateway.session_context import set_session_vars

        set_session_vars(
            platform="autopilots", source="teams", chat_id=reference,
            session_id=session_id, async_delivery=True,
        )
        action = str(args.get("action") or "").strip().lower()
        return next_call({**args, "deliver": "local"} if action in {"create", "update"} else args)

    return copy_context().run(invoke)


def register(context: Any) -> None:
    context.register_middleware("tool_execution", tool_execution)
