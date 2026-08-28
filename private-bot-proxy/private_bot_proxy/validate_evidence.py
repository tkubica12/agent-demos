from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def validate(schema_path: Path, report_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    validate(arguments.schema, arguments.report)
