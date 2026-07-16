from __future__ import annotations

import json
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


EVALUATOR_NAME = "step09-optimizer-training"
EVALUATOR_VERSION = "1"
RUBRIC_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluators"
    / EVALUATOR_NAME
    / "rubric_dimensions.json"
)


def main() -> None:
    endpoint = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    if not endpoint:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT must be set.")

    expected_ids = {
        dimension["id"]
        for dimension in json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
    }
    credential = DefaultAzureCredential(exclude_managed_identity_credential=True)
    with AIProjectClient(endpoint=endpoint, credential=credential) as client:
        evaluator = client.beta.evaluators.get_version(
            EVALUATOR_NAME,
            EVALUATOR_VERSION,
        )

    evaluator_data = evaluator.as_dict()
    metadata = evaluator_data.get("metadata", {})
    if metadata.get("generation_status") != "succeeded":
        raise RuntimeError(
            f"{EVALUATOR_NAME}:{EVALUATOR_VERSION} is not catalog-generated."
        )
    actual_ids = {
        dimension["id"]
        for dimension in evaluator_data["definition"]["dimensions"]
    }
    if actual_ids != expected_ids:
        raise RuntimeError(
            f"{EVALUATOR_NAME}:{EVALUATOR_VERSION} differs from the local rubric."
        )
    print(
        json.dumps(
            {
                "name": evaluator.name,
                "version": evaluator.version,
                "dimensions": len(actual_ids),
                "generationStatus": metadata["generation_status"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
