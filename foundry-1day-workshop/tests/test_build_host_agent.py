from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tomllib
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.ai.projects.models import MemoryStoreDefaultOptions
from azure.core.exceptions import HttpResponseError
from openai import RateLimitError
from httpx import Request, Response

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = ROOT / "teacher" / "demos" / "build-host-agent"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


demo = load_module("chapter2_demo", DEMO_ROOT / "scripts" / "demo.py")
agent = load_module("chapter2_agent", DEMO_ROOT / "agent" / "main.py")


def agent_details(principal_id="agent-principal"):
    return {
        "instance_identity": {
            "principal_id": principal_id,
        }
    }


def test_parse_contract_uses_published_teacher_outputs():
    project_id = (
        "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
        "accounts/teacher/projects/demo"
    )
    contract = demo.parse_contract(
        {
            "tenant_id": "tenant",
            "platform_outputs": {
                "teacher_application_insights_id": "appi",
                "teacher_foundry_id": "foundry",
                "teacher_log_analytics_workspace_id": "law",
                "teacher_memory_chat_model": "memory-chat",
                "teacher_memory_embedding_model": "memory-embedding",
                "teacher_model_reference": "shared-ai-gateway/model",
                "teacher_project_endpoint": "https://example/api/projects/demo",
                "teacher_project_id": project_id,
            },
        }
    )

    assert contract.subscription_id == "sub"
    assert contract.tenant_id == "tenant"
    assert contract.model_reference == "shared-ai-gateway/model"
    assert contract.memory_chat_model == "memory-chat"
    assert contract.memory_embedding_model == "memory-embedding"


def test_parse_contract_rejects_incomplete_platform():
    with pytest.raises(demo.DemoError, match="missing"):
        demo.parse_contract({"tenant_id": "tenant", "platform_outputs": {}})


def test_memory_models_require_platform_contract():
    contract = demo.PlatformContract(
        subscription_id="sub",
        tenant_id="tenant",
        project_endpoint="https://example",
        project_id="/subscriptions/sub/projects/example",
        foundry_id="foundry",
        model_reference="connected/chat",
        memory_chat_model=None,
        memory_embedding_model=None,
        application_insights_id="appi",
        log_analytics_workspace_id="law",
    )

    with pytest.raises(demo.DemoError, match="Memory models are absent"):
        demo._memory_models(contract)


def test_reuse_checks_only_latest_agent_version():
    class Version(dict):
        @property
        def version(self):
            return self["version"]

    newer_mismatch = Version(
        version="2",
        status="active",
        metadata={
            "workshop_owner": demo.OWNER_METADATA,
            "content_sha256": "invalid-experiment",
        },
    )
    class Agents:
        def list_versions(self, _name, *, include_drafts, order, limit):
            assert include_drafts is False
            assert order == "desc"
            assert limit == 1
            return [newer_mismatch]

    project = SimpleNamespace(agents=Agents())

    assert demo._find_reusable_version(project, "agent", "current") is None

    newer_mismatch["metadata"]["content_sha256"] = "current"

    assert demo._find_reusable_version(project, "agent", "current") is newer_mismatch


def test_reuse_accepts_latest_nonnumeric_released_version():
    class Version(dict):
        @property
        def version(self):
            return self["version"]

    released = Version(
        version="release-blue",
        draft=False,
        status="active",
        metadata={
            "workshop_owner": demo.OWNER_METADATA,
            "content_sha256": "current",
        },
    )
    class Agents:
        def list_versions(self, _name, *, include_drafts, order, limit):
            assert include_drafts is False
            assert order == "desc"
            assert limit == 1
            return [released]

    project = SimpleNamespace(agents=Agents())

    assert demo._find_reusable_version(project, "agent", "current") is released


