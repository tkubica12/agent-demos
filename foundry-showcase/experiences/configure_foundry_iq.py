from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from azure.identity import AzureCliCredential


SEARCH_API_VERSION = "2026-05-01-preview"
CONTENT_UNDERSTANDING_API_VERSION = "2025-11-01"
CONNECTION_API_VERSION = "2025-10-01-preview"
INDEX_NAME = "foundry-showcase-documents"
DOCUMENT_SOURCE_NAME = "foundry-showcase-documents"
WEB_SOURCE_NAME = "foundry-showcase-web"
KNOWLEDGE_BASE_NAME = "foundry-showcase-knowledge"
CONNECTION_NAME = "foundry-showcase-knowledge"
ANALYZER_NAME = "prebuilt-documentSearch"


def account_name_from_project_endpoint(project_endpoint: str) -> str:
    host = urlparse(project_endpoint).hostname or ""
    suffix = ".services.ai.azure.com"
    if not host.endswith(suffix):
        raise ValueError("The project endpoint does not use the expected Foundry host.")
    return host[: -len(suffix)]


def project_resource_id(
    subscription_id: str,
    resource_group: str,
    account_name: str,
    project_name: str,
) -> str:
    return (
        f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
        f"/providers/Microsoft.CognitiveServices/accounts/{account_name}"
        f"/projects/{project_name}"
    )


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    token: str,
    *,
    body: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    response = client.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {url} failed: {response.status_code} {response.text}")
    return response.json() if response.content else {}


def poll_operation(
    client: httpx.Client,
    operation_url: str,
    token: str,
    *,
    timeout_seconds: int = 900,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        payload = request_json(client, "GET", operation_url, token)
        status = str(payload.get("status", "")).lower()
        if status in {"succeeded", "completed"}:
            return payload
        if status in {"failed", "canceled", "cancelled"}:
            raise RuntimeError(f"Operation failed: {json.dumps(payload, default=str)}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Operation did not complete: {operation_url}")
        time.sleep(5)


def ensure_analyzer(
    client: httpx.Client,
    content_endpoint: str,
    token: str,
) -> dict[str, Any]:
    return request_json(
        client,
        "GET",
        (
            f"{content_endpoint}/contentunderstanding/analyzers/{ANALYZER_NAME}"
            f"?api-version={CONTENT_UNDERSTANDING_API_VERSION}"
        ),
        token,
    )


def ensure_content_defaults(
    client: httpx.Client,
    content_endpoint: str,
    token: str,
    completion_deployment: str,
    embedding_deployment: str,
) -> dict[str, Any]:
    url = (
        f"{content_endpoint}/contentunderstanding/defaults"
        f"?api-version={CONTENT_UNDERSTANDING_API_VERSION}"
    )
    current_response = client.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    if current_response.status_code == 400:
        error = current_response.json().get("error", {}).get("innererror", {})
        if error.get("code") != "DefaultsNotSet":
            raise RuntimeError(
                f"Content Understanding defaults read failed: "
                f"{current_response.status_code} {current_response.text}"
            )
        current = {}
    elif current_response.status_code >= 400:
        raise RuntimeError(
            f"Content Understanding defaults read failed: "
            f"{current_response.status_code} {current_response.text}"
        )
    else:
        current = current_response.json()
    model_deployments = dict(current.get("modelDeployments", {}))
    model_deployments.update(
        {
            "gpt-5.2": completion_deployment,
            "text-embedding-3-large": embedding_deployment,
            "prebuilt-analyzer-completion": completion_deployment,
            "prebuilt-analyzer-completion-mini": completion_deployment,
            "prebuilt-analyzer-embedding": embedding_deployment,
        }
    )
    response = client.patch(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json",
        },
        json={"modelDeployments": model_deployments},
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Content Understanding defaults failed: "
            f"{response.status_code} {response.text}"
        )
    return response.json()


def analyze_pdf(
    client: httpx.Client,
    content_endpoint: str,
    token: str,
    path: Path,
) -> dict[str, Any]:
    response = client.post(
        (
            f"{content_endpoint}/contentunderstanding/analyzers/{ANALYZER_NAME}"
            f":analyzeBinary?api-version={CONTENT_UNDERSTANDING_API_VERSION}"
        ),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/pdf",
        },
        content=path.read_bytes(),
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Analysis failed for {path.name}: {response.status_code} {response.text}")
    operation_url = response.headers.get("operation-location")
    if not operation_url:
        raise RuntimeError(f"Analysis response for {path.name} had no operation-location.")
    return poll_operation(client, operation_url, token)


