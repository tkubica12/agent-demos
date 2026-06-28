from __future__ import annotations

import argparse
import json
import subprocess
import time

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
    parser = argparse.ArgumentParser(description="Build OpenClaw images in ACR and write apps generated image tfvars.")
    parser.add_argument("--tag", default="")
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    registry = platform["acr_name"]
    login_server = platform["acr_login_server"]
    tag = args.tag or f"dev-{int(time.time())}-{git_sha()}"

    images = {
        "openclaw_image": (
            "openclaw-on-azure",
            "image/Dockerfile",
            "image",
        ),
        "bridge_image": (
            "openclaw-bridge",
            "bridge/Dockerfile",
            ".",
        ),
        "private_mcp_image": (
            "private-incidents-mcp",
            "private-incidents-mcp/Dockerfile",
            "private-incidents-mcp",
        ),
    }

    tfvars: dict[str, str] = {}
    for var_name, (repository, dockerfile, context) in images.items():
        digest = acr_build(registry=registry, repository=repository, dockerfile=dockerfile, context=context, tag=tag)
        tfvars[var_name] = f"{login_server}/{repository}@{digest}"

    write_tfvars(APPS_DIR / "generated.images.auto.tfvars.json", tfvars)
    print(json.dumps({"tag": tag, **tfvars}, indent=2))


if __name__ == "__main__":
    main()