def test_memory_showcase_uses_distinct_scopes_and_synthetic_fact():
    assert demo.MEMORY_SCOPE != demo.MEMORY_ISOLATED_SCOPE
    assert "GREEN-742" in demo.MEMORY_FACT
    assert "STOCK-318" in demo.MEMORY_DISCUSSION_START
    assert "STOCK-318" in demo.MEMORY_DISCUSSION_CLOSE
    assert "GREEN-742" not in demo.MEMORY_RECALL_QUESTION
    assert "STOCK-318" not in demo.MEMORY_RECALL_QUESTION
    assert "STOCK-318" not in demo.MEMORY_SUMMARY_SEARCH_QUERY
    assert "synthetic" in demo.MEMORY_FACT.lower()
    assert "sensitive personal" in demo.MEMORY_INSTRUCTIONS.lower()


def test_memory_response_retries_rate_limit(monkeypatch):
    class Responses:
        attempts = 0

        def create(self, **_kwargs):
            self.attempts += 1
            if self.attempts == 1:
                response = Response(
                    429,
                    headers={"retry-after-ms": "20000"},
                    request=Request("POST", "https://example/responses"),
                )
                raise RateLimitError(
                    "rate limited",
                    response=response,
                    body={"additionalInfo": {"request_id": "request-123"}},
                )
            return "recalled"

    responses = Responses()
    client = type("Client", (), {"responses": responses})()
    delays: list[float] = []
    monkeypatch.setattr(demo.time, "sleep", delays.append)

    result = demo._create_response_with_retry(client, input="recall")

    assert result == "recalled"
    assert responses.attempts == 2
    assert delays == [20]


def test_memory_response_bounds_output_tokens():
    class Responses:
        def create(self, **kwargs):
            return kwargs

    client = type("Client", (), {"responses": Responses()})()

    result = demo._create_memory_response(client, input="recall")

    assert result["max_output_tokens"] == 200


def test_memory_fallback_requires_prevalidated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(demo, "STATE_PATH", tmp_path / "missing.json")

    with pytest.raises(demo.DemoError, match="no prevalidated Memory result"):
        demo.memory_fallback()


