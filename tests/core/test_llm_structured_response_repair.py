"""Tests for the deterministic JSON-repair helper.

The LLM-based structured-response pipeline must recover from common
truncation/mildly-malformed JSON without requiring a second LLM round-trip.
The repair helper is the first line of defense against planner livelocks in
which the model repeatedly emits the same malformed payload.
"""

from __future__ import annotations

from bio_harness.core.llm_structured_response_mixin import _deterministic_json_repair


def test_deterministic_repair_handles_valid_json() -> None:
    parsed = _deterministic_json_repair('{"analysis_type": "evolution", "plan": []}')
    assert parsed == {"analysis_type": "evolution", "plan": []}


def test_deterministic_repair_strips_trailing_garbage_after_object() -> None:
    raw = '{"a": 1, "b": [2, 3]}  extraneous commentary after the JSON'
    parsed = _deterministic_json_repair(raw)
    assert parsed == {"a": 1, "b": [2, 3]}


def test_deterministic_repair_handles_trailing_comma_and_missing_close() -> None:
    # Truncated mid-array with an illegal trailing comma.
    raw = '{"plan": [ {"step_id": "s1"}, {"step_id": "s2"},'
    parsed = _deterministic_json_repair(raw)
    assert isinstance(parsed, dict)
    assert "plan" in parsed
    assert len(parsed["plan"]) == 2
    assert parsed["plan"][0]["step_id"] == "s1"


def test_deterministic_repair_balances_nested_unclosed_structure() -> None:
    raw = '{"outer": {"inner": [{"step_id": "a"}, {"step_id": "b"}'
    parsed = _deterministic_json_repair(raw)
    assert isinstance(parsed, dict)
    assert parsed["outer"]["inner"][0]["step_id"] == "a"
    assert parsed["outer"]["inner"][1]["step_id"] == "b"


def test_deterministic_repair_ignores_braces_inside_strings() -> None:
    raw = '{"message": "bad } brace { inside", "ok": true}'
    parsed = _deterministic_json_repair(raw)
    assert parsed == {"message": "bad } brace { inside", "ok": True}


def test_deterministic_repair_prefers_prefix_when_junk_trailing() -> None:
    # Second valid JSON object after the first is ignored; we keep the prefix.
    raw = '{"first": 1} {"second": 2}'
    parsed = _deterministic_json_repair(raw)
    assert parsed == {"first": 1}


def test_deterministic_repair_returns_none_for_empty_or_non_object() -> None:
    assert _deterministic_json_repair("") is None
    assert _deterministic_json_repair("   ") is None
    # A bare list is not a dict — our consumer needs objects.
    assert _deterministic_json_repair("[1, 2, 3]") is None
    assert _deterministic_json_repair("not json at all") is None


def test_deterministic_repair_handles_dangling_key_without_value() -> None:
    raw = '{"a": 1, "b": '
    parsed = _deterministic_json_repair(raw)
    assert isinstance(parsed, dict)
    assert parsed.get("a") == 1
    assert "b" not in parsed


def test_deterministic_repair_finds_json_after_leading_prose() -> None:
    raw = "Here is the plan:\n{\"analysis_type\": \"evolution\"}"
    parsed = _deterministic_json_repair(raw)
    assert parsed == {"analysis_type": "evolution"}
