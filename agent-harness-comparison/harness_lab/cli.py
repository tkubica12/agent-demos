from __future__ import annotations

import argparse
import asyncio
import json
import tomllib
import traceback

from .adapters import ADAPTERS
from .core import ROOT, VARIANTS, ResearchRun


def briefs() -> list[dict]:
    return tomllib.loads((ROOT / "workload.toml").read_text(encoding="utf-8"))["briefs"]


def error_leaves(error: BaseException) -> list[dict[str, str]]:
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in error_leaves(child)]
    return [{"type": type(error).__name__, "message": str(error)}]


async def execute(variant: str, brief: dict, phase: str = "smoke", *, on_started=None) -> dict:
    run = ResearchRun(variant, brief, phase)
    if on_started:
        on_started(run)
    try:
        async with asyncio.timeout(run.settings["limits"]["run_timeout_seconds"]):
            async with run.research_tools():
                run.log("agent_start")
                reply = await ADAPTERS[variant](run)
                run.log("agent_end")
        details = {"final_reply": reply}
        if not run.published:
            details.update(error_type="PublicationMissing", error="Harness stopped without validated HTML/PDF delivery.")
            run.log("failure", **details)
        result = run.complete("passed" if run.published else "failed", **details)
    except Exception as error:
        leaves = error_leaves(error)
        run.log(
            "failure", error_type=type(error).__name__, error=str(error),
            causes=leaves,
            traceback="".join(traceback.format_exception(error)),
        )
        result = run.complete(
            "failed", error_type=leaves[0]["type"], error=leaves[0]["message"], error_details=leaves,
        )
    print(json.dumps({
        key: result[key] for key in
        ("run_id", "variant", "status", "elapsed_seconds", "published", "model_estimated_usd")
    }), flush=True)
    if result.get("error"):
        print(result["error"], flush=True)
    if result["published"]:
        print(json.dumps({
            "html": str(run.directory / "report.standalone.html"),
            "pdf": str(run.directory / "report.pdf"),
        }), flush=True)
    return result


async def run_cli(arguments: argparse.Namespace) -> int:
    if arguments.command in {"demo", "smoke"}:
        selected = VARIANTS if arguments.variant == "all" else [arguments.variant]
        results = []
        for variant in selected:
            results.append(await execute(variant, briefs()[arguments.brief], "smoke"))
        return 0 if all(result["status"] == "passed" for result in results) else 1
    if arguments.command == "benchmark":
        from .experiment import benchmark
        return await benchmark()
    if arguments.command == "evaluate":
        from .experiment import evaluate
        return await evaluate()
    if arguments.command == "report":
        from .reporting import build_report
        return await build_report()
    raise ValueError(arguments.command)


def main() -> None:
    parser = argparse.ArgumentParser(description="One real research application, four native harnesses.")
    parser.add_argument(
        "command", nargs="?", default="demo",
        choices=["demo", "smoke", "benchmark", "evaluate", "report"],
    )
    parser.add_argument("--variant", default="all", choices=["all", *VARIANTS])
    parser.add_argument("--brief", default=0, type=int, choices=range(12))
    raise SystemExit(asyncio.run(run_cli(parser.parse_args())))


if __name__ == "__main__":
    main()