def content_markdown(payload: dict[str, Any]) -> str:
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"Content Understanding result is missing: {payload}")
    contents = result.get("contents")
    if not isinstance(contents, list) or not contents:
        raise RuntimeError(f"Content Understanding returned no contents: {payload}")
    markdown_parts = [
        item.get("markdown") or item.get("content")
        for item in contents
        if isinstance(item, dict)
    ]
    markdown = "\n\n".join(
        part.strip() for part in markdown_parts if isinstance(part, str) and part.strip()
    )
    if not markdown:
        raise RuntimeError(f"Content Understanding returned no markdown: {payload}")
    return markdown


def create_index(
    client: httpx.Client,
    search_endpoint: str,
    token: str,
) -> dict[str, Any]:
    return request_json(
        client,
        "PUT",
        f"{search_endpoint}/indexes/{INDEX_NAME}?api-version={SEARCH_API_VERSION}",
        token,
        body={
            "name": INDEX_NAME,
            "description": (
                "Content Understanding output from rich Foundry Showcase PDF documents."
            ),
            "fields": [
                {
                    "name": "id",
                    "type": "Edm.String",
                    "key": True,
                    "filterable": True,
                    "retrievable": True,
                },
                {
                    "name": "title",
                    "type": "Edm.String",
                    "searchable": True,
                    "retrievable": True,
                },
                {
                    "name": "content",
                    "type": "Edm.String",
                    "searchable": True,
                    "retrievable": True,
                },
                {
                    "name": "source_file",
                    "type": "Edm.String",
                    "searchable": True,
                    "filterable": True,
                    "retrievable": True,
                },
            ],
            "semantic": {
                "defaultConfiguration": "foundry-showcase-semantic",
                "configurations": [
                    {
                        "name": "foundry-showcase-semantic",
                        "prioritizedFields": {
                            "titleField": {"fieldName": "title"},
                            "prioritizedContentFields": [{"fieldName": "content"}],
                            "prioritizedKeywordsFields": [
                                {"fieldName": "source_file"}
                            ],
                        },
                    }
                ],
            },
        },
    )


def upload_documents(
    client: httpx.Client,
    search_endpoint: str,
    token: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    return request_json(
        client,
        "POST",
        (
            f"{search_endpoint}/indexes/{INDEX_NAME}/docs/index"
            f"?api-version={SEARCH_API_VERSION}"
        ),
        token,
        body={
            "value": [
                {"@search.action": "mergeOrUpload", **document}
                for document in documents
            ]
        },
    )


def create_knowledge_sources_and_base(
    client: httpx.Client,
    search_endpoint: str,
    token: str,
    openai_endpoint: str,
) -> dict[str, Any]:
    document_source = request_json(
        client,
        "PUT",
        (
            f"{search_endpoint}/knowledgesources/{DOCUMENT_SOURCE_NAME}"
            f"?api-version={SEARCH_API_VERSION}"
        ),
        token,
        body={
            "name": DOCUMENT_SOURCE_NAME,
            "kind": "searchIndex",
            "description": "Rich support operations PDFs analyzed with Content Understanding.",
            "searchIndexParameters": {
                "searchIndexName": INDEX_NAME,
                "semanticConfigurationName": "foundry-showcase-semantic",
                "sourceDataFields": [
                    {"name": "id"},
                    {"name": "title"},
                    {"name": "content"},
                    {"name": "source_file"},
                ],
            },
        },
    )
    web_source = request_json(
        client,
        "PUT",
        (
            f"{search_endpoint}/knowledgesources/{WEB_SOURCE_NAME}"
            f"?api-version={SEARCH_API_VERSION}"
        ),
        token,
        body={
            "name": WEB_SOURCE_NAME,
            "kind": "web",
            "description": "Current public web information through Foundry IQ.",
            "webParameters": {},
        },
    )
    knowledge_base = request_json(
        client,
        "PUT",
        (
            f"{search_endpoint}/knowledgebases/{KNOWLEDGE_BASE_NAME}"
            f"?api-version={SEARCH_API_VERSION}"
        ),
        token,
        body={
            "name": KNOWLEDGE_BASE_NAME,
            "description": (
                "Foundry Showcase knowledge base combining rich PDF content and web search."
            ),
            "retrievalInstructions": (
                "Use foundry-showcase-documents for Northwind support operations, "
                "metrics, architecture, governance, and procedures. Use "
                "foundry-showcase-web only for current public information. Cite sources."
            ),
            "knowledgeSources": [
                {"name": DOCUMENT_SOURCE_NAME},
                {"name": WEB_SOURCE_NAME},
            ],
            "models": [
                {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": openai_endpoint,
                        "deploymentId": "gpt-5.4-mini",
                        "modelName": "gpt-5.4-mini",
                    },
                }
            ],
            "retrievalReasoningEffort": {"kind": "medium"},
        },
    )
    return {
        "documentSource": document_source,
        "webSource": web_source,
        "knowledgeBase": knowledge_base,
    }


