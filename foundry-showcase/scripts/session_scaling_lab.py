"""Measure how Foundry hosted agents allocate compute across sessions.

Hosted agents scale per session, not per replica. This lab drives the deployed
showcase agent with three real session strategies and reports which sandbox served
each request, how many requests overlapped inside a sandbox, and what that implies
for latency and cost.

Modes:
  shared    - every virtual user shares one ``agent_session_id``
  isolated  - every virtual user gets its own ``agent_session_id``
  pooled    - virtual users are packed into a bounded pool of sessions

Usage:
  uv run --project foundry-showcase/main-agent python foundry-showcase/scripts/session_scaling_lab.py \
    --invocations-url "<main-agent-invocations-url>" --users 8 --max-users-per-session 4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from azure.identity import DefaultAzureCredential

SCOPE = "https://ai.azure.com/.default"
MODES = ("shared", "isolated", "pooled")


@dataclass
class SessionPool:
    """Caller-owned mapping of virtual users onto a bounded set of sessions."""

    max_users_per_session: int
    prefix: str
    user_to_session: dict[str, str] = field(default_factory=dict)
    session_user_counts: dict[str, int] = field(default_factory=dict)

    def session_for(self, user_id: str) -> str:
        if user_id in self.user_to_session:
            return self.user_to_session[user_id]
        session_id = next(
            (
                candidate
                for candidate, count in self.session_user_counts.items()
                if count < self.max_users_per_session
            ),
            None,
        )
        if session_id is None:
            session_id = f"{self.prefix}-{len(self.session_user_counts):03d}"
            self.session_user_counts[session_id] = 0
        self.user_to_session[user_id] = session_id
        self.session_user_counts[session_id] += 1
        return session_id


@dataclass
class TurnResult:
    mode: str
    user_id: str
    session_id: str
    latency_seconds: float
    status_code: int
    instance_id: str | None
    requests_served: int | None
    max_in_flight: int | None
    throttle_retries: int = 0
    error: str | None = None


def session_url(base_url: str, session_id: str | None) -> str:
    parts = urlsplit(base_url)
    query = dict(pair.split("=", 1) for pair in parts.query.split("&") if "=" in pair)
    if session_id:
        query["agent_session_id"] = session_id
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


async def call_agent(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str | None,
    payload: dict[str, Any],
    token: str,
) -> tuple[int, dict[str, Any] | str, float]:
    started = time.perf_counter()
    response = await client.post(
        session_url(base_url, session_id),
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    elapsed = time.perf_counter() - started
    try:
        body: dict[str, Any] | str = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body, elapsed


async def run_turn(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    mode: str,
    user_id: str,
    session_id: str,
    message: str,
    max_throttle_retries: int = 4,
) -> TurnResult:
    payload = {
        "message": message,
        "user": {"id": user_id},
        "correlationId": f"lab-{mode}-{user_id}",
    }
    retries = 0
    elapsed = 0.0
    while True:
        status, body, attempt_elapsed = await call_agent(client, base_url, session_id, payload, token)
        elapsed += attempt_elapsed
        if status != 429 or retries >= max_throttle_retries:
            break
        retries += 1
        await asyncio.sleep(2**retries)
    sandbox = body.get("sandbox") if isinstance(body, dict) else None
    error = None
    if status >= 400:
        error = body if isinstance(body, str) else json.dumps(body)[:400]
    return TurnResult(
        mode=mode,
        user_id=user_id,
        session_id=session_id,
        latency_seconds=round(elapsed, 3),
        status_code=status,
        instance_id=(sandbox or {}).get("instanceId"),
        requests_served=(sandbox or {}).get("requestsServed"),
        max_in_flight=(sandbox or {}).get("maxInFlight"),
        throttle_retries=retries,
        error=error,
    )


async def probe_session(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    session_id: str,
    max_throttle_retries: int = 4,
) -> dict[str, Any]:
    retries = 0
    while True:
        status, body, elapsed = await call_agent(
            client,
            base_url,
            session_id,
            {"action": "runtime_probe"},
            token,
        )
        if status != 429 or retries >= max_throttle_retries:
            break
        retries += 1
        await asyncio.sleep(2**retries)
    if status >= 400 or not isinstance(body, dict):
        return {"sessionId": session_id, "status": status, "error": str(body)[:400]}
    sandbox = body.get("sandbox", {})
    sandbox["sessionId"] = session_id
    sandbox["probeLatencySeconds"] = round(elapsed, 3)
    sandbox["probeThrottleRetries"] = retries
    return sandbox


def sessions_base_url(invocations_url: str) -> str:
    """Derive the session management collection from the invocations endpoint."""
    parts = urlsplit(invocations_url)
    path = parts.path.split("/protocols/")[0]
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/sessions", parts.query, ""))


async def session_state(client: httpx.AsyncClient, sessions_url: str, token: str, session_id: str) -> str:
    response = await client.get(
        f"{sessions_url.split('?')[0]}/{session_id}?{urlsplit(sessions_url).query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code >= 400:
        return f"error:{response.status_code}"
    return str(response.json().get("status", "unknown"))


async def stop_session(client: httpx.AsyncClient, sessions_url: str, token: str, session_id: str) -> str:
    response = await client.post(
        f"{sessions_url.split('?')[0]}/{session_id}/stop?{urlsplit(sessions_url).query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 409:
        return "already_stopped"
    if response.status_code >= 400:
        return f"error:{response.status_code}"
    return "stopped"


def assign_sessions(mode: str, users: list[str], run_id: str, max_users_per_session: int) -> dict[str, str]:
    if mode == "shared":
        return {user: f"lab-{run_id}-shared" for user in users}
    if mode == "isolated":
        return {user: f"lab-{run_id}-{user}" for user in users}
    pool = SessionPool(max_users_per_session=max_users_per_session, prefix=f"lab-{run_id}-pool")
    return {user: pool.session_for(user) for user in users}


def summarize(mode: str, turns: list[TurnResult], probes: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = sorted(turn.latency_seconds for turn in turns if turn.error is None)
    sandboxes = {turn.instance_id for turn in turns if turn.instance_id}
    sessions = {turn.session_id for turn in turns}
    observed_max_in_flight = max(
        [turn.max_in_flight or 0 for turn in turns] + [probe.get("maxInFlight", 0) or 0 for probe in probes],
        default=0,
    )
    return {
        "mode": mode,
        "turns": len(turns),
        "failures": sum(1 for turn in turns if turn.error is not None),
        "modelThrottleRetries": sum(turn.throttle_retries for turn in turns),
        "sessions": len(sessions),
        "distinctSandboxes": len(sandboxes),
        "maxInFlightPerSandbox": observed_max_in_flight,
        "latencyP50": round(statistics.median(latencies), 3) if latencies else None,
        "latencyP95": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else None,
        "latencyMax": round(max(latencies), 3) if latencies else None,
        "wallClockSeconds": None,
    }


async def run_mode(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    mode: str,
    users: list[str],
    run_id: str,
    max_users_per_session: int,
    message: str,
) -> tuple[dict[str, Any], list[TurnResult], list[dict[str, Any]]]:
    assignments = assign_sessions(mode, users, run_id, max_users_per_session)
    started = time.perf_counter()
    turns = await asyncio.gather(
        *(
            run_turn(client, base_url, token, mode, user, assignments[user], message)
            for user in users
        )
    )
    wall_clock = time.perf_counter() - started
    probes = await asyncio.gather(
        *(probe_session(client, base_url, token, session) for session in sorted(set(assignments.values())))
    )
    summary = summarize(mode, list(turns), list(probes))
    summary["wallClockSeconds"] = round(wall_clock, 3)
    return summary, list(turns), list(probes)


async def release_sessions(
    client: httpx.AsyncClient,
    sessions_url: str,
    token: str,
    session_ids: list[str],
) -> list[dict[str, str]]:
    """Stop sessions immediately instead of paying the idle timeout tail."""
    results = []
    for session_id in session_ids:
        before = await session_state(client, sessions_url, token, session_id)
        outcome = await stop_session(client, sessions_url, token, session_id)
        after = await session_state(client, sessions_url, token, session_id)
        results.append({"sessionId": session_id, "before": before, "stop": outcome, "after": after})
    return results


def cost_note(summary: dict[str, Any], cpu: float, memory_gib: float, idle_seconds: float) -> str:
    sandboxes = summary["distinctSandboxes"] or summary["sessions"]
    active = summary["wallClockSeconds"] or 0.0
    billed_minutes = sandboxes * (active + idle_seconds) / 60
    released_minutes = sandboxes * active / 60
    return (
        f"{sandboxes} active sandbox(es) x {cpu} vCPU / {memory_gib} GiB. "
        f"With the {idle_seconds / 60:.0f}-minute idle timeout this burst bills roughly "
        f"{billed_minutes:.1f} sandbox-minutes ({billed_minutes * cpu:.1f} vCPU-minutes); "
        f"stopping the sessions right away cuts that to about {released_minutes:.1f} sandbox-minutes."
    )


async def main_async(args: argparse.Namespace) -> int:
    credential = DefaultAzureCredential()
    token = credential.get_token(SCOPE).token
    run_id = args.run_id or uuid.uuid4().hex[:8]
    users = [f"user{index:03d}" for index in range(args.users)]
    modes = MODES if args.mode == "all" else (args.mode,)

    report: dict[str, Any] = {
        "runId": run_id,
        "startedAt": datetime.now(UTC).isoformat(),
        "invocationsUrl": args.invocations_url,
        "users": args.users,
        "maxUsersPerSession": args.max_users_per_session,
        "modes": [],
    }

    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for mode in modes:
            print(f"\n=== mode: {mode} ===")
            summary, turns, probes = await run_mode(
                client,
                args.invocations_url,
                token,
                mode,
                users,
                run_id,
                args.max_users_per_session,
                args.message,
            )
            print(json.dumps(summary, indent=2))
            print(cost_note(summary, args.cpu, args.memory_gib, args.idle_seconds))
            for probe in probes:
                print(
                    f"  session={probe.get('sessionId')} instance={probe.get('instanceId')} "
                    f"served={probe.get('requestsServed')} maxInFlight={probe.get('maxInFlight')} "
                    f"uptime={probe.get('processUptimeSeconds')}s "
                    f"resumeCount={(probe.get('sessionMarker') or {}).get('resumeCount')}"
                )
            report["modes"].append(
                {
                    "summary": summary,
                    "turns": [turn.__dict__ for turn in turns],
                    "probes": probes,
                }
            )
            if args.stop_when_done:
                released = await release_sessions(
                    client,
                    sessions_base_url(args.invocations_url),
                    token,
                    sorted({turn.session_id for turn in turns}),
                )
                report["modes"][-1]["released"] = released
                states = ", ".join(f"{item['sessionId']}:{item['before']}->{item['after']}" for item in released)
                print(f"  released: {states}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nWrote {out_path}")

    print("\n=== comparison ===")
    header = f"{'mode':<10}{'sessions':>10}{'sandboxes':>11}{'maxInFlight':>13}{'p50':>8}{'p95':>8}{'wall':>8}{'429s':>7}"
    print(header)
    for entry in report["modes"]:
        summary = entry["summary"]
        print(
            f"{summary['mode']:<10}{summary['sessions']:>10}{summary['distinctSandboxes']:>11}"
            f"{summary['maxInFlightPerSandbox']:>13}{summary['latencyP50'] or 0:>8}"
            f"{summary['latencyP95'] or 0:>8}{summary['wallClockSeconds']:>8}"
            f"{summary['modelThrottleRetries']:>7}"
        )
    failures = sum(entry["summary"]["failures"] for entry in report["modes"])
    return 1 if failures else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invocations-url", required=True)
    parser.add_argument("--mode", choices=(*MODES, "all"), default="all")
    parser.add_argument("--users", type=int, default=6)
    parser.add_argument("--max-users-per-session", type=int, default=3)
    parser.add_argument(
        "--run-id",
        default=None,
        help="Reuse a previous run id to hit the same session ids and observe warm resume.",
    )
    parser.add_argument("--message", default="In one short sentence, what does this agent do?")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--cpu", type=float, default=0.5)
    parser.add_argument("--memory-gib", type=float, default=1.0)
    parser.add_argument("--idle-seconds", type=float, default=900.0)
    parser.add_argument(
        "--stop-when-done",
        action="store_true",
        help="Stop every session the run created so compute is released immediately.",
    )
    parser.add_argument("--out", default=None)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
