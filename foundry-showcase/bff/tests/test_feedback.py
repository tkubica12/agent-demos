from __future__ import annotations

import json
import logging

import pytest

from feedback import EVENT_NAME, feedback_attributes, parse_hex_id, record_feedback

TRACE_ID = "38cf2c1fc140195dcc2de68e9ff0d4e6"
SPAN_ID = "f52be5677a2c7aca"


def test_thumbs_down_matches_annotation_schema():
    attributes = feedback_attributes(False, comment="Wrong policy quoted.")
    assert attributes["microsoft.custom_event.name"] == EVENT_NAME
    assert attributes["gen_ai.evaluation.name"] == "task_completion"
    assert attributes["gen_ai.evaluation.score.value"] == 0.0
    assert attributes["gen_ai.evaluation.score.label"] == "fail"
    assert attributes["gen_ai.evaluation.explanation"] == "Wrong policy quoted."
    assert attributes["microsoft.gen_ai.evaluation.actor.type"] == "human"
    assert attributes["microsoft.gen_ai.human_evaluation.source"] == "end_user"
    scale = json.loads(attributes["internal_properties"])
    assert scale["gen_ai.evaluation.type"] == "boolean"


def test_thumbs_up_scores_one_and_omits_empty_fields():
    attributes = feedback_attributes(True)
    assert attributes["gen_ai.evaluation.score.value"] == 1.0
    assert attributes["gen_ai.evaluation.score.label"] == "pass"
    assert "gen_ai.evaluation.explanation" not in attributes
    assert "microsoft.gen_ai.evaluation.tags.reviewer" not in attributes


def test_parse_hex_id_rejects_bad_input():
    with pytest.raises(ValueError):
        parse_hex_id("traceId", "short", 32)
    with pytest.raises(ValueError):
        parse_hex_id("traceId", "z" * 32, 32)
    with pytest.raises(ValueError):
        parse_hex_id("traceId", "0" * 32, 32)
    with pytest.raises(ValueError):
        parse_hex_id("traceId", None, 32)
    assert parse_hex_id("spanId", SPAN_ID, 16) == int(SPAN_ID, 16)


def test_record_feedback_emits_on_the_original_trace(caplog):
    with caplog.at_level("INFO", logger="foundry-showcase.feedback"):
        result = record_feedback(TRACE_ID, SPAN_ID, False, "Not helpful", "user@contoso.com")

    assert result["traceId"] == TRACE_ID
    assert result["spanId"] == SPAN_ID
    assert result["label"] == "fail"

    record = next(r for r in caplog.records if r.getMessage() == EVENT_NAME)
    assert record.__dict__["gen_ai.evaluation.score.label"] == "fail"
    assert record.__dict__["microsoft.gen_ai.evaluation.tags.reviewer"] == "user@contoso.com"


def test_record_feedback_survives_a_default_warning_root_logger():
    """Reproduces production: the Azure Monitor handler sits on the root logger, whose
    default level is WARNING. The annotation must still be dispatched."""
    root = logging.getLogger()
    previous_level = root.level
    received: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            received.append(record)

    handler = Capture()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    try:
        record_feedback(TRACE_ID, SPAN_ID, True, reviewer="user@contoso.com")
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    record = next(r for r in received if r.getMessage() == EVENT_NAME)
    assert record.__dict__["gen_ai.evaluation.score.label"] == "pass"


def test_record_feedback_rejects_malformed_ids():
    with pytest.raises(ValueError):
        record_feedback("nope", SPAN_ID, True)
