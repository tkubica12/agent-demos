from __future__ import annotations

import argparse
import json
import struct
import zipfile
import zlib
from pathlib import Path
from urllib.parse import urlparse

from scripts.tf_helpers import APPS_DIR, PLATFORM_DIR, REPO_ROOT, terraform_output


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)


def solid_png(path: Path, *, width: int, height: int, rgba: tuple[int, int, int, int]) -> None:
    raw = b"".join(b"\x00" + bytes(rgba) * width for _ in range(height))
    data = b"\x89PNG\r\n\x1a\n"
    data += png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    data += png_chunk(b"IDAT", zlib.compress(raw))
    data += png_chunk(b"IEND", b"")
    path.write_bytes(data)


def load_teams_tfvars() -> dict[str, str]:
    path = APPS_DIR / "generated.teams.auto.tfvars.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist. Run `uv run python -m scripts.setup_teams_tfvars` first.")
    return json.loads(path.read_text(encoding="utf-8"))


def bridge_domain() -> str:
    apps = terraform_output(APPS_DIR)
    bridge_url = apps["bridge_url"]
    domain = urlparse(bridge_url).netloc
    if not domain:
        raise RuntimeError(f"Could not parse bridge domain from {bridge_url}.")
    return domain


def render_manifest(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def enable_targeted_messages_preview(manifest: str) -> str:
    payload = json.loads(manifest)
    for bot in payload.get("bots", []):
        bot["supportsTargetedMessages"] = True
    return json.dumps(payload, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Package the OpenClaw Teams app manifest for sideloading.")
    parser.add_argument("--output", default="")
    parser.add_argument(
        "--preview-targeted-messages",
        action="store_true",
        help="Add supportsTargetedMessages=true. This is public developer preview and may be rejected by some Teams upload validators.",
    )
    args = parser.parse_args()

    platform = terraform_output(PLATFORM_DIR)
    teams = load_teams_tfvars()
    domain = bridge_domain()
    out_dir = REPO_ROOT / ".local" / platform["suffix"] / "teams"
    out_dir.mkdir(parents=True, exist_ok=True)

    template = (REPO_ROOT / "teams" / "manifest.template.json").read_text(encoding="utf-8")
    manifest = render_manifest(
        template,
        {
            "TEAMS_APP_ID": teams["teams_bot_app_id"],
            "TEAMS_BOT_APP_ID": teams["teams_bot_app_id"],
            "BRIDGE_DOMAIN": domain,
        },
    )
    if args.preview_targeted_messages:
        manifest = enable_targeted_messages_preview(manifest)

    manifest_path = out_dir / "manifest.json"
    outline_path = out_dir / "outline.png"
    color_path = out_dir / "color.png"
    package_path = Path(args.output) if args.output else out_dir / "openclaw-teams.zip"

    manifest_path.write_text(manifest, encoding="utf-8")
    solid_png(outline_path, width=32, height=32, rgba=(255, 255, 255, 255))
    solid_png(color_path, width=192, height=192, rgba=(98, 100, 167, 255))

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest)
        archive.write(outline_path, "outline.png")
        archive.write(color_path, "color.png")

    print(
        json.dumps(
            {
                "package": str(package_path),
                "manifest": str(manifest_path),
                "teamsBotAppId": teams["teams_bot_app_id"],
                "bridgeDomain": domain,
                "previewTargetedMessages": args.preview_targeted_messages,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
