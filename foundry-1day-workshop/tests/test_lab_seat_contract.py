"""Tests for the environment contract the Chapter 2 lab resolves before it runs.

The lab must work whether the seat publishes its contract as a file, as environment
variables, or as the well-known file written when the machine was built. These tests pin
the resolution order and the failure message, because an attendee who cannot resolve the
environment has no lab at all, and a silent wrong answer is worse than a loud missing one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "teacher" / "demos" / "build-host-agent" / "harness"))

import seat  # noqa: E402

MANIFEST = {
    "seat_id": "seat-001",
    "foundry_account": "labtest-seat-001-foundry-070168",
    "foundry_project": "labtest-seat-001-project",
    "memory_chat_model": "gpt-5.2",
    "rai_policy_name": "foundryws-guardrails-strict",
    "shared_models": [
        {"reference": "shared-ai-gateway/gpt-5.6-luna", "vendor": "Microsoft / OpenAI"},
        {"reference": "shared-ai-gateway/gpt-5.6-terra", "vendor": "Microsoft / OpenAI"},
        {"reference": "shared-ai-gateway/Mistral-Large-3", "vendor": "Mistral AI"},
        {"reference": "shared-ai-gateway/Kimi-K2.6", "vendor": "Moonshot AI"},
    ],
}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Keep every test off the real machine's seat file and environment."""
    monkeypatch.setattr(seat, "SEAT_ENV_FILE", tmp_path / "absent.env")
    for key in list(os.environ):
        if key.startswith(seat.ENV_PREFIX) or key == "SEAT_ID":
            monkeypatch.delenv(key, raising=False)


def write_manifest(tmp_path: Path, document: dict | None = None) -> Path:
    path = tmp_path / "seat.json"
    path.write_text(json.dumps(document or MANIFEST), encoding="utf-8")
    return path


def test_endpoint_is_derived_from_account_and_project(tmp_path) -> None:
    contract = seat.load_contract(write_manifest(tmp_path))
    assert contract.project_endpoint == (
        "https://labtest-seat-001-foundry-070168.services.ai.azure.com"
        "/api/projects/labtest-seat-001-project"
    )
    assert contract.local_model == "gpt-5.2"


def test_an_explicit_override_beats_the_contract_file(tmp_path) -> None:
    contract = seat.load_contract(
        write_manifest(tmp_path), {"project_endpoint": "https://example/api/projects/p"}
    )
    assert contract.project_endpoint == "https://example/api/projects/p"


def test_the_contract_file_beats_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(f"{seat.ENV_PREFIX}LOCAL_MODEL", "from-environment")
    contract = seat.load_contract(write_manifest(tmp_path))
    assert contract.local_model == "gpt-5.2"


def test_the_environment_is_used_when_no_file_is_given(monkeypatch) -> None:
    monkeypatch.setenv("SEAT_ID", "seat-007")
    monkeypatch.setenv(f"{seat.ENV_PREFIX}PROJECT_ENDPOINT", "https://example/api/projects/p")
    monkeypatch.setenv(f"{seat.ENV_PREFIX}LOCAL_MODEL", "gpt-5.2")
    monkeypatch.setenv(
        f"{seat.ENV_PREFIX}SHARED_MODELS",
        "shared-ai-gateway/gpt-5.6-luna,shared-ai-gateway/Mistral-Large-3",
    )
    contract = seat.load_contract(None)
    assert contract.seat_id == "seat-007"
    assert [model.slug for model in contract.shared_models] == ["gpt-5.6-luna", "Mistral-Large-3"]


