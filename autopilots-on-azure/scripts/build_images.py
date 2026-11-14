from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from scripts.setup_app_tfvars import runtime_app_tfvars_path
from scripts.tf_helpers import PLATFORM_DIR, REPO_ROOT, run, terraform_output, write_tfvars


HERMES_UPSTREAM_SOURCE = "docker.io/nousresearch/hermes-agent"
HERMES_UPSTREAM_VERSION = "v0.22.6"
HERMES_UPSTREAM_DIGEST = "sha256:9a31a97e38540562142c52f439c124325355f671f31ad10f9ebd1c7b55dab7ea"
HERMES_UPSTREAM_REPOSITORY = "hermes-agent-upstream"


@contextmanager
def pinned_build_context(context: str):
    source = (REPO_ROOT / context).resolve()
    with tempfile.TemporaryDirectory(prefix="autopilots-acr-source-") as temporary:
        staged = Path(temporary)
        manifest_path = REPO_ROOT / "deployment-manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            revision = manifest["source_commit"]
            inventory = manifest.get("files")
            if not inventory:
                raise ValueError("Git-free builds require the pinned source file inventory in deployment-manifest.json.")
            expected = set()
            for name, expected_hash in inventory.items():
                if Path(name).is_absolute() or ".." in Path(name).parts:
                    raise ValueError("Pinned build inventory contains an unsafe path.")
                path = REPO_ROOT / name
                relative = path.relative_to(source) if path.is_relative_to(source) else None
                if relative is None:
                    continue
                if path.is_symlink() or ".." in Path(name).parts:
                    raise ValueError("Pinned build inventory contains an unsafe path.")
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != expected_hash:
                    raise ValueError(f"Pinned build source differs: {name}")
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                expected.add(relative.as_posix())
        else:
            repository = Path(subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"], cwd=REPO_ROOT, text=True,
            ).strip())
            revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
            dirty = subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=normal", "--", str(source)],
                cwd=repository, text=True,
            )
            if dirty:
                raise ValueError("Build source is dirty; commit the intended source before exporting its pinned tree.")
            relative_source = source.relative_to(repository)
            tree = revision if relative_source == Path(".") else revision + ":" + relative_source.as_posix()
            expected = set(subprocess.check_output(
                ["git", "ls-tree", "-r", "--name-only", tree], cwd=repository, text=True,
            ).splitlines())
            archive = subprocess.check_output(["git", "archive", "--format=zip", tree], cwd=repository)
            with zipfile.ZipFile(io.BytesIO(archive)) as packed:
                for member in packed.infolist():
                    if member.is_dir():
                        continue
                    if ".." in Path(member.filename).parts or Path(member.filename).is_absolute():
                        raise ValueError("Pinned Git archive contains an unsafe path.")
                    target = staged / member.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(packed.read(member))
            if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip() != revision:
                raise ValueError("Build source moved away from the pinned commit during export.")
        actual = {path.relative_to(staged).as_posix() for path in staged.rglob("*") if path.is_file()}
        if actual != expected:
            raise ValueError("Staged build context differs from its pinned tracked tree.")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as packed:
            for name in sorted(actual):
                member = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                packed.writestr(member, (staged / name).read_bytes())
        print(json.dumps({"build_source_commit": revision,
                          "staged_archive_sha256": hashlib.sha256(archive.getvalue()).hexdigest(),
                          "tracked_members": len(actual)}))
        yield staged


def git_sha() -> str:
    result = run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture=True, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "nogit"


def import_hermes_upstream(registry: str, login_server: str) -> str:
    """Mirror the official Hermes image into ACR by digest; ACR pulls it server-side without stored credentials."""
    reference = f"{HERMES_UPSTREAM_REPOSITORY}@{HERMES_UPSTREAM_DIGEST}"
    present = run(
        ["az", "acr", "manifest", "show", "--registry", registry, "--name", reference, "--only-show-errors", "-o", "none"],
        cwd=REPO_ROOT, capture=True, check=False,
    )
    if present.returncode != 0:
        run([
            "az", "acr", "import", "--name", registry,
            "--source", f"{HERMES_UPSTREAM_SOURCE}@{HERMES_UPSTREAM_DIGEST}",
            "--image", f"{HERMES_UPSTREAM_REPOSITORY}:{HERMES_UPSTREAM_VERSION}",
            "--only-show-errors",
        ], cwd=REPO_ROOT)
    return f"{login_server}/{reference}"


def acr_build(
    *, registry: str, repository: str, dockerfile: str, context: str, tag: str,
    build_args: dict[str, str] | None = None,
    agent_pool_name: str = "",
) -> str:
    arguments = [argument for name, value in (build_args or {}).items() for argument in ("--build-arg", f"{name}={value}")]
    with pinned_build_context(context) as staged:
        staged_dockerfile = staged / (REPO_ROOT / dockerfile).resolve().relative_to((REPO_ROOT / context).resolve())
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
            str(staged_dockerfile),
            *(["--agent-pool", agent_pool_name] if agent_pool_name else []),
            *arguments,
            str(staged),
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
    }

    states = list(dict.fromkeys(args.state_name or ["hermes"]))
    runtime_paths = [runtime_app_tfvars_path("hermes", state) for state in states]
    for path in runtime_paths:
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist. Run scripts.setup_app_tfvars first.")
    hermes_base = import_hermes_upstream(registry, login_server)
    built_images: dict[str, str] = {}
    for var_name, (repository, dockerfile, context) in images.items():
        build_args = {"HERMES_BASE_IMAGE": hermes_base} if var_name == "runtime_image" else None
        digest = acr_build(
            registry=registry, repository=repository, dockerfile=dockerfile, context=context, tag=tag,
            build_args=build_args,
        )
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