def test_memory_fallback_is_explicitly_labeled(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "memory_showcase": {
                    "memory_items": 1,
                    "profile_recalled_in_new_conversation": "GREEN-742",
                    "chat_summary_recalled_contextually": "STOCK-318",
                },
                "memory_validated_at": "2026-07-29T07:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo, "STATE_PATH", state_path)

    result = demo.memory_fallback()

    assert result["prevalidated"] is True
    assert result["validated_at"] == "2026-07-29T07:00:00+00:00"


def test_memory_ttl_serializes_to_one_day():
    options = demo._memory_store_options()

    assert options.as_dict()["default_ttl_seconds"] == 86400
    assert options.user_profile_enabled is True
    assert options.chat_summary_enabled is True
    assert options.procedural_memory_enabled is False


def test_memory_store_reuse_requires_exact_options():
    expected = demo._memory_store_options()
    drifted = MemoryStoreDefaultOptions(
        user_profile_enabled=True,
        user_profile_details=expected.user_profile_details,
        chat_summary_enabled=True,
        procedural_memory_enabled=True,
        default_ttl_seconds=expected.default_ttl_seconds,
    )

    assert demo._memory_store_options_match(expected) is True
    assert demo._memory_store_options_match(drifted) is False
    assert demo._memory_store_options_match(object()) is False


def test_memory_kind_handles_sdk_enum_and_string():
    enum_item = type("Item", (), {"kind": demo.MemoryItemKind.CHAT_SUMMARY})()
    string_item = type("Item", (), {"kind": "user_profile"})()

    assert demo._memory_kind(enum_item) == "chat_summary"
    assert demo._memory_kind(string_item) == "user_profile"


def _run_mocked_memory_showcase(monkeypatch, *, leak_source=None):
    events = []
    response_calls = []

    class Conversations:
        next_id = 0

        def create(self):
            self.next_id += 1
            conversation = SimpleNamespace(id=f"conversation-{self.next_id}")
            events.append(("conversation.create", conversation.id))
            return conversation

        def delete(self, conversation_id):
            events.append(("conversation.delete", conversation_id))

    class Responses:
        def create(self, **kwargs):
            response_calls.append(kwargs)
            scope = kwargs["extra_headers"]["x-memory-user-id"]
            if scope == demo.MEMORY_ISOLATED_SCOPE:
                output = "GREEN-742 leaked" if leak_source == "agent" else "unknown"
            elif kwargs["input"] == demo.MEMORY_RECALL_QUESTION:
                output = "GREEN-742"
            else:
                output = "acknowledged"
            return SimpleNamespace(output_text=output)

    class OpenAI:
        conversations = Conversations()
        responses = Responses()

        def close(self):
            events.append(("openai.close", None))

    class Project:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_openai_client(self):
            return OpenAI()

    memories = [
        SimpleNamespace(kind="user_profile", content="GREEN-742"),
        SimpleNamespace(kind="chat_summary", content="STOCK-318 no transfer"),
    ]
    summary = SimpleNamespace(kind="chat_summary", content="STOCK-318 no transfer")

    monkeypatch.setattr(
        demo,
        "ensure_memory_agent",
        lambda _contract, **_kwargs: "1",
    )
    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())
    monkeypatch.setattr(
        demo,
        "_clear_memory_scope",
        lambda _project, scope: events.append(("scope.clear", scope)),
    )

    def wait_for_kinds(_project, *, scope, timeout_seconds, required_kinds):
        events.append(("memory.wait", scope, timeout_seconds, required_kinds))
        return memories

    def wait_for_summary(_project, *, scope, query, timeout_seconds):
        events.append(("summary.wait", scope, query, timeout_seconds))
        return [summary], summary.content

    monkeypatch.setattr(demo, "_wait_for_memory_kinds", wait_for_kinds)
    monkeypatch.setattr(demo, "_wait_for_contextual_summary", wait_for_summary)
    monkeypatch.setattr(
        demo,
        "_search_contextual_memories",
        lambda _project, *, scope, query: (
            [summary] if leak_source == "contextual" else []
        ),
    )
    monkeypatch.setattr(
        demo,
        "_search_static_memories",
        lambda _project, *, scope: (
            [SimpleNamespace(kind="user_profile", content="transformed leak")]
            if leak_source == "static"
            else []
        ),
    )
    monkeypatch.setattr(demo, "_save_state", lambda values: events.append(("save", values)))

    result = demo.invoke_memory_showcase(object(), timeout_seconds=120)
    return result, events, response_calls


def test_memory_showcase_orders_scopes_and_separates_conversations(monkeypatch):
    result, events, calls = _run_mocked_memory_showcase(monkeypatch)

    assert events[:2] == [
        ("scope.clear", demo.MEMORY_SCOPE),
        ("scope.clear", demo.MEMORY_ISOLATED_SCOPE),
    ]
    assert [call["conversation"] for call in calls] == [
        "conversation-1",
        "conversation-1",
        "conversation-1",
        "conversation-2",
        "conversation-3",
    ]
    assert events.index(("conversation.delete", "conversation-1")) < next(
        index for index, event in enumerate(events) if event[0] == "summary.wait"
    )
    assert demo.MEMORY_SUMMARY_SEARCH_QUERY not in [
        call["input"] for call in calls
    ]
    assert result["memory_kinds"] == ["chat_summary", "user_profile"]
    assert result["isolated_static_memory_items"] == 0
    assert result["isolated_chat_summary_items"] == 0


@pytest.mark.parametrize("leak_source", ["static", "contextual", "agent"])
def test_memory_showcase_rejects_isolated_scope_leaks(monkeypatch, leak_source):
    with pytest.raises(demo.DemoError, match="crossed user scopes"):
        _run_mocked_memory_showcase(monkeypatch, leak_source=leak_source)