def test_the_well_known_seat_file_is_the_last_resort(tmp_path, monkeypatch) -> None:
    seat_file = tmp_path / "seat.env"
    seat_file.write_text(
        "SEAT_ID=seat-003\n"
        "WORKSHOP_PROJECT_ENDPOINT=https://example/api/projects/p\n"
        "MODEL_DEPLOYMENT=gpt-5.2\n"
        'WORKSHOP_SHARED_MODELS="shared-ai-gateway/gpt-5.6-luna,shared-ai-gateway/gpt-5.6-terra"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(seat, "SEAT_ENV_FILE", seat_file)
    contract = seat.load_contract(None)
    assert contract.seat_id == "seat-003"
    assert contract.local_model == "gpt-5.2"


def test_a_missing_value_names_the_field_and_every_source_searched(tmp_path) -> None:
    path = write_manifest(tmp_path, {"seat_id": "seat-001"})
    with pytest.raises(seat.ContractError) as error:
        seat.load_contract(path)
    message = str(error.value)
    assert "project_endpoint" in message
    assert "shared_models" in message
    assert str(path) in message
    assert str(seat.SEAT_ENV_FILE) in message


def test_pairs_rotate_so_the_room_covers_every_prepared_vendor(tmp_path) -> None:
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for number in range(1, 7):
        document = dict(MANIFEST, seat_id=f"seat-{number:03d}")
        contract = seat.load_contract(write_manifest(tmp_path, document))
        assigned = contract.assigned_models
        pairs.append((assigned[0].slug, assigned[1].slug))
        seen.update(model.slug for model in assigned)
    assert seen == set(seat.PREPARED_SLUGS)
    assert len({pair for pair in pairs}) > 1


def test_every_seat_compares_two_vendors(tmp_path) -> None:
    """No seat may draw two models from the same vendor.

    A same-vendor pair leaves the attendee nothing to weigh, and on this task both
    Microsoft/OpenAI models pass the contract, so that seat would also never see a
    failure and could not do the step that asks them to judge one.
    """
    for number in range(1, 61):
        document = dict(MANIFEST, seat_id=f"seat-{number:03d}")
        contract = seat.load_contract(write_manifest(tmp_path, document))
        first, second = contract.assigned_models
        assert first.vendor != second.vendor, f"seat-{number:03d} drew two {first.vendor} models"


def test_bare_references_still_produce_a_cross_vendor_pair(tmp_path) -> None:
    """The seat file and the environment variable carry references with no vendor.

    That is the path a real attendee machine uses, so the cross-vendor guarantee has to
    survive it rather than holding only for a structured manifest.
    """
    document = dict(
        MANIFEST,
        shared_models=[
            "shared-ai-gateway/gpt-5.6-luna",
            "shared-ai-gateway/gpt-5.6-terra",
            "shared-ai-gateway/Mistral-Large-3",
        ],
    )
    for number in range(1, 13):
        contract = seat.load_contract(
            write_manifest(tmp_path, dict(document, seat_id=f"seat-{number:03d}"))
        )
        first, second = contract.assigned_models
        assert first.vendor != second.vendor
        assert seat.UNSPECIFIED_VENDOR not in (first.vendor, second.vendor)


def test_a_room_of_one_vendor_refuses_to_assign_a_pair(tmp_path) -> None:
    """Silently degrading would contradict what the guide promises the attendee."""
    document = dict(
        MANIFEST,
        shared_models=[
            {"reference": "shared-ai-gateway/gpt-5.6-luna", "vendor": "Microsoft / OpenAI"},
            {"reference": "shared-ai-gateway/gpt-5.6-terra", "vendor": "Microsoft / OpenAI"},
        ],
    )
    contract = seat.load_contract(write_manifest(tmp_path, document))
    with pytest.raises(seat.ContractError, match="no cross-vendor pair"):
        _ = contract.assigned_models


def test_the_excluded_model_is_never_assigned(tmp_path) -> None:
    for number in range(1, 13):
        document = dict(MANIFEST, seat_id=f"seat-{number:03d}")
        contract = seat.load_contract(write_manifest(tmp_path, document))
        assert all(model.slug not in seat.EXCLUDED_SLUGS for model in contract.assigned_models)


def test_a_seat_without_two_prepared_models_fails_clearly(tmp_path) -> None:
    document = dict(
        MANIFEST,
        shared_models=[{"reference": "shared-ai-gateway/Kimi-K2.6", "vendor": "Moonshot AI"}],
    )
    contract = seat.load_contract(write_manifest(tmp_path, document))
    with pytest.raises(seat.ContractError, match="at least two prepared models"):
        _ = contract.assigned_models


def test_describe_marks_the_deployed_but_unassigned_model(tmp_path) -> None:
    contract = seat.load_contract(write_manifest(tmp_path))
    rendered = seat.describe(contract)
    assert "deliberately not assigned" in rendered
    assert "Kimi-K2.6" in rendered


def write_seat_file(path: Path) -> Path:
    path.write_text(
        "SEAT_ID=seat-042\n"
        "WORKSHOP_PROJECT_ENDPOINT=https://x.services.ai.azure.com/api/projects/p\n"
        "WORKSHOP_LOCAL_MODEL=gpt-5.2\n"
        "WORKSHOP_SHARED_MODELS=shared-ai-gateway/gpt-5.6-luna,shared-ai-gateway/Mistral-Large-3\n",
        encoding="utf-8",
    )
    return path


def test_the_seat_file_location_can_be_overridden(tmp_path, monkeypatch) -> None:
    """The seat file belongs to the machine image, not to this lab.

    Its path differs between image builds and operating systems, so a facilitator has to
    be able to correct it in the room without editing the harness.
    """
    elsewhere = write_seat_file(tmp_path / "somewhere-else.env")
    monkeypatch.setenv(f"{seat.ENV_PREFIX}SEAT_FILE", str(elsewhere))
    contract = seat.load_contract(None)
    assert contract.seat_id == "seat-042"
    assert contract.local_model == "gpt-5.2"


def test_the_override_still_yields_a_cross_vendor_pair(tmp_path, monkeypatch) -> None:
    elsewhere = write_seat_file(tmp_path / "somewhere-else.env")
    monkeypatch.setenv(f"{seat.ENV_PREFIX}SEAT_FILE", str(elsewhere))
    first, second = seat.load_contract(None).assigned_models
    assert first.vendor != second.vendor
    assert seat.UNSPECIFIED_VENDOR not in (first.vendor, second.vendor)


def test_the_default_location_is_used_when_no_override_is_set(tmp_path, monkeypatch) -> None:
    default = write_seat_file(tmp_path / "default.env")
    monkeypatch.setattr(seat, "SEAT_ENV_FILE", default)
    assert seat.load_contract(None).seat_id == "seat-042"


def test_a_missing_seat_file_is_named_and_reported_as_missing(tmp_path, monkeypatch) -> None:
    absent = tmp_path / "nowhere" / "seat.env"
    monkeypatch.setenv(f"{seat.ENV_PREFIX}SEAT_FILE", str(absent))
    with pytest.raises(seat.ContractError) as error:
        seat.load_contract(None)
    message = str(error.value)
    assert str(absent) in message, "the reader cannot check a path the error will not name"
    assert "not found" in message
