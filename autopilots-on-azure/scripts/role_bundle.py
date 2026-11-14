from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import uuid

from azure.core.exceptions import ResourceNotFoundError

from scripts.tf_helpers import REPO_ROOT


def build_role_bundle(role: dict, *, root: Path = REPO_ROOT, verify: bool = True) -> tuple[dict[str, bytes], str]:
    path = PurePosixPath(str(role.get("path") or "blueprints/" + role["blueprint"]).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in str(path):
        raise ValueError("A local role path must stay relative to the bundled source root.")
    directory = root.joinpath(*path.parts)
    if directory.is_symlink() or not directory.is_dir() or not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("The local role directory is missing or escapes the bundled source root.")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", str(role["commit"])):
        raise ValueError("A local role requires its full immutable release commit.")
    files = {}
    hashes = {}
    for candidate in sorted(directory.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"Local role bundles must not contain symlinks: {candidate.name}")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(directory).as_posix()
        content = candidate.read_bytes()
        files[path.as_posix() + "/" + relative] = content
        hashes[relative] = hashlib.sha256(content).hexdigest()
    if not hashes:
        raise ValueError("The local role bundle is empty.")
    manifest = json.dumps({
        "schemaVersion": 1, "roleBlueprint": role["blueprint"], "roleBlueprintPath": path.as_posix(),
        "roleRelease": role["release"], "roleReleaseCommit": role["commit"], "files": hashes,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(manifest).hexdigest()
    if verify and role.get("bundle_sha256") != digest:
        raise ValueError("Local role bundle_sha256 is missing or differs from the bundled snapshot; expected " + digest)
    files["role-release.json"] = manifest
    return files, digest


def _execute(sandbox, command: str) -> None:
    completion = uuid.uuid4().hex
    result = sandbox.exec(command + " && printf '%s\\n' " + shlex.quote(completion))
    if (type(result.exit_code) is not int or result.exit_code != 0
            or completion not in str(result.stdout).splitlines()):
        raise RuntimeError("Applying private role configuration did not complete.")


def publish_role_bundle(sandbox, role: dict) -> str:
    files, digest = build_role_bundle(role)
    directory = "/data/role-bundles/" + digest
    paths = [directory + "/" + name for name in files]
    try:
        manifest = sandbox.read_file(directory + "/role-release.json")
    except ResourceNotFoundError:
        manifest = None
    if manifest is not None:
        if hashlib.sha256(manifest).hexdigest() != digest:
            raise ValueError("The immutable role data directory contains a different manifest.")
        _execute(sandbox, "chmod 0600 " + " ".join(shlex.quote(path) for path in paths))
        return directory
    parents = sorted({directory + "/" + str(PurePosixPath(name).parent) for name in files})
    _execute(sandbox, "mkdir -p " + " ".join(shlex.quote(parent) for parent in parents))
    for name in sorted(files, key=lambda value: value == "role-release.json"):
        sandbox.write_file(directory + "/" + name, files[name], mode="0600")
    _execute(sandbox, "chmod 0600 " + " ".join(shlex.quote(path) for path in paths))
    return directory
