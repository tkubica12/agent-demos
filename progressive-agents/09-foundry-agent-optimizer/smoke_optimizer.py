from __future__ import annotations

import json
import os
from pathlib import Path

import yaml
from azure.ai.agentserver.optimization import load_skills_from_dir

from main import (
    load_optimization_configuration,
    lookup_support_policy,
    materialize_optimizer_skills,
)


ROOT = Path(__file__).parent
BASELINE = ROOT / ".agent_configs" / "baseline"
EXPECTED_SKILLS = {
    "escalation-policy",
    "profile-update-policy",
    "support-style",
}
READ_ONLY_TOOLS = {"lookup_support_policy"}


def load_jsonl(path: Path) -> list[dict]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise AssertionError(f"{path} must contain evaluation tasks.")
    for row in rows:
        if not row.get("name") or not row.get("query"):
            raise AssertionError(f"{path} contains a task without name and query.")
        criteria = row.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise AssertionError(f"{row['name']} must define testable criteria.")
    return rows


def validate_eval_config(path: Path, dataset_name: str) -> dict:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if config["agent"]["name"] != "step-09-foundry-agent-optimizer":
        raise AssertionError(f"{path} targets the wrong hosted agent.")
    if config["agent"]["config"] != ".agent_configs/baseline/metadata.yaml":
        raise AssertionError(f"{path} does not pin the versioned baseline.")
    if config["dataset"]["local_uri"] != f"datasets/{dataset_name}.jsonl":
        raise AssertionError(f"{path} references the wrong dataset.")
    if "builtin.task_adherence" not in config["evaluators"]:
        raise AssertionError(f"{path} must include task adherence.")
    custom_evaluator = next(
        evaluator
        for evaluator in config["evaluators"]
        if isinstance(evaluator, dict)
    )
    if custom_evaluator["name"] != "step09-optimizer-training":
        raise AssertionError(f"{path} references the wrong custom evaluator.")
    if custom_evaluator["version"] != "1":
        raise AssertionError(f"{path} must pin evaluator version 1.")
    return config


def main() -> None:
    version = (BASELINE / "VERSION").read_text(encoding="utf-8").strip()
    if version != "1.0.0":
        raise AssertionError(f"Unexpected baseline version: {version}")

    config = load_optimization_configuration()
    if not str(config.source).startswith("local:"):
        raise AssertionError(f"Expected local optimizer config, got {config.source}")
    expected_candidate_id = os.getenv("OPTIMIZATION_CANDIDATE_ID")
    if expected_candidate_id:
        if config.candidate_id != expected_candidate_id:
            raise AssertionError(
                f"Expected candidate {expected_candidate_id}, got {config.candidate_id}."
            )
        resolved_config_path = Path(str(config.source).removeprefix("local:")).resolve()
        if resolved_config_path.name != expected_candidate_id:
            raise AssertionError(
                f"Candidate {expected_candidate_id} resolved to {resolved_config_path}."
            )
    elif config.candidate_id is not None:
        raise AssertionError(f"Expected baseline config, got {config.candidate_id}.")

    skills = load_skills_from_dir(Path(config.skills_dir))
    skill_names = {skill.name for skill in skills}
    if skill_names != EXPECTED_SKILLS:
        raise AssertionError(f"Unexpected optimizer skills: {sorted(skill_names)}")
    candidate_marker = "OPTIMIZER-CANDIDATE-BODY-TEST"
    escalation_config = next(
        skill for skill in config.skills if skill.name == "escalation-policy"
    )
    escalation_config.body = f"{escalation_config.body}\n\n{candidate_marker}"
    runtime_skills = materialize_optimizer_skills(config)
    if runtime_skills is None:
        raise AssertionError("Optimizer skills were not materialized for MAF.")
    escalation_skill = runtime_skills / "escalation-policy" / "SKILL.md"
    escalation_resource = (
        runtime_skills
        / "escalation-policy"
        / "references"
        / "escalation-matrix.md"
    )
    materialized_body = escalation_skill.read_text(encoding="utf-8")
    if (
        not expected_candidate_id
        and "Read `references/escalation-matrix.md`" not in materialized_body
    ):
        raise AssertionError("Materialized candidate skill body is unavailable.")
    if candidate_marker not in materialized_body:
        raise AssertionError("Candidate skill changes did not reach the MAF skill tree.")
    if "ESC-CANARY-7742" not in escalation_resource.read_text(encoding="utf-8"):
        raise AssertionError("Materialized skills lost the checked-in resource.")

    tool_names = {
        definition["function"]["name"] for definition in config.tool_definitions
    }
    if tool_names != READ_ONLY_TOOLS:
        raise AssertionError(f"Optimizer tools must stay read-only: {sorted(tool_names)}")
    config.apply_tool_descriptions([lookup_support_policy])
    expected_description = next(
        definition["function"]["description"]
        for definition in config.tool_definitions
        if definition["function"]["name"] == "lookup_support_policy"
    )
    if lookup_support_policy.description != expected_description:
        raise AssertionError("Optimizer tool description was not applied.")

    returns = json.loads(lookup_support_policy(topic="returns"))
    shipping = json.loads(lookup_support_policy(topic="shipping"))
    if returns["policy"]["policy_id"] != "RET-CANARY-9031":
        raise AssertionError("Return policy lookup did not return checked-in data.")
    if shipping["policy"]["policy_id"] != "SHIP-CANARY-4186":
        raise AssertionError("Shipping policy lookup did not return checked-in data.")

    train_config = validate_eval_config(ROOT / "eval.yaml", "optimizer-train")
    validate_eval_config(ROOT / "eval.validation.yaml", "optimizer-validation")
    search_space = train_config["options"]["optimization_config"]["model_search_space"]
    if search_space != ["gpt-5.4-nano", "gpt-4.1"]:
        raise AssertionError(f"Unexpected model search space: {search_space}")
    if config.model not in {"gpt-5.4-mini", *search_space}:
        raise AssertionError(f"Unexpected optimizer model: {config.model}")
    if train_config["options"]["optimization_model"] != "gpt-5.4":
        raise AssertionError("The supported optimization model must be explicit.")

    train_rows = load_jsonl(ROOT / "datasets" / "optimizer-train.jsonl")
    validation_rows = load_jsonl(ROOT / "datasets" / "optimizer-validation.jsonl")
    train_names = {row["name"] for row in train_rows}
    validation_names = {row["name"] for row in validation_rows}
    if train_names & validation_names:
        raise AssertionError("Training and validation task names must be disjoint.")

    print(
        json.dumps(
            {
                "baselineVersion": version,
                "configSource": config.source,
                "model": config.model,
                "skills": sorted(skill_names),
                "readOnlyTools": sorted(tool_names),
                "trainingTasks": len(train_rows),
                "validationTasks": len(validation_rows),
                "modelSearchSpace": search_space,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
