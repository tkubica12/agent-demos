"""Run private-only acceptance from an in-VNet host; no Git metadata required."""

import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import tomllib
import uuid

from fixtures import create_fixtures
from network_probe import check


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    stacks = config["stacks"]
    if not stacks or len({stack["name"] for stack in stacks}) != len(stacks):
        raise ValueError("Configure distinct nonempty stacks.")
    output = Path(config["output_directory"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex
    fixture_codes = create_fixtures(output / "fixtures", nonce)
    evidence = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "results": []}
    for stack in stacks:
        if not os.environ.get(stack["access_code_env"]):
            raise ValueError(f"Missing access code environment variable for {stack['name']}.")
        if not os.environ.get(stack["evidence_key_env"]):
            raise ValueError(f"Missing private native evidence key environment variable for {stack['name']}.")
        if not isinstance(stack.get("app_insights_resource_id"), str):
            raise ValueError(f"Configure the authorized Application Insights resource for {stack['name']}.")
        if not isinstance(stack.get("owner_display_name"), str) or not stack["owner_display_name"].strip():
            raise ValueError(f"Configure the real DEMO Worker owner display name for {stack['name']}.")
        stack["worker_owner_id"] = str(uuid.UUID(stack["worker_owner_id"]))
        evidence["results"].append(check(stack["url"]))
        for field in ("gateway_url", "runtime_url"):
            evidence["results"].append(check(stack[field], transport_only=True))
    (output / "private-network.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    public = json.loads(Path(config["public_negative_evidence"]).read_text(encoding="utf-8"))
    if public.get("control", {}).get("status") != "pass":
        raise ValueError("Outside-VNet evidence must include a successful public connectivity control.")
    recorded_at = datetime.datetime.fromisoformat(public["at"])
    age = datetime.datetime.now(datetime.timezone.utc) - recorded_at
    if not datetime.timedelta(0) <= age <= datetime.timedelta(hours=24):
        raise ValueError("Public-negative evidence must be from the last 24 hours.")
    for stack in stacks:
        if not any(result.get("url") == stack["url"].rstrip("/") and result.get("status") == "pass"
                   and result.get("public_negative") is True for result in public["results"]):
            raise ValueError(f"Missing successful outside-VNet negative probe for {stack['name']}.")
    runtime = {
        "stacks": stacks, "output": str(output), "fixture_codes": fixture_codes,
        "timeout_ms": config.get("timeout_seconds", 180) * 1000,
    }
    runtime_path = output / "runtime.json"
    runtime_path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    environment = dict(os.environ)
    environment["PRIVATE_E2E_CONFIG"] = str(runtime_path)
    command = ["node", str(Path(__file__).with_name("acceptance.mjs"))]
    result = subprocess.run(command, env=environment, check=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
