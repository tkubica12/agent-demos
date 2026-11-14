"""Validate the selected deployed widget bridge and unchanged skill from guest-byte attestation."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re


EXPECTED = {
    "bridge/html_widgets.py": {
        "git_blob_sha1": "63582be6a82b4e0ff261cc1505305372e87c74df",
        "sha256": "e80a0f605323735d1eaeabc376f236fdb9847827f5812f04d58a499694426c4f",
    },
    "runtime_skills/teams-html-widget/SKILL.md": {
        "git_blob_sha1": "b899de1ab25ad95f4e6719b3b3b48b24689f8f40",
        "sha256": "c2a7f13381adc0f5afdaa37ab1b825925fa673a74cd568b59de557af696020c4",
    },
}


def validate_attestation(payload, source_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or payload.get("source_commit") != source_commit:
        raise ValueError("Widget attestation does not match the operator-approved source commit.")
    release = payload.get("runtime_release")
    bundled = payload.get("bundled_skills_path")
    if not isinstance(release, str) or not release or not isinstance(bundled, str):
        raise ValueError("Selected native runtime release and bundled skill path are unavailable.")
    root = PurePosixPath(bundled)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("Invalid selected bundled skill path.")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("Guest widget file attestation is unavailable.")
    projected = {}
    for name, expected in EXPECTED.items():
        file = files.get(name)
        if not isinstance(file, dict) or any(file.get(key) != value for key, value in expected.items()):
            raise ValueError(f"Deployed guest bytes differ from the unchanged required blob: {name}.")
        guest = file.get("guest_path")
        if not isinstance(guest, str) or not PurePosixPath(guest).is_absolute() or ".." in PurePosixPath(guest).parts:
            raise ValueError("Invalid attested guest file path.")
        if name.endswith("SKILL.md") and PurePosixPath(guest) != root / "teams-html-widget" / "SKILL.md":
            raise ValueError("Attested skill is not the selected HERMES_BUNDLED_SKILLS file.")
        projected[name] = {key: file[key] for key in ("guest_path", "sha256", "git_blob_sha1")}
    return {
        "status": "pass", "source_commit": source_commit, "runtime_release": release,
        "bundled_skills_path": bundled, "files": projected, "proof": "actual-guest-bytes-matched-required-git-blobs",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    print(json.dumps(validate_attestation(json.loads(args.attestation.read_text()), args.source_commit)))


if __name__ == "__main__":
    main()
