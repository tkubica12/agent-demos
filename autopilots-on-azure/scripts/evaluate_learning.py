"""Local, response-only baseline/candidate evaluation using the real Hermes 0.19 CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import yaml

from runtimes.hermes.learning import (
    evaluate_scenario_response, validate_agent_proposed_scenarios, validate_governed_artifact,
)
from scripts.collective_review import load_approved_envelopes


SCHEMA_VERSION = "1.0"
GOVERNED_PREFIXES = ("skills/role/", "skills/candidates/")
LIMITATIONS = [
    "Literal response.text assertions only; no tool workflows, environment setup, or side-effect assertions.",
    "Governed skill bodies are explicitly included in the prompt; native skill discovery is not measured.",
    "Setup assumptions are supplied as text, not executed or independently verified.",
    "Independent-suite authorship is declared by the operator, not proven by this runner.",
    "One fresh conversation per case and arm; stochastic variance and broader quality are not measured.",
    "Private state stays in local isolated copies and normal model context; raw responses are not exported.",
    "Tools, hooks, plugins, MCP, external memory, fallback models, and background learning are disabled.",
]
PREFLIGHT = """
from importlib.metadata import version
if version("hermes-agent") != "0.19.0":
    raise RuntimeError("Hermes 0.19.0 is required")
from hermes_cli.config import load_config
from hermes_cli.tools_config import _get_platform_tools
from model_tools import get_tool_definitions
cfg = load_config()
if cfg["memory"]["nudge_interval"] != 0 or cfg["skills"]["creation_nudge_interval"] != 0:
    raise RuntimeError("Learning must be disabled")
tools = get_tool_definitions(enabled_toolsets=sorted(_get_platform_tools(cfg, "cli")), quiet_mode=True)
if tools:
    raise RuntimeError("The actual Hermes runtime enabled tools despite response-only configuration")