def create_project_connection(
    client: httpx.Client,
    management_token: str,
    project_id: str,
    mcp_url: str,
) -> dict[str, Any]:
    return request_json(
        client,
        "PUT",
        (
            f"https://management.azure.com{project_id}/connections/{CONNECTION_NAME}"
            f"?api-version={CONNECTION_API_VERSION}"
        ),
        management_token,
        body={
            "name": CONNECTION_NAME,
            "type": "Microsoft.MachineLearningServices/workspaces/connections",
            "properties": {
                "authType": "ProjectManagedIdentity",
                "category": "RemoteTool",
                "target": mcp_url,
                "isSharedToAll": True,
                "audience": "https://search.azure.com/",
                "metadata": {"ApiType": "Azure"},
            },
        },
    )


def retrieve(
    client: httpx.Client,
    search_endpoint: str,
    token: str,
) -> dict[str, Any]:
    return request_json(
        client,
        "POST",
        (
            f"{search_endpoint}/knowledgebases/{KNOWLEDGE_BASE_NAME}/retrieve"
            f"?api-version={SEARCH_API_VERSION}"
        ),
        token,
        body={
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Answer from the Foundry Showcase documents and cite sources.",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "How much did the unresolved support backlog decrease, "
                                "and what control sequence governs case updates?"
                            ),
                        }
                    ],
                },
            ]
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-endpoint",
        default=os.getenv("FOUNDRY_PROJECT_ENDPOINT"),
    )
    parser.add_argument("--project-name", default="tomaskubica-foundry-project")
    parser.add_argument("--resource-group", default="ai-services")
    parser.add_argument("--search-endpoint", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument(
        "--completion-deployment",
        default="foundry-showcase-gpt-5.2",
    )
    parser.add_argument(
        "--embedding-deployment",
        default="text-embedding-3-large",
    )
    parser.add_argument(
        "--assets-dir",
        type=Path,
        default=Path(__file__).parent / "generated",
    )
    args = parser.parse_args()
    if not args.project_endpoint:
        parser.error("--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required.")
    pdfs = sorted(args.assets_dir.glob("*.pdf"))
    if not pdfs:
        parser.error(f"No PDFs found in {args.assets_dir}.")

    credential = AzureCliCredential(process_timeout=60)
    search_token = credential.get_token("https://search.azure.com/.default").token
    cognitive_token = credential.get_token(
        "https://cognitiveservices.azure.com/.default"
    ).token
    management_token = credential.get_token(
        "https://management.azure.com/.default"
    ).token
    account_name = account_name_from_project_endpoint(args.project_endpoint)
    content_endpoint = f"https://{account_name}.cognitiveservices.azure.com"
    openai_endpoint = f"https://{account_name}.openai.azure.com"
    project_id = project_resource_id(
        args.subscription_id,
        args.resource_group,
        account_name,
        args.project_name,
    )
    mcp_url = (
        f"{args.search_endpoint}/knowledgebases/{KNOWLEDGE_BASE_NAME}/mcp"
        f"?api-version={SEARCH_API_VERSION}"
    )

    with httpx.Client(timeout=300) as client:
        analyzer = ensure_analyzer(
            client,
            content_endpoint,
            cognitive_token,
        )
        defaults = ensure_content_defaults(
            client,
            content_endpoint,
            cognitive_token,
            args.completion_deployment,
            args.embedding_deployment,
        )
        analyses = {
            path.name: analyze_pdf(client, content_endpoint, cognitive_token, path)
            for path in pdfs
        }
        documents = [
            {
                "id": f"document-{index}",
                "title": path.stem.replace("-", " ").title(),
                "content": content_markdown(analyses[path.name]),
                "source_file": path.name,
            }
            for index, path in enumerate(pdfs, start=1)
        ]
        index = create_index(client, args.search_endpoint, search_token)
        upload = upload_documents(
            client,
            args.search_endpoint,
            search_token,
            documents,
        )
        knowledge = create_knowledge_sources_and_base(
            client,
            args.search_endpoint,
            search_token,
            openai_endpoint,
        )
        connection = create_project_connection(
            client,
            management_token,
            project_id,
            mcp_url,
        )
        retrieval = retrieve(client, args.search_endpoint, search_token)

    output = {
        "analyzer": analyzer,
        "defaults": defaults,
        "analyses": analyses,
        "index": index,
        "upload": upload,
        "knowledge": knowledge,
        "connection": connection,
        "mcpUrl": mcp_url,
        "retrieval": retrieval,
    }
    output_path = args.assets_dir / "foundry-iq-result.json"
    output_path.write_text(
        json.dumps(output, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "documents": [document["source_file"] for document in documents],
                "index": INDEX_NAME,
                "knowledgeSources": [DOCUMENT_SOURCE_NAME, WEB_SOURCE_NAME],
                "knowledgeBase": KNOWLEDGE_BASE_NAME,
                "connection": CONNECTION_NAME,
                "mcpUrl": mcp_url,
                "retrieval": retrieval,
                "resultFile": str(output_path),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