def test_clear_memory_scope_waits_until_stale_items_are_gone(monkeypatch):
    class Stores:
        responses = [[SimpleNamespace(content="stale")], []]

        def delete_scope(self, *, name, scope):
            assert name == demo.MEMORY_STORE_NAME
            assert scope == demo.MEMORY_SCOPE

        def list_memories(self, *, name, scope):
            assert name == demo.MEMORY_STORE_NAME
            assert scope == demo.MEMORY_SCOPE
            return self.responses.pop(0)

    project = SimpleNamespace(
        beta=SimpleNamespace(memory_stores=Stores()),
    )
    sleeps = []
    monkeypatch.setattr(demo.time, "sleep", sleeps.append)

    demo._clear_memory_scope(project, demo.MEMORY_SCOPE)

    assert sleeps == [2]


def test_memory_reset_refuses_unowned_agent_versions(monkeypatch):
    class Version(dict):
        @property
        def version(self):
            return self["version"]

    class Agents:
        def list_versions(self, name, include_drafts):
            assert name == demo.MEMORY_AGENT_NAME
            assert include_drafts is True
            return [Version(version="9", metadata={"workshop_owner": "someone-else"})]

        def delete_version(self, **_kwargs):
            raise AssertionError("unowned version must not be deleted")

    class Project:
        agents = Agents()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())

    with pytest.raises(demo.DemoError, match="unowned versions"):
        demo.reset_memory_resources(object())


