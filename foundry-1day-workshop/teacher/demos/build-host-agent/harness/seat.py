"""Resolve the environment contract this lab needs, from whichever source is present.

Your seat holds a Foundry project, a set of shared model references, a local model
deployment and a responsible AI policy. The lab needs to know all four, and where those
values live depends on how your environment was prepared. Rather than hardcode one
answer, this module looks in three places in order: an explicit file you pass, then
environment variables, then the well-known seat file written when the machine was built.

The first source that supplies a value wins, so you can override any single field on the
command line without rewriting the rest. If nothing supplies a required value the error
names the field and every place that was searched, because "endpoint is None" three
frames deep is a worse start to a lab than a sentence telling you what to set.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

SEAT_ENV_FILE = Path("/etc/workshop/seat.env")


def _seat_env_file() -> Path:
    """Where the seat file lives, overridable without editing the harness.

    The default is the location the seat image is expected to write. An override exists
    because that location is a property of the machine image rather than of this lab, and
    a facilitator should be able to correct it in the room without a code change.
    """
    override = os.environ.get(f"{ENV_PREFIX}SEAT_FILE")
    return Path(override) if override else SEAT_ENV_FILE

ENV_PREFIX = "WORKSHOP_"

#: The three models any attendee pair is drawn from. Kimi-K2.6 is deployed and reachable
#: but is deliberately excluded: on this scored task at room concurrency it exhausted its
#: output budget without finishing on most calls, so it belongs in the discussion rather
#: than in someone's hands as a broken exercise.
PREPARED_SLUGS = ("gpt-5.6-luna", "gpt-5.6-terra", "Mistral-Large-3")

EXCLUDED_SLUGS = ("Kimi-K2.6",)

#: Vendor per prepared model, keyed by slug in lower case.
#:
#: The seat file and the environment variable both carry bare model references with no
#: vendor attached, and pair assignment guarantees that no attendee compares two models
#: from the same vendor. Without this map that guarantee would hold for a structured
#: manifest and silently collapse on the seat path, which is the one attendees actually
#: use. The prepared list is curated here anyway, so their vendors are known here too.
KNOWN_VENDORS = {
    "gpt-5.6-luna": "Microsoft / OpenAI",
    "gpt-5.6-terra": "Microsoft / OpenAI",
    "mistral-large-3": "Mistral AI",
    "kimi-k2.6": "Moonshot AI",
}

UNSPECIFIED_VENDOR = "unspecified"


class ContractError(RuntimeError):
    """A required environment value could not be resolved from any source."""


@dataclass(frozen=True)
class SharedModel:
    """One shared model reference and the vendor it comes from."""

    reference: str
    vendor: str

    @property
    def slug(self) -> str:
        """The bare model name, without the connection prefix."""
        return self.reference.split("/")[-1]


@dataclass(frozen=True)
class SeatContract:
    """Everything the lab needs to know about the environment it is running in."""

    seat_id: str
    project_endpoint: str
    shared_models: tuple[SharedModel, ...]
    local_model: str
    rai_policy_name: str | None
    rai_policy_id: str | None

    @property
    def assigned_models(self) -> tuple[SharedModel, ...]:
        """The two prepared models this seat compares.

        Every pair spans two vendors. That is the whole point of the comparison: a
        same-vendor pair gives the attendee nothing to weigh, and on this task the two
        Microsoft/OpenAI models both pass the contract, so such a seat would also never
        see a failure and could not do the step that asks them to judge one. Pairs rotate
        across the valid cross-vendor combinations so the room still covers every prepared
        model between it, which is what makes the closing board worth filling in.
        """
        prepared = [model for model in self.shared_models if model.slug in PREPARED_SLUGS]
        if len(prepared) < 2:
            raise ContractError(
                "at least two prepared models are required to run the comparison; "
                f"found {[model.slug for model in prepared]}. Prepared models are "
                f"{', '.join(PREPARED_SLUGS)}."
            )

        cross_vendor = [
            pair
            for pair in itertools.combinations(prepared, 2)
            if pair[0].vendor != pair[1].vendor
            and UNSPECIFIED_VENDOR not in (pair[0].vendor, pair[1].vendor)
        ]
        if not cross_vendor:
            # Degrading silently to a same-vendor pair would leave the attendee with
            # nothing to weigh while the guide promises otherwise, so stop here instead.
            described = ", ".join(f"{model.slug} ({model.vendor})" for model in prepared)
            raise ContractError(
                "no cross-vendor pair can be built from the prepared models, so this seat "
                "cannot run the comparison the lab is built around. Prepared models "
                f"resolved as: {described}. Every pair must span two vendors; check that "
                "the seat contract lists the expected models and that any vendor it "
                "declares is correct."
            )

        return cross_vendor[_seat_number(self.seat_id) % len(cross_vendor)]

    def model_by_slug(self, slug: str) -> SharedModel:
        for model in self.shared_models:
            if model.slug.lower() == slug.lower():
                return model
        known = ", ".join(model.slug for model in self.shared_models)
        raise ContractError(f"no shared model named {slug!r}; this seat has {known}")


def _seat_number(seat_id: str) -> int:
    """Extract the trailing number from a seat identifier such as ``seat-001``."""
    match = re.search(r"(\d+)\s*$", seat_id)
    return int(match.group(1)) if match else 0


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE file, ignoring blanks and comments."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_document(path: Path) -> dict:
    """Load a contract file that may be JSON or YAML.

    The platform publishes seat manifests as YAML, but JSON is what survives being piped
    through ``jq`` on a machine that may not have a YAML parser installed. Both are
    accepted so neither side has to convert.
    """
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml
        except ModuleNotFoundError as error:  # pragma: no cover - depends on environment
            raise ContractError(
                f"{path} is not JSON, and PyYAML is not installed to read it as YAML. "
                "Convert it with `python -c \"import sys,yaml,json;"
                'json.dump(yaml.safe_load(open(sys.argv[1])), sys.stdout)" file.yaml > file.json`.'
            ) from error
        document = yaml.safe_load(text)
    if not isinstance(document, dict):
        raise ContractError(f"{path} does not contain a mapping of contract values")
    return document


def _shared_models_from(raw: object) -> tuple[SharedModel, ...]:
    """Build the model list from either structured entries or a plain reference list."""
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(raw, list):
        return ()
    models: list[SharedModel] = []
    for entry in raw:
        if isinstance(entry, str):
            models.append(SharedModel(reference=entry, vendor=_vendor_for(entry)))
        elif isinstance(entry, dict) and entry.get("reference"):
            reference = str(entry["reference"])
            declared = str(entry.get("vendor") or "").strip()
            models.append(
                SharedModel(
                    reference=reference,
                    vendor=declared or _vendor_for(reference),
                )
            )
    return tuple(models)


def _vendor_for(reference: str) -> str:
    """Look up the vendor for a prepared model reference that arrived without one."""
    return KNOWN_VENDORS.get(reference.split("/")[-1].lower(), UNSPECIFIED_VENDOR)


def _endpoint_from(document: dict) -> str | None:
    """Derive the project endpoint from account and project names when not given directly."""
    for key in ("project_endpoint", "foundry_project_endpoint"):
        if document.get(key):
            return str(document[key])
    account = document.get("foundry_account")
    project = document.get("foundry_project")
    if account and project:
        return f"https://{account}.services.ai.azure.com/api/projects/{project}"
    return None


def _sources(contract_path: Path | None) -> tuple[dict, dict, dict]:
    file_document = _load_document(contract_path) if contract_path else {}

    # The platform publishes the seat's chat deployment as `memory_chat_model`. Normalise
    # it here rather than treating it as a last-resort fallback, otherwise an unrelated
    # environment variable would outrank the contract file that was passed explicitly.
    if not file_document.get("local_model") and file_document.get("memory_chat_model"):
        file_document = dict(file_document, local_model=file_document["memory_chat_model"])
    if not file_document.get("project_endpoint"):
        derived = _endpoint_from(file_document)
        if derived:
            file_document = dict(file_document, project_endpoint=derived)

    environment = {
        "seat_id": os.environ.get(f"{ENV_PREFIX}SEAT_ID") or os.environ.get("SEAT_ID"),
        "project_endpoint": os.environ.get(f"{ENV_PREFIX}PROJECT_ENDPOINT"),
        "shared_models": os.environ.get(f"{ENV_PREFIX}SHARED_MODELS"),
        "local_model": os.environ.get(f"{ENV_PREFIX}LOCAL_MODEL"),
        "rai_policy_name": os.environ.get(f"{ENV_PREFIX}RAI_POLICY_NAME"),
        "rai_policy_id": os.environ.get(f"{ENV_PREFIX}RAI_POLICY_ID"),
    }

    seat_file = _read_env_file(_seat_env_file())
    from_file = {
        "seat_id": seat_file.get("SEAT_ID"),
        "project_endpoint": seat_file.get(f"{ENV_PREFIX}PROJECT_ENDPOINT")
        or seat_file.get("PROJECT_ENDPOINT"),
        "shared_models": seat_file.get(f"{ENV_PREFIX}SHARED_MODELS")
        or seat_file.get("SHARED_MODELS"),
        "local_model": seat_file.get(f"{ENV_PREFIX}LOCAL_MODEL")
        or seat_file.get("MODEL_DEPLOYMENT"),
        "rai_policy_name": seat_file.get(f"{ENV_PREFIX}RAI_POLICY_NAME")
        or seat_file.get("RAI_POLICY_NAME"),
        "rai_policy_id": seat_file.get(f"{ENV_PREFIX}RAI_POLICY_ID")
        or seat_file.get("RAI_POLICY_ID"),
    }
    return file_document, environment, from_file


def load_contract(
    contract_path: Path | None = None, overrides: dict[str, str | None] | None = None
) -> SeatContract:
    """Resolve the seat contract from overrides, then a file, then environment, then disk.

    Ordering matters more than it looks. An explicit command-line value must win so you
    can point the lab at a different project without editing anything, and the well-known
    file must come last so a stale machine default never silently overrides what you
    asked for.
    """
    file_document, environment, from_file = _sources(contract_path)
    overrides = {key: value for key, value in (overrides or {}).items() if value}

    def resolve(field: str) -> object:
        for source in (overrides, file_document, environment, from_file):
            value = source.get(field)
            if value:
                return value
        return None

    endpoint = resolve("project_endpoint")

    shared = _shared_models_from(
        overrides.get("shared_models")
        or file_document.get("shared_models")
        or environment["shared_models"]
        or from_file["shared_models"]
        or []
    )

    seat_id = resolve("seat_id")
    local_model = resolve("local_model")

    missing: list[str] = []
    if not endpoint:
        missing.append("project_endpoint (or foundry_account plus foundry_project)")
    if not shared:
        missing.append("shared_models")
    if not local_model:
        missing.append("local_model")

    if missing:
        seat_file_path = _seat_env_file()
        found = "present" if seat_file_path.is_file() else "not found"
        searched = [
            f"--contract file ({contract_path})" if contract_path else "--contract file (not given)",
            f"environment variables prefixed {ENV_PREFIX}",
            f"seat file {seat_file_path} ({found})",
        ]
        raise ContractError(
            "could not resolve: "
            + ", ".join(missing)
            + ". Searched, in order: "
            + "; ".join(searched)
            + ". Ask your facilitator for the seat contract file and pass it with --contract."
        )

    return SeatContract(
        seat_id=str(seat_id or "seat-unknown"),
        project_endpoint=str(endpoint),
        shared_models=shared,
        local_model=str(local_model),
        rai_policy_name=str(resolve("rai_policy_name")) if resolve("rai_policy_name") else None,
        rai_policy_id=str(resolve("rai_policy_id")) if resolve("rai_policy_id") else None,
    )


def describe(contract: SeatContract) -> str:
    """Render the resolved contract for the preflight check."""
    lines = [
        f"  seat            {contract.seat_id}",
        f"  project         {contract.project_endpoint}",
        f"  local model     {contract.local_model}",
        f"  policy name     {contract.rai_policy_name or 'not resolved'}",
        f"  policy id       {'resolved' if contract.rai_policy_id else 'not resolved'}",
        "  shared models",
    ]
    for model in contract.shared_models:
        note = ""
        if model.slug in EXCLUDED_SLUGS:
            note = "  (deployed, deliberately not assigned)"
        lines.append(f"    {model.reference:<40} {model.vendor}{note}")
    assigned = contract.assigned_models
    lines.append(f"  assigned pair   {assigned[0].slug} and {assigned[1].slug}")
    return "\n".join(lines)
