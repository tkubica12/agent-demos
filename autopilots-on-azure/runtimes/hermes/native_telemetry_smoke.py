"""Validate native image dependency additions without replacing the Hermes base."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from packaging.utils import canonicalize_name


CORE_PACKAGES = frozenset({
    "opentelemetry-api", "opentelemetry-sdk", "opentelemetry-semantic-conventions", "packaging",
})


def installed_versions() -> dict[str, str]:
    return {
        canonicalize_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
    }


def verify_base_versions(before: dict[str, str], after: dict[str, str]) -> None:
    if not CORE_PACKAGES.issubset(before):
        raise RuntimeError("Native Hermes base is missing required telemetry packages.")
    changed = sorted(name for name, version in before.items() if after.get(name) != version)
    if changed:
        raise RuntimeError("Native dependency installation changed base packages: " + ", ".join(changed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("capture", "verify"))
    parser.add_argument("snapshot", type=Path)
    arguments = parser.parse_args()
    if arguments.phase == "capture":
        versions = installed_versions()
        verify_base_versions(versions, versions)
        arguments.snapshot.write_text(json.dumps(versions, sort_keys=True), encoding="utf-8")
        return

    before = json.loads(arguments.snapshot.read_text(encoding="utf-8"))
    verify_base_versions(before, installed_versions())
    import telemetry
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    telemetry.configure_telemetry("hermes-gateway")
    if not HTTPXClientInstrumentor().is_instrumented_by_opentelemetry:
        raise RuntimeError("Native Hermes HTTPX instrumentation did not activate.")


if __name__ == "__main__":
    main()
