from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
FORBIDDEN = (
    "Direct" + "LineChannel",
    "Direct" + "LineExtension",
    "/v3/" + "extension",
    "bot" + "builder-",
    "named" + " pipes",
)


def test_no_forbidden_dependencies_or_resources() -> None:
    included = {".py", ".toml", ".tf", ".ps1", ".json", ".md", ".yml", ".yaml"}
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in included and ".venv" not in path.parts:
            content = path.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN:
                assert forbidden.lower() not in content.lower(), f"{forbidden} in {path}"


def test_python_dependencies_use_uv() -> None:
    assert not list(ROOT.rglob("requirements*.txt"))
    assert (ROOT / "uv.lock").is_file()
    assert (ROOT / "certificate-runner" / "uv.lock").is_file()


def test_manifest_consistency_when_generated() -> None:
    manifest_path = ROOT / ".artifacts" / "teams-package" / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bot = manifest["bots"][0]
    assert bot["botId"] == manifest["webApplicationInfo"]["id"]
    assert set(bot["scopes"]) == {"personal", "groupChat"}
    assert manifest["webApplicationInfo"]["resource"].startswith("api://botid-")
    assert "token.botframework.com" in manifest["validDomains"]


def test_certificate_runner_resolves_uv_console_scripts() -> None:
    dockerfile = (ROOT / "certificate-runner" / "Dockerfile").read_text(encoding="utf-8")
    assert 'ENV PATH="/runner/.venv/bin:$PATH"' in dockerfile
    assert (ROOT / "certificate-runner" / "uv.lock").is_file()


def test_gateway_nsg_is_managed_before_vnet() -> None:
    networking = (ROOT / "infra" / "bootstrap" / "networking.tf").read_text(encoding="utf-8")
    assert 'resource "azapi_resource" "app_gateway_nsg"' in networking
    assert "networkSecurityGroup = {" in networking
    assert "azapi_resource.app_gateway_nsg.id" in networking
