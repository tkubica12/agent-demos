from __future__ import annotations

import json
from pathlib import Path
import re


ROLES = frozenset({"runtime", "gateway", "simulator"})
IMAGE_PATTERN = r"ghcr\.io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+@sha256:[0-9a-f]{64}"


def load_manifest(path: Path) -> dict:
    return validate_manifest(json.loads(path.read_text(encoding="utf-8")))


def validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != {"schema", "source_commit", "images"}:
        raise ValueError("Image manifest requires exactly schema, source_commit and images.")
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        raise ValueError("Image manifest schema must be integer 1.")
    if not isinstance(manifest["source_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"]):
        raise ValueError("Image manifest source_commit must be a full lowercase Git commit SHA.")
    images = manifest["images"]
    if not isinstance(images, dict) or set(images) != ROLES:
        raise ValueError("Image manifest requires exactly runtime, gateway and simulator images.")
    for role, reference in images.items():
        if not isinstance(reference, str) or not re.fullmatch(IMAGE_PATTERN, reference):
            raise ValueError(f"{role} image must be an immutable ghcr.io repository@sha256:<64 lowercase hex> reference.")
    return manifest
