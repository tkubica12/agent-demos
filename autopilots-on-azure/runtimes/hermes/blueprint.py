from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import yaml


INSTANCE_MANIFEST = "autopilots-instance.json"
DEFAULT_DISTRIBUTION_OWNED = (
    "SOUL.md",
    "config.yaml",
    "mcp.json",
    "skills",
    "cron",
    "distribution.yaml",
)
USER_OWNED_ROOTS = frozenset(
    {
        ".env",
        "auth.json",
        "memories",
        "sessions",
        "state.db",
        "state.db-shm",
        "state.db-wal",
        "hermes_state.db",
        "response_store.db",
        "response_store.db-shm",
        "response_store.db-wal",
        "logs",
        "workspace",
        "plans",
        "home",
        "local",
        "checkpoints",
        "backups",
        "cache",
        "image_cache",
        "audio_cache",
        "document_cache",
        "browser_screenshots",
    }
)
PROFILE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class BlueprintSettings:
    name: str
    source: str
    repository_path: str
    version: str
    commit: str
    instance_id: str
    assignee_scope: str


@dataclass(frozen=True)
class BlueprintInstall:
    profile_home: Path
    manifest: dict[str, Any]
    changed: bool


def settings_from_environment() -> BlueprintSettings | None:
    source = os.getenv("HERMES_BLUEPRINT_SOURCE", "").strip()
    if not source:
        partial = [
            name
            for name in (
                "HERMES_BLUEPRINT_NAME",
                "HERMES_BLUEPRINT_PATH",
                "HERMES_BLUEPRINT_VERSION",
                "HERMES_BLUEPRINT_COMMIT",
            )
            if os.getenv(name, "").strip()
        ]
        if partial:
            raise ValueError(f"HERMES_BLUEPRINT_SOURCE is required when setting: {', '.join(partial)}.")
        return None
    name = os.getenv("HERMES_BLUEPRINT_NAME", "junior-project-manager").strip()
    commit = os.getenv("HERMES_BLUEPRINT_COMMIT", "").strip()
    parsed_source = urlsplit(source)
    if parsed_source.scheme in {"http", "https"} and (parsed_source.username or parsed_source.password):
        raise ValueError("HERMES_BLUEPRINT_SOURCE must not contain embedded credentials.")
    if not PROFILE_NAME_PATTERN.fullmatch(name):
        raise ValueError("HERMES_BLUEPRINT_NAME must contain only lowercase letters, numbers, and hyphens.")
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("HERMES_BLUEPRINT_COMMIT must be a full 40-character Git commit SHA.")
    instance_id = (
        os.getenv("AUTOPILOT_INSTANCE_ID", "").strip()
        or os.getenv("AUTOPILOT_NAME", "").strip()
        or name
    )
    return BlueprintSettings(
        name=name,
        source=source,
        repository_path=os.getenv("HERMES_BLUEPRINT_PATH", "").strip().strip("/\\"),
        version=os.getenv("HERMES_BLUEPRINT_VERSION", "").strip(),
        commit=commit.lower(),
        instance_id=instance_id,
        assignee_scope=os.getenv("HERMES_ASSIGNEE_SCOPE", "unassigned").strip() or "unassigned",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _instance_manifest_path(profile_home: Path) -> Path:
    return profile_home / "local" / INSTANCE_MANIFEST


def _matches_installed(settings: BlueprintSettings, manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("blueprintName") == settings.name
        and manifest.get("blueprintSource") == settings.source
        and manifest.get("blueprintRepositoryPath", "") == settings.repository_path
        and manifest.get("blueprintCommit") == settings.commit
        and (not settings.version or manifest.get("blueprintVersion") == settings.version)
        and manifest.get("instanceId") == settings.instance_id
        and manifest.get("assigneeScope") == settings.assignee_scope
    )


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _clone_at_commit(settings: BlueprintSettings, destination: Path) -> Path:
    try:
        _run_git(["clone", "--no-checkout", settings.source, str(destination)])
        _run_git(["checkout", "--detach", settings.commit], cwd=destination)
        actual_commit = _run_git(["rev-parse", "HEAD"], cwd=destination).lower()
    except FileNotFoundError as exc:
        raise RuntimeError("Git is required to install a Hermes blueprint.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Failed to fetch Hermes blueprint: {detail}") from exc
    if actual_commit != settings.commit:
        raise RuntimeError(
            f"Blueprint checkout resolved to {actual_commit}, expected pinned commit {settings.commit}."
        )
    distribution_root = destination
    if settings.repository_path:
        relative = PurePosixPath(settings.repository_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("HERMES_BLUEPRINT_PATH must be a relative repository path.")
        distribution_root = destination.joinpath(*relative.parts)
    if not distribution_root.is_dir():
        raise FileNotFoundError(f"Blueprint path does not exist at commit {settings.commit}: {settings.repository_path}")
    return distribution_root


def _distribution_manifest(distribution_root: Path, settings: BlueprintSettings) -> tuple[dict[str, Any], list[str]]:
    path = distribution_root / "distribution.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Blueprint is missing {path}.")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("distribution.yaml must contain a YAML mapping.")
    name = str(payload.get("name") or "").strip()
    version = str(payload.get("version") or "").strip()
    if name != settings.name:
        raise ValueError(f"Blueprint manifest name is {name!r}, expected {settings.name!r}.")
    if settings.version and version != settings.version:
        raise ValueError(f"Blueprint manifest version is {version!r}, expected {settings.version!r}.")
    owned = payload.get("distribution_owned") or list(DEFAULT_DISTRIBUTION_OWNED)
    if not isinstance(owned, list) or not owned:
        raise ValueError("distribution_owned must be a non-empty list.")
    normalized = [_validate_owned_path(str(item)) for item in owned]
    if "distribution.yaml" not in normalized:
        normalized.append("distribution.yaml")
    return payload, normalized


def _validate_owned_path(value: str) -> str:
    relative = PurePosixPath(value.strip().replace("\\", "/").strip("/"))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Invalid distribution-owned path: {value!r}.")
    if relative.parts[0] in USER_OWNED_ROOTS:
        raise ValueError(f"Blueprint cannot own instance-private path: {value!r}.")
    return relative.as_posix()


def _reject_symlinks(distribution_root: Path, owned_paths: list[str]) -> None:
    for relative in owned_paths:
        source = distribution_root.joinpath(*PurePosixPath(relative).parts)
        if not source.exists():
            continue
        candidates = [source, *source.rglob("*")] if source.is_dir() else [source]
        for candidate in candidates:
            if candidate.is_symlink():
                raise ValueError(f"Blueprint distribution cannot contain symlinks: {candidate.relative_to(distribution_root)}")


def _copy_owned_paths(distribution_root: Path, profile_home: Path, owned_paths: list[str]) -> None:
    profile_home.mkdir(parents=True, exist_ok=True)
    for relative in owned_paths:
        parts = PurePosixPath(relative).parts
        source = distribution_root.joinpath(*parts)
        destination = profile_home.joinpath(*parts)
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        if not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)


