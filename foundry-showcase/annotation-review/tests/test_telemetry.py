from __future__ import annotations

import json

import pytest

from telemetry import (
    Annotation,
    annotation_envelope,
    annotation_properties,
    build_evaluation_dataset,
    dataset_to_jsonl,
    parse_connection_string,
    rows_to_dicts,
    strip_memory_preamble,
    summarize_messages,
)

TRACE_ID = "38cf2c1fc140195dcc2de68e9ff0d4e6"
SPAN_ID = "f52be5677a2c7aca"


def test_annotation_rejects_malformed_ids():
    with pytest.raises(ValueError):
        Annotation(trace_id="abc", span_id=SPAN_ID, passed=True)
    with pytest.raises(ValueError):
        Annotation(trace_id=TRACE_ID, span_id="abc", passed=True)
    with pytest.raises(ValueError):
        Annotation(trace_id="0" * 32, span_id=SPAN_ID, passed=True)


def test_annotation_rejects_unknown_source():
    with pytest.raises(ValueError):
        Annotation(trace_id=TRACE_ID, span_id=SPAN_ID, passed=True, source="robot")


def test_thumbs_down_uses_documented_schema():
    annotation = Annotation(
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        passed=False,
        explanation="Wrong refund window.",
        reviewer="reviewer@contoso.com",
    )
    properties = annotation_properties(annotation)
    assert properties["gen_ai.evaluation.name"] == "task_completion"
    assert properties["gen_ai.evaluation.score.value"] == "0.0"
    assert properties["gen_ai.evaluation.score.label"] == "fail"
    assert properties["microsoft.gen_ai.evaluation.actor.type"] == "human"
    assert properties["microsoft.gen_ai.human_evaluation.source"] == "builder"
    assert properties["gen_ai.evaluation.explanation"] == "Wrong refund window."
    internal = json.loads(properties["internal_properties"])
    assert internal["gen_ai.evaluation.type"] == "boolean"
    assert internal["gen_ai.evaluation.min_value"] == "0.0"
    assert internal["gen_ai.evaluation.max_value"] == "1.0"
    assert internal["gen_ai.evaluation.desirable_direction"] == "increase"


def test_thumbs_up_scores_one():
    properties = annotation_properties(
        Annotation(trace_id=TRACE_ID, span_id=SPAN_ID, passed=True)
    )
    assert properties["gen_ai.evaluation.score.value"] == "1.0"
    assert properties["gen_ai.evaluation.score.label"] == "pass"


def test_envelope_binds_annotation_to_original_trace():
    envelope = annotation_envelope(
        Annotation(trace_id=TRACE_ID, span_id=SPAN_ID, passed=True), "the-ikey"
    )
    assert envelope["tags"]["ai.operation.id"] == TRACE_ID
    assert envelope["tags"]["ai.operation.parentId"] == SPAN_ID
    assert envelope["iKey"] == "the-ikey"
    assert envelope["data"]["baseType"] == "EventData"
    assert envelope["data"]["baseData"]["name"] == "gen_ai.evaluation.result"


def test_end_user_source_is_allowed():
    properties = annotation_properties(
        Annotation(
            trace_id=TRACE_ID, span_id=SPAN_ID, passed=True, source="end_user"
        )
    )
    assert properties["microsoft.gen_ai.human_evaluation.source"] == "end_user"


def test_parse_connection_string():
    key, endpoint = parse_connection_string(
        "InstrumentationKey=abc;IngestionEndpoint=https://x.in.applicationinsights.azure.com/;"
        "LiveEndpoint=https://y/"
    )
    assert key == "abc"
    assert endpoint == "https://x.in.applicationinsights.azure.com"


def test_parse_connection_string_requires_fields():
    with pytest.raises(ValueError):
        parse_connection_string("LiveEndpoint=https://y/")


