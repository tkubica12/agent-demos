from __future__ import annotations

import json
import os

from azure.identity import AzureCliCredential

from case_mcp.repository import TableCaseRepository
from case_mcp.sample_data import SAMPLE_CASES


def main() -> None:
    endpoint = os.getenv("CASE_TABLE_ENDPOINT")
    if not endpoint:
        raise RuntimeError("CASE_TABLE_ENDPOINT must be set.")
    repository = TableCaseRepository(endpoint, AzureCliCredential())
    repository.seed(SAMPLE_CASES)
    print(
        json.dumps(
            {
                "endpoint": endpoint,
                "seededCases": [case.case_id for case in SAMPLE_CASES],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
