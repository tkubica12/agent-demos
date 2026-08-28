from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from private_bot_proxy.teams_package import (
    PACKAGE_FILES,
    SCHEMA_PATH,
    build,
    validate_manifest,
    validate_package,
)
from private_bot_proxy.validate_evidence import validate

APP_ID = "22222222-2222-2222-2222-222222222222"
TEAMS_APP_ID = "44444444-4444-4444-4444-444444444444"


def test_package_matches_official_v1_23_schema(tmp_path: Path) -> None:
    build(tmp_path, APP_ID, TEAMS_APP_ID)
    package_path = tmp_path / "private-bot-proxy.zip"
    validate_package(package_path)

    with ZipFile(package_path) as package:
        assert frozenset(package.namelist()) == PACKAGE_FILES
        manifest = json.loads(package.read("manifest.json"))

    assert manifest["$schema"].endswith("/v1.23/MicrosoftTeams.schema.json")
    assert manifest["id"] == TEAMS_APP_ID
    assert manifest["id"] != manifest["bots"][0]["botId"]
    assert manifest["bots"][0]["botId"] == APP_ID
    assert set(manifest["bots"][0]["scopes"]) == {"personal", "groupChat"}
    assert manifest["webApplicationInfo"] == {
        "id": APP_ID,
        "resource": f"api://botid-{APP_ID}",
    }
    assert manifest["validDomains"] == ["token.botframework.com"]


def test_schema_rejects_unsupported_top_level_properties(tmp_path: Path) -> None:
    build(tmp_path, APP_ID, TEAMS_APP_ID)
    with ZipFile(tmp_path / "private-bot-proxy.zip") as package:
        manifest = json.loads(package.read("manifest.json"))
    manifest["packageName"] = "unsupported"

    with pytest.raises(ValueError, match="Additional properties are not allowed"):
        validate_manifest(manifest)


def test_vendored_schema_is_strict_v1_23() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["manifestVersion"]["const"] == "1.23"
    assert "packageName" not in schema["properties"]


def test_experiment_report_template_matches_schema() -> None:
    root = Path(__file__).parents[1]
    validate(
        root / "evidence" / "schema.json",
        root / "evidence" / "report.template.json",
    )