def test_rows_to_dicts():
    payload = {
        "tables": [
            {
                "name": "PrimaryResult",
                "columns": [{"name": "a"}, {"name": "b"}],
                "rows": [[1, 2], [3, 4]],
            }
        ]
    }
    assert rows_to_dicts(payload) == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
    assert rows_to_dicts({"tables": []}) == []


def test_summarize_messages_reads_last_matching_role():
    raw = json.dumps(
        [
            {"role": "user", "parts": [{"type": "text", "content": "first"}]},
            {
                "role": "assistant",
                "parts": [{"type": "tool_call", "id": "1", "name": "x"}],
            },
            {"role": "user", "parts": [{"type": "text", "content": "second"}]},
        ]
    )
    assert summarize_messages(raw, "user") == "second"
    assert summarize_messages(raw, "assistant") == ""
    assert summarize_messages(None, "user") == ""
    assert summarize_messages("not json", "user") == ""


def test_strip_memory_preamble():
    text = (
        "Persistent user context:\nNo stored memories yet.\n\n"
        "Current user message:\nWhere is my order?"
    )
    assert strip_memory_preamble(text) == "Where is my order?"
    assert strip_memory_preamble("plain question") == "plain question"


def test_build_evaluation_dataset_only_keeps_flagged_answers():
    runs = [
        {
            "traceId": TRACE_ID,
            "spanId": SPAN_ID,
            "question": "Where is my order?",
            "answer": "It shipped yesterday.",
            "agentName": "foundry-showcase-main",
            "agentVersion": "28",
        },
        {
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "question": "Refund policy?",
            "answer": "30 days.",
            "agentName": "foundry-showcase-main",
            "agentVersion": "28",
        },
    ]
    annotations = [
        {
            "traceId": TRACE_ID,
            "spanId": SPAN_ID,
            "label": "fail",
            "explanation": "Order actually shipped today.",
            "reviewer": "reviewer@contoso.com",
            "timestamp": "2026-08-03T00:00:00Z",
        },
        {
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "label": "pass",
            "explanation": "",
            "reviewer": "reviewer@contoso.com",
            "timestamp": "2026-08-03T00:00:00Z",
        },
    ]
    cases = build_evaluation_dataset(runs, annotations)
    assert len(cases) == 1
    assert cases[0]["id"] == 1
    assert cases[0]["query"] == "Where is my order?"
    assert cases[0]["candidate_response"] == "It shipped yesterday."
    assert cases[0]["reviewer_comment"] == "Order actually shipped today."
    assert cases[0]["agent_version"] == "28"
    assert "business reviewer" in cases[0]["description"]
    assert "Order actually shipped today." in cases[0]["description"]


def test_build_evaluation_dataset_deduplicates_repeat_ratings():
    runs = [
        {
            "traceId": TRACE_ID,
            "spanId": SPAN_ID,
            "question": "Where is my order?",
            "answer": "It shipped yesterday.",
        }
    ]
    annotations = [
        {"traceId": TRACE_ID, "spanId": SPAN_ID, "label": "fail", "timestamp": "1"},
        {"traceId": TRACE_ID, "spanId": SPAN_ID, "label": "fail", "timestamp": "2"},
    ]
    assert len(build_evaluation_dataset(runs, annotations)) == 1


def test_end_user_feedback_is_described_as_such():
    runs = [
        {
            "traceId": TRACE_ID,
            "spanId": SPAN_ID,
            "question": "Where is my order?",
            "answer": "It shipped yesterday.",
        }
    ]
    annotations = [
        {
            "traceId": TRACE_ID,
            "spanId": "0" * 15 + "1",
            "label": "fail",
            "source": "end_user",
            "explanation": "That is not what happened.",
            "timestamp": "1",
        }
    ]
    cases = build_evaluation_dataset(runs, annotations)
    assert len(cases) == 1
    assert "end user in the chat client" in cases[0]["description"]
    assert cases[0]["feedback_source"] == "end_user"


def test_dataset_to_jsonl_is_one_object_per_line():
    text = dataset_to_jsonl([{"query": "a"}, {"query": "b"}])
    lines = text.splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["query"] == "a"
