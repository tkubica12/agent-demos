from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def https_url(value: str, field: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute HTTPS URL.")
    return value


def build_publish_request(
    config: dict[str, Any],
    *,
    agent_guid: str,
    blueprint_client_id: str,
    subscription_id: str,
    agent_name: str,
    agent_version: str,
) -> dict[str, Any]:
    publication = config["publication"]
    template = config["agentic_user_template"]
    return {
        "agentGuid": agent_guid,
        "botId": blueprint_client_id,
        "publishAsDigitalWorker": True,
        "appPublishScope": publication["publish_scope"],
        "subscriptionId": subscription_id,
        "agentName": agent_name,
        "appVersion": f"{publication['version_prefix']}.{agent_version}",
        "shortDescription": publication["short_description"],
        "fullDescription": publication["full_description"],
        "developerName": publication["developer_name"],
        "developerWebsiteUrl": https_url(
            publication["developer_website_url"], "developer_website_url"
        ),
        "privacyUrl": https_url(publication["privacy_url"], "privacy_url"),
        "termsOfUseUrl": https_url(
            publication["terms_of_use_url"], "terms_of_use_url"
        ),
        "useAgenticUserTemplate": True,
        "agenticUserTemplate": {
            "Id": template["id"],
            "File": template["file"],
            "SchemaVersion": template["schema_version"],
            "AgentIdentityBlueprintId": blueprint_client_id,
            "CommunicationProtocol": template["communication_protocol"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--agent-guid", required=True)
    parser.add_argument("--blueprint-client-id", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--agent-name", required=True)
    parser.add_argument("--agent-version", required=True)
    args = parser.parse_args()

    with args.config.open("rb") as handle:
        config = tomllib.load(handle)
    request = build_publish_request(
        config,
        agent_guid=args.agent_guid,
        blueprint_client_id=args.blueprint_client_id,
        subscription_id=args.subscription_id,
        agent_name=args.agent_name,
        agent_version=args.agent_version,
    )
    print(json.dumps(request, separators=(",", ":")))


if __name__ == "__main__":
    main()