def test_memory_reset_recovers_after_agent_deleted_store_retained(
    tmp_path, monkeypatch
):
    shared = {
        "agent_exists": True,
        "store_exists": True,
        "store_delete_attempts": 0,
    }

    class Version(dict):
        @property
        def version(self):
            return self["version"]

    class Agents:
        def list_versions(self, name, include_drafts):
            assert name == demo.MEMORY_AGENT_NAME
            assert include_drafts is True
            if not shared["agent_exists"]:
                raise demo.ResourceNotFoundError(message="agent missing")
            return [
                Version(
                    version="1",
                    metadata={"workshop_owner": demo.OWNER_METADATA},
                )
            ]

        def delete_version(self, **_kwargs):
            return None

        def get(self, name):
            assert name == demo.MEMORY_AGENT_NAME
            if not shared["agent_exists"]:
                raise demo.ResourceNotFoundError(message="agent missing")
            return agent_details()

        def delete(self, name):
            assert name == demo.MEMORY_AGENT_NAME
            if not shared["agent_exists"]:
                raise demo.ResourceNotFoundError(message="missing")
            shared["agent_exists"] = False

    class Stores:
        def get(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            if not shared["store_exists"]:
                raise demo.ResourceNotFoundError(message="store missing")
            return SimpleNamespace(
                metadata={"workshop_owner": demo.OWNER_METADATA}
            )

        def delete(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            shared["store_delete_attempts"] += 1
            if shared["store_delete_attempts"] == 1:
                raise HttpResponseError(message="transient")
            shared["store_exists"] = False

    class Project:
        agents = Agents()
        beta = SimpleNamespace(memory_stores=Stores())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "memory_showcase": {"memory_items": 2},
                "memory_validated_at": "2026-07-29T07:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(demo, "STATE_PATH", state_path)
    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())

    with pytest.raises(demo.DemoError, match="memory reset incomplete"):
        demo.reset_memory_resources(object())

    assert shared["agent_exists"] is False
    assert shared["store_exists"] is True
    assert state_path.exists()

    result = demo.reset_memory_resources(object())

    assert result["agent_versions"] == []
    assert result["memory_store"] == demo.MEMORY_STORE_NAME
    assert shared["store_exists"] is False
    assert not state_path.exists()


@pytest.mark.parametrize("resource", ["version", "agent", "store"])
def test_memory_reset_accepts_only_typed_absence(tmp_path, monkeypatch, resource):
    class Version(dict):
        @property
        def version(self):
            return self["version"]

    class Agents:
        def list_versions(self, _name, include_drafts):
            assert include_drafts is True
            return [
                Version(
                    version="1",
                    metadata={"workshop_owner": demo.OWNER_METADATA},
                )
            ]

        def delete_version(self, **_kwargs):
            if resource == "version":
                raise demo.ResourceNotFoundError(message="already absent")

        def get(self, _name):
            return agent_details()

        def delete(self, _name):
            if resource == "agent":
                raise demo.ResourceNotFoundError(message="already absent")

    class Stores:
        def get(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            return SimpleNamespace(
                metadata={"workshop_owner": demo.OWNER_METADATA}
            )

        def delete(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            if resource == "store":
                raise demo.ResourceNotFoundError(message="already absent")

    class Project:
        agents = Agents()
        beta = SimpleNamespace(memory_stores=Stores())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())
    monkeypatch.setattr(demo, "STATE_PATH", tmp_path / "missing-state.json")

    demo.reset_memory_resources(object())


def test_memory_reset_rejects_generic_http_404(tmp_path, monkeypatch):
    class Version(dict):
        @property
        def version(self):
            return self["version"]

    class Agents:
        def list_versions(self, _name, include_drafts):
            assert include_drafts is True
            return [
                Version(
                    version="1",
                    metadata={"workshop_owner": demo.OWNER_METADATA},
                )
            ]

        def delete_version(self, **_kwargs):
            return None

        def get(self, _name):
            return agent_details()

        def delete(self, _name):
            raise HttpResponseError(message="generic 404", status_code=404)

    class Stores:
        def get(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            raise demo.ResourceNotFoundError(message="store absent")

    class Project:
        agents = Agents()
        beta = SimpleNamespace(memory_stores=Stores())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())
    monkeypatch.setattr(demo, "STATE_PATH", tmp_path / "state.json")

    with pytest.raises(demo.DemoError, match="generic 404"):
        demo.reset_memory_resources(object())


def test_memory_reset_refuses_existing_empty_agent(monkeypatch):
    class Agents:
        delete_called = False

        def list_versions(self, _name, include_drafts):
            assert include_drafts is True
            return []

        def get(self, _name):
            return agent_details()

        def delete(self, _name):
            self.delete_called = True

    agents = Agents()
    project = SimpleNamespace(agents=agents)

    class Project:
        def __enter__(self):
            return project

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())

    with pytest.raises(demo.DemoError, match="no versions"):
        demo.reset_memory_resources(object())

    assert agents.delete_called is False


def test_memory_reset_recovers_retained_empty_agent(tmp_path, monkeypatch):
    shared = {
        "versions_exist": True,
        "parent_delete_attempts": 0,
        "store_exists": True,
    }

    class Version(dict):
        @property
        def version(self):
            return self["version"]

    class Agents:
        def list_versions(self, _name, include_drafts):
            assert include_drafts is True
            if shared["versions_exist"]:
                return [
                    Version(
                        version="1",
                        metadata={"workshop_owner": demo.OWNER_METADATA},
                    )
                ]
            return []

        def get(self, _name):
            return agent_details("retained-agent-principal")

        def delete_version(self, **_kwargs):
            shared["versions_exist"] = False

        def delete(self, _name):
            shared["parent_delete_attempts"] += 1
            if shared["parent_delete_attempts"] == 1:
                raise HttpResponseError(message="transient parent failure")

    class Stores:
        def get(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            if not shared["store_exists"]:
                raise demo.ResourceNotFoundError(message="store absent")
            return SimpleNamespace(
                metadata={"workshop_owner": demo.OWNER_METADATA}
            )

        def delete(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            shared["store_exists"] = False

    class Project:
        agents = Agents()
        beta = SimpleNamespace(memory_stores=Stores())

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(demo, "STATE_PATH", state_path)
    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())

    with pytest.raises(demo.DemoError, match="transient parent failure"):
        demo.reset_memory_resources(object())

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["validated_agent_deletions"][demo.MEMORY_AGENT_NAME] == (
        "retained-agent-principal"
    )
    assert shared["versions_exist"] is False
    assert shared["store_exists"] is True

    result = demo.reset_memory_resources(object())

    assert result["agent_versions"] == []
    assert result["memory_store"] == demo.MEMORY_STORE_NAME
    assert shared["store_exists"] is False
    assert not state_path.exists()


def test_partial_cleanup_reports_scope_and_retains_state(tmp_path, monkeypatch):
    class Version(dict):
        @property
        def version(self):
            return self["version"]

    versions = [
        Version(version="1", metadata={"workshop_owner": demo.OWNER_METADATA})
    ]

    class Agents:
        def list_versions(self, _name, include_drafts):
            assert include_drafts is True
            return versions

        def delete_version(self, *, agent_name, agent_version, force):
            assert force is True
            if agent_name == demo.MEMORY_AGENT_NAME:
                raise HttpResponseError(message=f"retained {agent_version}")

        def get(self, name):
            return agent_details(f"principal-{name}")

        def delete(self, _name):
            return None

    class MemoryStores:
        def get(self, *, name):
            assert name == demo.MEMORY_STORE_NAME
            return type(
                "Store",
                (),
                {"metadata": {"workshop_owner": demo.OWNER_METADATA}},
            )()

        def delete(self, *, name):
            assert name == demo.MEMORY_STORE_NAME

    class Project:
        agents = Agents()
        beta = type("Beta", (), {"memory_stores": MemoryStores()})()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(demo, "STATE_PATH", state_path)
    monkeypatch.setattr(demo, "project_client", lambda _contract: Project())

    with pytest.raises(demo.DemoError, match="failed_or_retained") as failure:
        demo.cleanup(object())

    assert demo.MEMORY_AGENT_NAME in str(failure.value)
    assert state_path.exists()


def test_source_archive_is_deterministic_and_minimal():
    first = demo.create_source_archive()
    second = demo.create_source_archive()

    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()
    with zipfile.ZipFile(BytesIO(first)) as archive:
        assert archive.namelist() == ["main.py", "requirements.txt"]
        assert ".env" not in archive.namelist()


def test_hosted_requirements_match_locked_versions():
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = {
        package["name"].lower().replace("_", "-"): package["version"]
        for package in lock["package"]
    }
    requirements = (
        DEMO_ROOT / "agent" / "requirements.txt"
    ).read_text(encoding="utf-8").splitlines()

    for line in requirements:
        requirement = line.strip()
        if not requirement or requirement.startswith("#"):
            continue
        name, version = requirement.split("==", 1)
        normalized_name = name.split("[", 1)[0].lower().replace("_", "-")
        assert locked[normalized_name] == version


def test_shortfall_tool_is_deterministic():
    result = agent.calculate_shortfall.func(
        current_stock=8,
        reserved_stock=3,
        forecast_demand=14,
    )

    assert json.loads(result) == {
        "available_stock": 5,
        "forecast_demand": 14,
        "shortfall": 9,
    }


def test_shortfall_rejects_negative_values():
    with pytest.raises(ValueError, match="non-negative"):
        agent.calculate_shortfall.func(
            current_stock=-1,
            reserved_stock=0,
            forecast_demand=1,
        )


def test_scenario_and_system_prompt_are_synthetic_and_bounded():
    assert "Synthetic store" in demo.STOCK_EXCEPTION
    assert "Never recommend product substitutions" in agent.SYSTEM_PROMPT
    assert "claim access to live systems" in agent.SYSTEM_PROMPT


def test_response_text_reads_responses_api_shape():
    payload = {
        "output": [
            {
                "content": [
                    {"type": "output_text", "text": "PRIORITY: high"},
                    {"type": "output_text", "text": "\nNEXT ACTION: escalate"},
                ]
            }
        ]
    }

    assert demo._response_text(payload) == "PRIORITY: high\nNEXT ACTION: escalate"
