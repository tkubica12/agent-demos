from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

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
