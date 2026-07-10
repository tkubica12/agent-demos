from __future__ import annotations

import argparse
import json
import subprocess
import time

from scripts.setup_app_tfvars import runtime_app_tfvars_path
from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, output, run, terraform_output, write_tfvars


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Autopilots on Azure images in ACR and write apps generated image tfvars.")
    parser.add_argument("--tag", default="")
    parser.add_argument("--runtime", choices=["openclaw", "hermes"], default="openclaw")
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    registry = platform["acr_name"]
    login_server = platform["acr_login_server"]
    tag = args.tag or f"dev-{int(time.time())}-{git_sha()}"

    runtime_repository = f"{args.runtime}-runtime"
    runtime_dockerfile = f"runtimes/{args.runtime}/Dockerfile"
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

    runtime_path = runtime_app_tfvars_path(args.runtime)
    if not runtime_path.exists():
        raise FileNotFoundError(f"{runtime_path} does not exist. Run scripts.setup_app_tfvars first.")
    tfvars = json.loads(runtime_path.read_text(encoding="utf-8"))
    built_images: dict[str, str] = {}
    for var_name, (repository, dockerfile, context) in images.items():
        digest = acr_build(registry=registry, repository=repository, dockerfile=dockerfile, context=context, tag=tag)
        tfvars[var_name] = f"{login_server}/{repository}@{digest}"
        built_images[var_name] = tfvars[var_name]
        if var_name == "runtime_image":
            tfvars["agent_runtime"] = args.runtime
            tfvars["runtime_disk_image_name"] = f"{args.runtime}-runtime-{digest.removeprefix('sha256:')[:12]}"

    write_tfvars(runtime_path, tfvars)
    write_tfvars(APPS_DIR / "generated.app.auto.tfvars.json", tfvars)
    write_tfvars(APPS_DIR / "generated.runtime.auto.tfvars.json", tfvars)
    (APPS_DIR / "generated.images.auto.tfvars.json").unlink(missing_ok=True)
    print(json.dumps({"tag": tag, "runtime": args.runtime, **built_images}, indent=2))


if __name__ == "__main__":
    main()
