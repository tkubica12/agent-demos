from __future__ import annotations

import base64
import hashlib
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
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


WORKER_MANIFEST = "worker.json"
DEFAULT_DISTRIBUTION_OWNED = (
    "SOUL.md",
    "config.yaml",
    "mcp.json",
    "skills/role",
    "cron",
    "distribution.yaml",
)
WORKER_OWNED_ROOTS = frozenset(
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
        "learning",
        "checkpoints",
        "backups",
        "cache",
        "image_cache",
        "audio_cache",
        "document_cache",
        "browser_screenshots",
    }
)
RESERVED_WORKER_SKILL_NAMESPACES = frozenset({"private", "candidates", "runtime"})
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
RELEASE_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


@dataclass(frozen=True)
class RoleReleaseSettings:
    role_blueprint: str
    source: str
    repository_path: str
    role_release: str
    commit: str
    worker_id: str
    assignment_scope: str


@dataclass(frozen=True)
class RoleReleaseInstall:
    profile_home: Path
    manifest: dict[str, Any]
    changed: bool


def role_release_settings_from_environment() -> RoleReleaseSettings | None:
    legacy = [
        name
        for name in (
            "HERMES_BLUEPRINT_NAME",
            "HERMES_BLUEPRINT_SOURCE",
            "HERMES_BLUEPRINT_PATH",
            "HERMES_BLUEPRINT_VERSION",
            "HERMES_BLUEPRINT_COMMIT",
            "HERMES_ASSIGNEE_SCOPE",
        )
        if os.getenv(name, "").strip()
    ]
    if legacy:
        raise ValueError(
            "Legacy Hermes blueprint environment is not accepted by Role Release 3. "
            f"Configure the HERMES_ROLE_* and WORKER_* settings explicitly; found: {', '.join(legacy)}."
        )
    source = os.getenv("HERMES_ROLE_BLUEPRINT_SOURCE", "").strip()
    if not source:
        partial = [
            name
            for name in (
                "HERMES_ROLE_BLUEPRINT",
                "HERMES_ROLE_BLUEPRINT_PATH",
                "HERMES_ROLE_RELEASE",
                "HERMES_ROLE_RELEASE_COMMIT",
            )
            if os.getenv(name, "").strip()
        ]
        if partial:
            raise ValueError(
                f"HERMES_ROLE_BLUEPRINT_SOURCE is required when setting: {', '.join(partial)}."
            )
        return None
    role_blueprint = os.getenv("HERMES_ROLE_BLUEPRINT", "junior-project-manager").strip()
    commit = os.getenv("HERMES_ROLE_RELEASE_COMMIT", "").strip()
    role_release = os.getenv("HERMES_ROLE_RELEASE", "").strip()
    parsed_source = urlsplit(source)
    if parsed_source.scheme in {"http", "https"} and (parsed_source.username or parsed_source.password):
        raise ValueError("HERMES_ROLE_BLUEPRINT_SOURCE must not contain embedded credentials.")
    if not NAME_PATTERN.fullmatch(role_blueprint):
        raise ValueError("HERMES_ROLE_BLUEPRINT must contain only lowercase letters, numbers, and hyphens.")
    if not RELEASE_PATTERN.fullmatch(role_release):
        raise ValueError("HERMES_ROLE_RELEASE must use semantic version MAJOR.MINOR.PATCH.")
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ValueError("HERMES_ROLE_RELEASE_COMMIT must be a full 40-character Git commit SHA.")
    worker_id = (
        os.getenv("WORKER_ID", "").strip()
        or os.getenv("AUTOPILOT_NAME", "").strip()
        or role_blueprint
    )
    if not NAME_PATTERN.fullmatch(worker_id):
        raise ValueError("WORKER_ID must contain only lowercase letters, numbers, and hyphens.")
    return RoleReleaseSettings(
        role_blueprint=role_blueprint,
        source=source,
        repository_path=os.getenv("HERMES_ROLE_BLUEPRINT_PATH", "").strip().strip("/\\"),
        role_release=role_release,
        commit=commit.lower(),
        worker_id=worker_id,
        assignment_scope=os.getenv("WORKER_ASSIGNMENT_SCOPE", "unassigned").strip() or "unassigned",
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


def worker_manifest_path(profile_home: Path) -> Path:
    return profile_home / "local" / WORKER_MANIFEST


def _matches_installed(settings: RoleReleaseSettings, manifest: dict[str, Any]) -> bool:
    return (
        manifest.get("roleBlueprint") == settings.role_blueprint
        and manifest.get("roleBlueprintSource") == settings.source
        and manifest.get("roleBlueprintPath", "") == settings.repository_path
        and manifest.get("roleRelease") == settings.role_release
        and manifest.get("roleReleaseCommit") == settings.commit
        and manifest.get("workerId") == settings.worker_id
        and manifest.get("assignmentScope") == settings.assignment_scope
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


def _clone_at_commit(settings: RoleReleaseSettings, destination: Path) -> Path:
    try:
        _run_git(["clone", "--no-checkout", settings.source, str(destination)])
        _run_git(["checkout", "--detach", settings.commit], cwd=destination)
        actual_commit = _run_git(["rev-parse", "HEAD"], cwd=destination).lower()
    except FileNotFoundError as exc:
        raise RuntimeError("Git is required to install a Hermes Role Release.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f"Failed to fetch Hermes Role Release: {detail}") from exc
    if actual_commit != settings.commit:
        raise RuntimeError(
            f"Role Release checkout resolved to {actual_commit}, expected {settings.commit}."
        )
    distribution_root = destination
    if settings.repository_path:
        relative = PurePosixPath(settings.repository_path.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("HERMES_ROLE_BLUEPRINT_PATH must be a relative repository path.")
        distribution_root = destination.joinpath(*relative.parts)
    if not distribution_root.is_dir():
        raise FileNotFoundError(
            f"Role Blueprint path does not exist at commit {settings.commit}: {settings.repository_path}"
        )
    return distribution_root


def _validate_owned_path(value: str) -> str:
    relative = PurePosixPath(value.strip().replace("\\", "/").strip("/"))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Invalid Role Blueprint-owned path: {value!r}.")
    if relative.parts[0] in WORKER_OWNED_ROOTS:
        raise ValueError(f"Role Blueprint cannot own Worker-private path: {value!r}.")
    if relative.parts[0] == "skills":
        if len(relative.parts) < 2 or relative.parts[1] != "role":
            raise ValueError("Role Blueprint skill paths must be under skills/role.")
        if len(relative.parts) >= 2 and relative.parts[1] in RESERVED_WORKER_SKILL_NAMESPACES:
            raise ValueError(f"Role Blueprint cannot own reserved Worker skill namespace: {value!r}.")
    return relative.as_posix()


def _distribution_manifest(
    distribution_root: Path,
    settings: RoleReleaseSettings,
) -> tuple[dict[str, Any], list[str]]:
    path = distribution_root / "distribution.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Role Blueprint is missing {path}.")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("distribution.yaml must contain a YAML mapping.")
    role_blueprint = str(payload.get("role_blueprint") or "").strip()
    role_release = str(payload.get("role_release") or "").strip()
    if role_blueprint != settings.role_blueprint:
        raise ValueError(
            f"Role Blueprint manifest name is {role_blueprint!r}, expected {settings.role_blueprint!r}."
        )
    if role_release != settings.role_release:
        raise ValueError(
            f"Role Release manifest is {role_release!r}, expected {settings.role_release!r}."
        )
    owned = payload.get("distribution_owned") or list(DEFAULT_DISTRIBUTION_OWNED)
    if not isinstance(owned, list) or not owned:
        raise ValueError("distribution_owned must be a non-empty list.")
    normalized = [_validate_owned_path(str(item)) for item in owned]
    if "distribution.yaml" not in normalized:
        normalized.append("distribution.yaml")
    return payload, normalized


def _reject_symlinks(distribution_root: Path, owned_paths: list[str]) -> None:
    for relative in owned_paths:
        source = distribution_root.joinpath(*PurePosixPath(relative).parts)
        if not source.exists():
            continue
        candidates = [source, *source.rglob("*")] if source.is_dir() else [source]
        for candidate in candidates:
            if candidate.is_symlink():
                raise ValueError(
                    f"Role Blueprint distribution cannot contain symlinks: {candidate.relative_to(distribution_root)}"
                )


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


def _hash_files(root: Path, *, profile_home: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not root.exists():
        return hashes
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Role Skill tree cannot contain symlinks: {path}.")
        if path.is_file() and path.name != ".usage.json":
            hashes[path.relative_to(profile_home).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def role_skill_hashes(profile_home: Path) -> dict[str, str]:
    return _hash_files(profile_home / "skills" / "role", profile_home=profile_home)


def candidate_improvement_hashes(profile_home: Path) -> dict[str, str]:
    return _hash_files(profile_home / "skills" / "candidates", profile_home=profile_home)


def governed_state_hash(profile_home: Path) -> str:
    payload = {
        "roleSkills": role_skill_hashes(profile_home),
        "candidateImprovements": candidate_improvement_hashes(profile_home),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _release_tuple(value: str) -> tuple[int, int, int]:
    match = RELEASE_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid Role Release {value!r}.")
    return tuple(int(part) for part in match.groups())


def worker_refresh_requires_export(profile_home: Path, manifest: dict[str, Any]) -> bool:
    baseline = manifest.get("roleSkillBaseline")
    if not isinstance(baseline, dict):
        raise ValueError("Worker manifest roleSkillBaseline must be an object.")
    return role_skill_hashes(profile_home) != baseline or bool(candidate_improvement_hashes(profile_home))


def _require_export_receipt(profile_home: Path, manifest: dict[str, Any]) -> None:
    if not worker_refresh_requires_export(profile_home, manifest):
        return
    commit = str(manifest["roleReleaseCommit"])
    exports = profile_home / "learning" / "exports"
    receipt = _read_json(exports / f"{commit}.approved.json")
    packet = _read_json(exports / f"{commit}.packet.json")
    packet_digest = hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    signed_receipt = {key: value for key, value in receipt.items() if key != "signature"}
    public_key_value = os.getenv("COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY", "")
    signature_valid = False
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_value)).verify(
            base64.b64decode(receipt.get("signature", "")),
            json.dumps(
                signed_receipt,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8"),
        )
        signature_valid = True
    except (InvalidSignature, ValueError, TypeError):
        signature_valid = False
    if (
        receipt.get("approved") is not True
        or receipt.get("roleReleaseCommit") != commit
        or receipt.get("workerId") != manifest["workerId"]
        or receipt.get("governedStateHash") != governed_state_hash(profile_home)
        or receipt.get("packetDigest") != packet_digest
        or packet.get("governedStateHash") != receipt.get("governedStateHash")
        or not signature_valid
    ):
        raise RuntimeError(
            "Worker Refresh is blocked until current Role Skill diffs and Candidate Improvements "
            "have an approved export receipt."
        )


def _archive_release_state(profile_home: Path, manifest: dict[str, Any]) -> None:
    release = str(manifest["roleRelease"])
    archive = profile_home / "learning" / "archive" / f"role-release-{release}"
    archive.mkdir(parents=True, exist_ok=True)
    records = profile_home / "learning" / "records.jsonl"
    if records.exists() and records.stat().st_size:
        shutil.move(str(records), str(archive / "records.jsonl"))
    candidates = profile_home / "skills" / "candidates"
    if candidates.exists() and any(candidates.iterdir()):
        destination = archive / "candidate-improvements"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(candidates), str(destination))
    candidates.mkdir(parents=True, exist_ok=True)


def _copy_path(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def _refresh_transaction_path(profile_home: Path) -> Path:
    return profile_home / "learning" / "refresh-transaction"


def _recover_refresh_transaction(profile_home: Path) -> None:
    transaction_root = _refresh_transaction_path(profile_home)
    metadata_path = transaction_root / "transaction.json"
    if not metadata_path.is_file():
        return
    metadata = _read_json(metadata_path)
    previous_owned = [str(value) for value in metadata.get("previousOwned") or []]
    target_owned = [str(value) for value in metadata.get("targetOwned") or []]
    for relative in sorted(set(previous_owned + target_owned)):
        path = profile_home.joinpath(*PurePosixPath(relative).parts)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    backup_profile = transaction_root / "profile"
    for relative in previous_owned:
        source = backup_profile.joinpath(*PurePosixPath(relative).parts)
        destination = profile_home.joinpath(*PurePosixPath(relative).parts)
        _copy_path(source, destination)
    for relative in (
        "learning/records.jsonl",
        "skills/candidates",
        f"learning/archive/role-release-{metadata['previousRelease']}",
    ):
        destination = profile_home.joinpath(*PurePosixPath(relative).parts)
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        source = backup_profile.joinpath(*PurePosixPath(relative).parts)
        _copy_path(source, destination)
    manifest_backup = transaction_root / "worker.json"
    if manifest_backup.is_file():
        _copy_path(manifest_backup, worker_manifest_path(profile_home))
    shutil.rmtree(transaction_root)


def _start_refresh_transaction(
    profile_home: Path,
    manifest: dict[str, Any],
    target_owned: list[str],
) -> Path:
    _recover_refresh_transaction(profile_home)
    transaction_root = _refresh_transaction_path(profile_home)
    transaction_root.mkdir(parents=True)
    previous_owned = [str(value) for value in manifest.get("distributionOwned") or []]
    backup_profile = transaction_root / "profile"
    for relative in previous_owned:
        _copy_path(
            profile_home.joinpath(*PurePosixPath(relative).parts),
            backup_profile.joinpath(*PurePosixPath(relative).parts),
        )
    for relative in ("learning/records.jsonl", "skills/candidates"):
        _copy_path(
            profile_home.joinpath(*PurePosixPath(relative).parts),
            backup_profile.joinpath(*PurePosixPath(relative).parts),
        )
    _copy_path(worker_manifest_path(profile_home), transaction_root / "worker.json")
    _write_json_atomic(
        transaction_root / "transaction.json",
        {
            "transactionVersion": "1.0",
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "previousRelease": manifest["roleRelease"],
            "previousOwned": previous_owned,
            "targetOwned": target_owned,
        },
    )
    return transaction_root


def install_or_refresh_role_release(
    hermes_home: Path,
    settings: RoleReleaseSettings,
) -> RoleReleaseInstall:
    profile_home = hermes_home / "profiles" / settings.role_blueprint
    _recover_refresh_transaction(profile_home)
    manifest_path = worker_manifest_path(profile_home)
    legacy_manifest = profile_home / "local" / "autopilots-instance.json"
    if legacy_manifest.exists() and not manifest_path.exists():
        raise RuntimeError(
            "Legacy Worker profile migration is required before installing Role Release 3. "
            "Convert private-cache.md to a Private Playbook, convert hot-learning to Candidate Improvements, "
            "and remove legacy distribution-owned skill paths."
        )
    installed = _read_json(manifest_path)
    if profile_home.is_dir() and _matches_installed(settings, installed):
        return RoleReleaseInstall(profile_home=profile_home, manifest=installed, changed=False)

    if installed:
        current_release = str(installed.get("roleRelease") or "")
        current_commit = str(installed.get("roleReleaseCommit") or "")
        if _release_tuple(settings.role_release) <= _release_tuple(current_release):
            raise ValueError(
                f"Worker Refresh requires a newer Role Release than {current_release}; "
                f"received {settings.role_release}."
            )
        if current_commit == settings.commit:
            raise ValueError("A new Role Release must use a new immutable commit.")
        _require_export_receipt(profile_home, installed)

    with tempfile.TemporaryDirectory(prefix="hermes-role-release-") as temp_dir:
        distribution_root = _clone_at_commit(settings, Path(temp_dir) / "repository")
        distribution, owned_paths = _distribution_manifest(distribution_root, settings)
        _reject_symlinks(distribution_root, owned_paths)
        previous_owned = installed.get("distributionOwned") or []
        if not isinstance(previous_owned, list):
            raise ValueError(f"{manifest_path} distributionOwned must be a list.")
        transaction_root = None
        try:
            if installed:
                transaction_root = _start_refresh_transaction(profile_home, installed, owned_paths)
                _archive_release_state(profile_home, installed)
            _remove_stale_owned_paths(profile_home, [str(path) for path in previous_owned], owned_paths)
            _copy_owned_paths(distribution_root, profile_home, owned_paths)
        except Exception:
            if installed:
                _recover_refresh_transaction(profile_home)
            raise

    for directory in (
        "memories",
        "sessions",
        "logs",
        "workspace",
        "plans",
        "home",
        "local",
        "learning",
        "skills/private",
        "skills/candidates",
    ):
        (profile_home / directory).mkdir(parents=True, exist_ok=True)

    worker_manifest = {
        "roleBlueprint": settings.role_blueprint,
        "roleBlueprintSource": settings.source,
        "roleBlueprintPath": settings.repository_path,
        "roleRelease": str(distribution["role_release"]),
        "roleReleaseCommit": settings.commit,
        "workerId": settings.worker_id,
        "assignmentScope": settings.assignment_scope,
        "profileName": settings.role_blueprint,
        "distributionOwned": owned_paths,
        "roleSkillBaseline": role_skill_hashes(profile_home),
        "refreshedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        _write_json_atomic(manifest_path, worker_manifest)
    except Exception:
        if installed:
            _recover_refresh_transaction(profile_home)
        raise
    transaction_root = _refresh_transaction_path(profile_home)
    if transaction_root.exists():
        shutil.rmtree(transaction_root)
    return RoleReleaseInstall(profile_home=profile_home, manifest=worker_manifest, changed=True)
