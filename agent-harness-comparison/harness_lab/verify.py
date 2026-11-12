from __future__ import annotations

import hashlib
import json
from collections import Counter

from .core import ROOT, VARIANTS, Budget, config, write_json
from .experiment import CRITERIA, fingerprint, load_state
from .reporting import aggregate, metrics_for_records


def cooldown_gaps(recorded_jobs: dict[str, tuple[float, float]]) -> list[float]:
    """Compare Unix completion/start timestamps, not perf-counter durations."""
    ordered = sorted(recorded_jobs.values())
    return [right[0] - left[1] for left, right in zip(ordered, ordered[1:])]


def verify() -> dict:
    state = load_state()
    summary = aggregate()
    records = summary["records"]
    failures = []

    def check(name: str, passed: bool) -> None:
        if not passed:
            failures.append(name)

    check("144 recorded attempts", len(records) == 144 and all(record["run_id"] for record in records))
    check("144 unique run IDs", len({record["run_id"] for record in records}) == 144)
    check("36 pairs", len(state["pairs"]) == 36 and len({pair["id"] for pair in state["pairs"]}) == 36)
    check("12 briefs x 12 cells", sorted(Counter(record["brief"] for record in records).values()) == [12] * 12)
    protocols = state.get("protocol_history", [])
    check("primary cohort is serial", state.get("primary_phase") == "serial")
    check("serial scheduler", state.get("workers") == 1 and state.get("cooldown_seconds") == 60)
    check("unique protocol phases", len({phase["id"] for phase in protocols}) == len(protocols))
    initial = next((phase for phase in protocols if phase["id"] == "parallel_quota"), None)
    if initial:
        initial_records = [record for record in records if record["measurement_phase"] == "parallel_quota"]
        check("32 original quota-phase attempts", len(initial_records) == 32)
        check("original attempts never replaced", {record["run_id"] for record in initial_records} == set(initial["original_run_ids"]))
        check("112 serial attempts", sum(record["measurement_phase"] == "serial" for record in records) == 112)
    for protocol in protocols:
        selected = [record for record in records if record.get("measurement_phase") == protocol["id"]]
        check(f"{protocol['id']}: planned coverage", len(selected) == protocol["planned_attempts"])
        for snapshot in [protocol, *protocol.get("scheduler_revisions", [])]:
            check(
                f"{protocol['id']}: native inputs unchanged",
                {name: value for name, value in snapshot["fingerprint"].items() if name != "harness_lab\\experiment.py"}
                == {name: value for name, value in state["fingerprint"].items() if name != "harness_lab\\experiment.py"},
            )
    serial_records = [record for record in records if record.get("measurement_phase") == "serial"]
    check("primary metrics exclude quota phase", summary["metrics"] == metrics_for_records(serial_records))
    for pair in state["pairs"]:
        check(f"{pair['id']}: four variants", Counter(cell["variant"] for cell in pair["cells"]) == Counter(VARIANTS))
        check(f"{pair['id']}: unmixed phase", len({cell.get("measurement_phase") for cell in pair["cells"]}) == 1)
    for record in records:
        label = record["run_id"] or f"{record['pair']}/{record['variant']}"
        check(f"{label}: terminal", record["status"] in {"passed", "failed", "interrupted"})
        check(f"{label}: judgment", record.get("quality") is not None)
        if record.get("quality"):
            scores = record["quality"]["criteria"]
            check(f"{label}: rubric", set(scores) == set(CRITERIA) and all(0 <= score <= 4 for score in scores.values()))
            if record["status"] != "passed":
                check(f"{label}: failed-attempt zero", not any(scores.values()))
        if record["status"] == "passed":
            check(f"{label}: deterministic artifacts", record.get("artifact_check", {}).get("passed", False))
            check(f"{label}: actual usage", record.get("tokens") is not None)
        if record.get("tokens"):
            tokens = record["tokens"]
            check(f"{label}: token normalization", 0 <= tokens["cached_tokens"] + tokens["cache_write_tokens"] <= tokens["input_tokens"])
        if record.get("model_estimated_usd") is not None:
            check(f"{label}: nonnegative cost", record["model_estimated_usd"] >= 0)
    budget = Budget()
    with budget.connect() as db:
        counts = dict(db.execute("SELECT variant,COUNT(*) FROM runs WHERE status='passed' GROUP BY variant").fetchall())
        running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
        ledger = {row[0]: row[1:] for row in db.execute("SELECT id,variant,phase,started FROM runs")}
    serial_jobs = {}
    for record in records:
        label = record["run_id"]
        check(f"{label}: ledger entry", label in ledger)
        if label in ledger:
            check(f"{label}: benchmark identity", ledger[label][:2] == (record["variant"], "benchmark"))
        if record.get("measurement_phase") == "serial" and label:
            directory = ROOT / "runs" / label
            events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            markers = [event for event in events if event["kind"] == "experiment_cell"]
            check(
                f"{label}: serial provenance",
                len(markers) == 1 and markers[0].get("measurement_phase") == "serial"
                and markers[0].get("workers") == 1 and markers[0].get("cooldown_seconds") == 60,
            )
            if label in ledger and record.get("elapsed_seconds") is not None:
                serial_jobs[label] = (ledger[label][2], (directory / "result.json").stat().st_mtime)
    for pair in state["pairs"]:
        destination = ROOT / "runs" / "evaluations" / f"{pair['id']}.json"
        check(f"{pair['id']}: persisted evaluator", destination.exists())
        if not destination.exists():
            continue
        evaluation = json.loads(destination.read_text(encoding="utf-8"))
        check(f"{pair['id']}: anonymous labels", set(evaluation["mapping"]) == set("ABCD"))
        check(f"{pair['id']}: four verdicts", Counter(item["label"] for item in evaluation["judgments"]) == Counter("ABCD"))
        check(f"{pair['id']}: evaluator mapping", {
            item["run_id"] for item in evaluation["mapping"].values()
        } == {cell["run_id"] for cell in pair["cells"]})
        evaluator = evaluation["evaluator"]
        check(f"{pair['id']}: evaluator succeeded", evaluator["status"] == "passed")
        label = evaluator["run_id"]
        check(f"{pair['id']}: actual evaluator ledger", label in ledger and ledger[label][:2] == ("judge", "evaluation"))
        if label in ledger:
            serial_jobs[label] = (ledger[label][2], (ROOT / "runs" / label / "result.json").stat().st_mtime)
    gaps = cooldown_gaps(serial_jobs)
    check("serial research and judges observe cooldown", bool(gaps) and min(gaps) >= 60)
    check("four real native success paths", all(counts.get(variant, 0) > 0 for variant in VARIANTS))
    check("no unclosed ledger operations", running == 0)
    check("operation cap", summary["operations"] <= config()["limits"]["max_runs"])
    check("spending envelope", summary["conservative_spending_envelope_usd"] <= config()["limits"]["max_estimated_usd"])
    check("WebIQ cap", summary["webiq_calls"] <= config()["webiq"]["max_calls"])
    check("frozen inputs unchanged", state["fingerprint"] == fingerprint())
    write_json(ROOT / "results" / "timing.json", {
        "clock": "Unix time",
        "completion_source": "Unmodified result.json filesystem last-write time",
        "jobs": [{
            "run_id": label, "started": start, "result_written": completed,
            "result_sha256": hashlib.sha256((ROOT / "runs" / label / "result.json").read_bytes()).hexdigest(),
        } for label, (start, completed) in sorted(serial_jobs.items(), key=lambda item: item[1][0])],
    })
    result = {
        "status": "passed" if not failures else "failed", "failures": failures,
        "attempts": summary["attempts"], "quality_coverage": summary["quality_coverage"],
        "operations": summary["operations"], "estimated_envelope_usd": summary["conservative_spending_envelope_usd"],
        "primary_phase": summary["primary_phase"], "primary_attempts": summary["primary_attempts"],
        "minimum_observed_model_job_gap_seconds": min(gaps) if gaps else None,
        "cooldown_clock": "Unix time: original result.json last-write time to next ledger start; no mixed-clock subtraction",
        "timing_evidence": "results\\timing.json",
    }
    write_json(ROOT / "results" / "checks.json", result)
    return result


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "passed" else 1)
