from __future__ import annotations

import argparse
import binascii
import json
import struct
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from jsonschema import Draft4Validator

SCHEMA_PATH = Path(__file__).parents[1] / "tests" / "fixtures" / "MicrosoftTeams.v1.23.schema.json"
PACKAGE_FILES = frozenset({"manifest.json", "outline.png", "color.png"})


def validate_manifest(manifest: dict[str, object]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft4Validator(schema).iter_errors(manifest),
        key=lambda error: list(error.path),
    )
    if errors:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors
        )
        raise ValueError(f"Teams manifest schema validation failed: {messages}")
    if manifest.get("manifestVersion") != "1.23":
        raise ValueError("Teams manifestVersion must match the vendored v1.23 schema.")


def validate_package(package_path: Path) -> None:
    with ZipFile(package_path) as package:
        names = frozenset(package.namelist())
        if names != PACKAGE_FILES:
            raise ValueError(f"Teams package must contain exactly {sorted(PACKAGE_FILES)}.")
        manifest = json.loads(package.read("manifest.json"))
        validate_manifest(manifest)
        expected_dimensions = {"outline.png": (32, 32), "color.png": (192, 192)}
        for name, expected in expected_dimensions.items():
            content = package.read(name)
            if content[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"{name} is not a PNG.")
            dimensions = struct.unpack(">II", content[16:24])
            if dimensions != expected:
                raise ValueError(f"{name} dimensions are {dimensions}; expected {expected}.")


def png(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    row = b"\x00" + bytes(rgba) * width
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build(output: Path, app_id: str, teams_app_id: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.23/MicrosoftTeams.schema.json",
        "manifestVersion": "1.23",
        "version": "1.0.1",
        "id": teams_app_id,
        "developer": {
            "name": "Private Bot Proxy Proof",
            "websiteUrl": "https://tomasonline.net",
            "privacyUrl": "https://tomasonline.net",
            "termsOfUseUrl": "https://tomasonline.net",
        },
        "name": {"short": "Private Bot Proxy", "full": "Private Bot Proxy Proof"},
        "description": {
            "short": "Tests Azure Bot private endpoint ingress through Application Gateway.",
            "full": (
                "A fixed-response Teams agent used for a falsifiable private endpoint experiment."
            ),
        },
        "icons": {"outline": "outline.png", "color": "color.png"},
        "accentColor": "#0078D4",
        "bots": [
            {
                "botId": app_id,
                "scopes": ["personal", "groupChat"],
                "supportsFiles": False,
                "isNotificationOnly": False,
            }
        ],
        "permissions": ["identity"],
        "validDomains": ["token.botframework.com"],
        "webApplicationInfo": {
            "id": app_id,
            "resource": f"api://botid-{app_id}",
        },
    }
    validate_manifest(manifest)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output / "outline.png").write_bytes(png(32, 32, (0, 120, 212, 255)))
    (output / "color.png").write_bytes(png(192, 192, (0, 120, 212, 255)))
    package_path = output / "private-bot-proxy.zip"
    with ZipFile(package_path, "w", ZIP_DEFLATED) as package:
        for name in ("manifest.json", "outline.png", "color.png"):
            package.write(output / name, name)
    validate_package(package_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--teams-app-id", required=True)
    arguments = parser.parse_args()
    build(arguments.output, arguments.app_id, arguments.teams_app_id)