print("HERMES_EVALUATION_PREFLIGHT_OK")
"""


class EvaluationError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _json_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvaluationError("Evaluation inputs must contain JSON objects.")
    return value


def load_independent_suite(path: Path) -> dict[str, Any]:
    suite = _json_file(path)
    if set(suite) != {"schemaVersion", "suiteKind", "scenarios"} or suite["schemaVersion"] != SCHEMA_VERSION:
        raise EvaluationError("Independent suite requires schemaVersion 1.0, suiteKind, and scenarios.")
    if suite["suiteKind"] not in ("independent_regression", "independent_holdout"):
        raise EvaluationError("A trusted independent regression or holdout suite is mandatory.")
    validate_agent_proposed_scenarios(suite["scenarios"])
    return suite


def profile_files(home: Path) -> dict[str, bytes]:
    if not home.is_dir() or home.is_symlink() or (
        getattr(home.stat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise EvaluationError("Profiles must be ordinary directories, not links.")
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(home.rglob("*")):
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise EvaluationError("Evaluation profiles cannot contain symbolic links or junctions.")
        relative = path.relative_to(home).as_posix()
        if relative.split("/")[0] in ("learning", ".git"):
            continue
        if path.is_file():
            size = path.stat().st_size
            total += size
            if size > 10_000_000 or total > 50_000_000:
                raise EvaluationError("Use profile snapshots smaller than 50 MB, with files smaller than 10 MB.")
            files[relative] = path.read_bytes()
    if "config.yaml" not in files:
        raise EvaluationError("Each profile must provide config.yaml.")
    if ".env" in files and files[".env"].strip():
        raise EvaluationError("Use one shared authenticated process environment, not profile .env overrides.")
    return files


def compare_profiles(
    baseline: dict[str, bytes], candidate: dict[str, bytes],
) -> tuple[dict[str, Any], dict[str, Any]]:
    common = lambda files: {key: value for key, value in files.items() if not key.startswith(GOVERNED_PREFIXES)}
    if common(baseline) != common(candidate):
        raise EvaluationError("Baseline and candidate must have identical config and private state outside governed skills.")
    if baseline == candidate:
        raise EvaluationError("Baseline and candidate must differ in at least one governed skill.")
    if any(key.startswith(GOVERNED_PREFIXES) and key not in candidate for key in baseline):
        raise EvaluationError("Governed skill deletion is outside the supported learning contract.")
    for files in (baseline, candidate):
        governed = {key: value for key, value in files.items() if key.startswith(GOVERNED_PREFIXES)}
        artifacts = {key.rsplit("/", 1)[0] for key in governed}
        for artifact in artifacts:
            validate_governed_artifact(artifact, {
                key: value.decode("utf-8") for key, value in governed.items() if key.startswith(artifact + "/")
            })
    config = yaml.safe_load(baseline["config.yaml"])
    if not isinstance(config, dict):
        raise EvaluationError("config.yaml must contain a mapping.")
    model = config.get("model")
    if not isinstance(model, dict) or not all(
        isinstance(model.get(key), str) and model[key].strip() for key in ("default", "provider")
    ) or model["provider"] == "auto":
        raise EvaluationError("Both profiles must explicitly pin model.default and model.provider (not auto).")
    settings = json.loads(json.dumps(config))
    settings["platform_toolsets"] = {"cli": []}
    settings["plugins"] = {"enabled": []}
    settings["mcp_servers"] = {}
    settings["hooks"] = {}
    settings["fallback_model"] = None
    settings["fallback_models"] = []
    settings["memory"] = {
        **(settings.get("memory") or {}), "provider": "", "nudge_interval": 0, "write_approval": True,
    }
    settings["skills"] = {
        **(settings.get("skills") or {}), "external_dirs": [], "inline_shell": False,
        "creation_nudge_interval": 0, "write_approval": True,
    }
    settings["curator"] = {"enabled": False}
    settings["context"] = {"engine": "compressor"}
    settings["agent"] = {**(settings.get("agent") or {}), "coding_context": "off"}
    return settings, model


def signed_scenarios(
    packet_path: Path | None, public_keys_path: Path | None,
    baseline: dict[str, bytes], candidate: dict[str, bytes],
) -> tuple[list[dict[str, Any]], str | None]:
    if packet_path is None and public_keys_path is None:
        return [], None
    if packet_path is None or public_keys_path is None:
        raise EvaluationError("--approved-packet and --worker-public-keys must be provided together.")
    envelope = load_approved_envelopes(
        [packet_path], worker_public_keys=_json_file(public_keys_path),
    )[0]
    cases = []
    packet_paths: set[str] = set()
    for improvement in envelope["packet"]["improvements"]:
        packet_paths.update(improvement["files"])
        prefix = improvement["artifactPath"] + "/"
        actual_baseline = {
            key: hashlib.sha256(value).hexdigest() for key, value in baseline.items() if key.startswith(prefix)
        }
        if actual_baseline != improvement["baselineFileHashes"] or any(
            candidate.get(key) != value.encode("utf-8") for key, value in improvement["files"].items()
        ):
            raise EvaluationError("Operator profiles must match the signed packet's baseline and candidate artifacts.")
        for record in improvement["provenance"]:
            for scenario in record["agentProposedScenarios"]:
                cases.append({"scenario": scenario, "recordId": record["recordId"], "source": "agent_proposed"})
    changed_paths = {key for key in baseline.keys() | candidate.keys() if baseline.get(key) != candidate.get(key)}
    if changed_paths != packet_paths:
        raise EvaluationError("All compared skill changes must be covered by the supplied signed packet.")
    return cases, envelope["receipt"]["packetDigest"]


def isolated_profile(case_root: Path, files: dict[str, bytes], config: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    home = case_root / "profiles" / "evaluation"
    home.mkdir(parents=True)
    for relative, value in files.items():
        if relative.split("/")[0] in ("hooks", "plugins", "cron", "profiles", "evaluation-usage.json"):
            continue
        path = home.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (home / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("HERMES_", "MCP_", "HONCHO_", "PYTHONPATH", "PYTHONHOME"))
    }
    scratch = case_root / "scratch"
    scratch.mkdir()
    env.update({
        "HERMES_HOME": str(home), "HOME": str(case_root), "USERPROFILE": str(case_root),
        "AZURE_CONFIG_DIR": str(Path(os.environ.get("AZURE_CONFIG_DIR") or Path.home() / ".azure").resolve()),
        "TEMP": str(scratch), "TMP": str(scratch), "TMPDIR": str(scratch),
        "HERMES_DISABLE_LAZY_INSTALLS": "1", "HERMES_SKIP_NODE_BOOTSTRAP": "1",
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
    })
    return home, env


def scenario_prompt(files: dict[str, bytes], scenario: dict[str, Any]) -> str:
    parts = ["Apply these governed role skill instructions for this response-only evaluation:"]
    for key, value in sorted(files.items()):
        if key.startswith(GOVERNED_PREFIXES):
            parts.append(f"\n{key}\n{value.decode('utf-8')}")
    parts.extend((
        "\nScenario setup assumptions (provided, not executed):",
        "\n".join(scenario["setupAssumptions"]),
        "\nUser request:", scenario["input"],
    ))
    prompt = "\n".join(parts)
    if len(prompt.encode("utf-16-le")) > 28_000:
        raise EvaluationError("Combined skills and scenario exceed the bounded Hermes CLI prompt size.")
    return prompt


def run_response(
    python: Path, home: Path, env: dict[str, str], model: dict[str, Any], prompt: str, timeout: int,
) -> tuple[str, dict[str, Any]]:
    preflight = subprocess.run(
        [str(python), "-I", "-c", PREFLIGHT], cwd=home, env=env, text=True, encoding="utf-8",
        capture_output=True, timeout=timeout, check=False, shell=False,
    )
    if preflight.returncode or "HERMES_EVALUATION_PREFLIGHT_OK" not in preflight.stdout:
        raise EvaluationError("Actual Hermes 0.19 preflight failed; check installation and tool-free configuration.")
    usage_path = home / "evaluation-usage.json"
    result = subprocess.run(
        [str(python), "-I", "-m", "hermes_cli.main", "-z", prompt, "--model", model["default"],
         "--provider", model["provider"], "--usage-file", str(usage_path)],
        cwd=home, env=env, text=True, encoding="utf-8", capture_output=True,
        timeout=timeout, check=False, shell=False,
    )
    if result.returncode != 0 or not usage_path.is_file():
        raise EvaluationError("Actual Hermes execution failed or produced no usage receipt; no quality result.")
    usage = _json_file(usage_path)
    if (
        usage.get("completed") is not True or usage.get("failed") is not False
        or not isinstance(usage.get("api_calls"), int) or isinstance(usage["api_calls"], bool)
        or usage["api_calls"] < 1 or not result.stdout.strip()
        or usage.get("model") != model["default"] or usage.get("provider") != model["provider"]
    ):
        raise EvaluationError("Hermes did not complete with the pinned model/provider and an actual API call.")
    return result.stdout.rstrip("\r\n"), usage


def evaluate(
    *, baseline_profile: Path, candidate_profile: Path, independent_suite: Path,
    output: Path, hermes_python: Path, timeout: int = 180,
    approved_packet: Path | None = None, worker_public_keys: Path | None = None,
) -> dict[str, Any]:
    workspace = Path.cwd().resolve()
    output = output.resolve()
    baseline, candidate = profile_files(baseline_profile), profile_files(candidate_profile)
    baseline_profile, candidate_profile = baseline_profile.resolve(), candidate_profile.resolve()
    if not output.is_relative_to(workspace) or any(
        output.is_relative_to(profile) or profile.is_relative_to(output)
        for profile in (baseline_profile, candidate_profile)
    ) or output.exists():
        raise EvaluationError("Choose a new output file inside the workspace and outside both source profiles.")
    if timeout < 1 or timeout > 900:
        raise EvaluationError("Timeout must be between 1 and 900 seconds.")
    suite = load_independent_suite(independent_suite)
    config, model = compare_profiles(baseline, candidate)
    cases, packet_digest = signed_scenarios(approved_packet, worker_public_keys, baseline, candidate)
    author_inputs = {case["scenario"]["input"].strip().casefold() for case in cases}
    if any(case["input"].strip().casefold() in author_inputs for case in suite["scenarios"]):
        raise EvaluationError("Independent test inputs must not duplicate signed agent-proposed test inputs.")
    cases.extend({"scenario": case, "source": suite["suiteKind"]} for case in suite["scenarios"])
    report: dict[str, Any] = {
        "evaluationVersion": SCHEMA_VERSION, "status": "not_evaluated",
        "runtime": "hermes-agent==0.19.0", "model": model["default"], "provider": model["provider"],
        "independentSuiteDigest": _digest(suite), "independence": "operator_declared",
        "packetDigest": packet_digest, "limitations": LIMITATIONS, "cases": [],
        "profileDigests": {
            arm: _digest({key: hashlib.sha256(value).hexdigest() for key, value in files.items()})
            for arm, files in (("baseline", baseline), ("candidate", candidate))
        },
    }
    run_root = workspace / ".artifacts" / "learning-evaluation" / uuid.uuid4().hex
    if any(run_root.is_relative_to(profile) for profile in (baseline_profile, candidate_profile)):
        raise EvaluationError("Source profiles cannot contain the evaluation workspace.")
    run_root.mkdir(parents=True)
    try:
        for case in cases:
            row: dict[str, Any] = {
                "scenarioId": case["scenario"]["scenarioId"], "source": case["source"],
                "scenarioDigest": _digest(case["scenario"]), "recordId": case.get("recordId"),
            }
            report["cases"].append(row)
            for arm, files in (("baseline", baseline), ("candidate", candidate)):
                case_root = run_root / uuid.uuid4().hex
                home, env = isolated_profile(case_root, files, config)
                try:
                    response, usage = run_response(
                        hermes_python.resolve(), home, env, model, scenario_prompt(files, case["scenario"]), timeout,
                    )
                    score = evaluate_scenario_response(case["scenario"], response)
                    row[arm] = {
                        "passed": score["passed"],
                        "assertions": [item["passed"] for item in score["assertions"]],
                        "responseSha256": hashlib.sha256(response.encode("utf-8")).hexdigest(),
                        "responseCharacters": len(response),
                        "usage": {key: usage.get(key) for key in (
                            "api_calls", "input_tokens", "output_tokens", "estimated_cost_usd", "cost_status",
                        )},
                    }
                finally:
                    shutil.rmtree(case_root)
        if profile_files(baseline_profile) != baseline or profile_files(candidate_profile) != candidate:
            raise EvaluationError("A source profile changed during evaluation; comparison is invalid.")
        independent = [row for row in report["cases"] if row["source"] != "agent_proposed"]
        report["status"] = "evaluated"
        report["comparison"] = {
            "independentBaselinePassed": sum(row["baseline"]["passed"] for row in independent),
            "independentCandidatePassed": sum(row["candidate"]["passed"] for row in independent),
            "independentCaseCount": len(independent),
            "independentRegressions": sum(
                row["baseline"]["passed"] and not row["candidate"]["passed"] for row in independent
            ),
            "independentCandidateAllPassed": all(row["candidate"]["passed"] for row in independent),
        }
    except (EvaluationError, subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        report["status"] = "error"
        report["error"] = str(exc) if isinstance(exc, EvaluationError) else type(exc).__name__
        raise EvaluationError(report["error"]) from exc
    finally:
        shutil.rmtree(run_root)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Profiles must pin model.default/provider, share config/private state, and use shared process "
            "authentication (no .env overrides). Independent suite JSON: schemaVersion='1.0', "
            "suiteKind='independent_regression' or 'independent_holdout', scenarios=[declarative scenario objects "
            "with scenarioId, input, setupAssumptions, expectedObservableOutcomes, acceptanceCriteria, scope]. "
            "Only literal response.text assertions run; no tool workflow or skill-discovery coverage is claimed."
        ),
    )
    parser.add_argument("--baseline-profile", type=Path, required=True)
    parser.add_argument("--candidate-profile", type=Path, required=True)
    parser.add_argument("--independent-suite", type=Path, required=True,
                        help="Trusted operator-authored JSON regression/holdout suite; never agent-proposed cases.")
    parser.add_argument("--approved-packet", type=Path)
    parser.add_argument("--worker-public-keys", type=Path)
    parser.add_argument("--hermes-python", type=Path, default=Path(sys.executable),
                        help="Python interpreter with the actual pinned Hermes 0.19.0 runtime installed.")
    parser.add_argument("--output", type=Path, required=True, help="New local summary path; no raw private responses.")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    report = evaluate(**vars(args))
    print(json.dumps({"status": report["status"], "comparison": report["comparison"]}, sort_keys=True))
    if not report["comparison"]["independentCandidateAllPassed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