def _remove_stale_owned_paths(profile_home: Path, previous: list[str], current: list[str]) -> None:
    current_paths = {PurePosixPath(path) for path in current}
    for value in previous:
        normalized = _validate_owned_path(value)
        relative = PurePosixPath(normalized)
        if relative in current_paths:
            continue
        destination = profile_home.joinpath(*relative.parts)
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()


def install_or_update_blueprint(hermes_home: Path, settings: BlueprintSettings) -> BlueprintInstall:
    profile_home = hermes_home / "profiles" / settings.name
    manifest_path = _instance_manifest_path(profile_home)
    installed = _read_json(manifest_path)
    if profile_home.is_dir() and _matches_installed(settings, installed):
        return BlueprintInstall(profile_home=profile_home, manifest=installed, changed=False)

    with tempfile.TemporaryDirectory(prefix="hermes-blueprint-") as temp_dir:
        distribution_root = _clone_at_commit(settings, Path(temp_dir) / "repository")
        distribution, owned_paths = _distribution_manifest(distribution_root, settings)
        _reject_symlinks(distribution_root, owned_paths)
        previous_owned = installed.get("distributionOwned") or []
        if not isinstance(previous_owned, list):
            raise ValueError(f"{manifest_path} distributionOwned must be a list.")
        _remove_stale_owned_paths(profile_home, [str(path) for path in previous_owned], owned_paths)
        _copy_owned_paths(distribution_root, profile_home, owned_paths)

    for directory in ("memories", "sessions", "logs", "workspace", "plans", "home", "local"):
        (profile_home / directory).mkdir(parents=True, exist_ok=True)

    instance_manifest = {
        "blueprintName": settings.name,
        "blueprintSource": settings.source,
        "blueprintRepositoryPath": settings.repository_path,
        "blueprintVersion": str(distribution.get("version") or ""),
        "blueprintCommit": settings.commit,
        "instanceId": settings.instance_id,
        "assigneeScope": settings.assignee_scope,
        "profileName": settings.name,
        "distributionOwned": owned_paths,
        "installedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_json_atomic(manifest_path, instance_manifest)
    return BlueprintInstall(profile_home=profile_home, manifest=instance_manifest, changed=True)
