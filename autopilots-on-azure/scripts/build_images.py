from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from scripts.setup_app_tfvars import runtime_app_tfvars_path
from scripts.tf_helpers import PLATFORM_DIR, REPO_ROOT, run, terraform_output, write_tfvars


def git_sha() -> str:
    result = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "nogit"


def acr_build(*, registry: str, repository: str, dockerfile: str, context: str, tag: str) -> str:
    result = run(
        [
            "az",
            "acr",
            "build",
            "--registry",
            registry,
            "--image",
            f"{repository}:{tag}",
            "--file",
            dockerfile,
            context,
            "--no-logs",
            "-o",
            "json",
        ],
        cwd=REPO_ROOT,
        capture=True,
    )
    payload = json.loads(result.stdout)
    images = payload.get("outputImages") or []
    for image in images:
        if image.get("repository") == repository and image.get("digest"):
            return image["digest"]
    raise RuntimeError(f"ACR build for {repository}:{tag} did not return an image digest.")


def write_worker_images(
    paths: list[Path],
    images: dict[str, str],
    *,
    runtime: str,
) -> None:
    workers: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        values = json.loads(path.read_text(encoding="utf-8"))
        if values.get("agent_runtime") != runtime:
            raise ValueError(f"{path} is not configured for runtime {runtime}.")
        workers.append((path, {**values, **images}))
    for path, values in workers:
        write_tfvars(path, values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Autopilots on Azure images in ACR and write apps generated image tfvars.")
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--state-name",
        action="append",
        default=[],
        help="Worker state to update; repeat to publish one image build to several Workers.",
    )
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    registry = platform["acr_name"]
    login_server = platform["acr_login_server"]
    tag = args.tag or f"dev-{int(time.time())}-{git_sha()}"

    runtime_repository = "hermes-runtime"
    runtime_dockerfile = "runtimes/hermes/Dockerfile"
    images = {
        "runtime_image": (
            runtime_repository,
            runtime_dockerfile,
            ".",
        ),
        "bridge_image": (
            "autopilot-bridge",
            "bridge/Dockerfile",
            ".",
        ),
        "private_mcp_image": (
            "private-incidents-mcp",
            "private-incidents-mcp/Dockerfile",
            ".",
        ),
        "public_shipments_mcp_image": (
            "public-shipments-mcp",
            "public-shipments-mcp/Dockerfile",
            ".",
        ),
    }

    states = list(dict.fromkeys(args.state_name or ["hermes"]))
    runtime_paths = [runtime_app_tfvars_path("hermes", state) for state in states]
    for path in runtime_paths:
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist. Run scripts.setup_app_tfvars first.")
    built_images: dict[str, str] = {}
    for var_name, (repository, dockerfile, context) in images.items():
        digest = acr_build(registry=registry, repository=repository, dockerfile=dockerfile, context=context, tag=tag)
        built_images[var_name] = f"{login_server}/{repository}@{digest}"
        built_images[var_name.removesuffix("_image") + "_disk_source_image"] = (
            f"{login_server}/{repository}:{tag}"
        )
        if var_name == "runtime_image":
            built_images["runtime_disk_image_name"] = f"hermes-runtime-{digest.removeprefix('sha256:')[:12]}"

    write_worker_images(runtime_paths, built_images, runtime="hermes")
    print(json.dumps({"tag": tag, "runtime": "hermes", "states": states, **built_images}, indent=2))


if __name__ == "__main__":
    main()
