"""Tests for the planner plan-tool validator.

The validator must never reject a plan solely because the stepwise retrieval
subset omitted a universally-available skill (e.g. ``bash_run``). Dropping
``bash_run`` creates a livelock where the LLM legitimately proposes
``bash_run`` for concat/merge steps, the validator rejects the plan, and
the planner retries forever.
"""

from __future__ import annotations

from bio_harness.core.llm_entrypoints_mixin import (
    LLMEntrypointsMixin,
    _ALWAYS_AVAILABLE_SKILL_NAMES,
)


class _ValidatorHarness(LLMEntrypointsMixin):
    """Minimal concrete subclass exposing the validator for testing."""


def test_bash_run_is_always_considered_available() -> None:
    validator = _ValidatorHarness()
    plan = {"plan": [{"tool_name": "bash_run", "arguments": {"command": "ls"}}]}
    # Retrieval-limited subset deliberately excludes bash_run.
    unknown = validator._unknown_plan_tools(plan=plan, available_skills=[{"name": "bwa_mem_align"}])
    assert unknown == []


def test_unknown_tools_still_flagged_when_not_in_always_list() -> None:
    validator = _ValidatorHarness()
    plan = {"plan": [{"tool_name": "nonexistent_hallucinated_tool"}]}
    unknown = validator._unknown_plan_tools(
        plan=plan, available_skills=[{"name": "bwa_mem_align"}]
    )
    assert unknown == ["nonexistent_hallucinated_tool"]


def test_explicit_available_skills_still_allowed() -> None:
    validator = _ValidatorHarness()
    plan = {"plan": [{"tool_name": "bwa_mem_align"}, {"tool_name": "bash_run"}]}
    unknown = validator._unknown_plan_tools(
        plan=plan, available_skills=[{"name": "bwa_mem_align"}]
    )
    assert unknown == []


def test_always_available_set_is_frozen_and_contains_bash_run() -> None:
    assert isinstance(_ALWAYS_AVAILABLE_SKILL_NAMES, frozenset)
    assert "bash_run" in _ALWAYS_AVAILABLE_SKILL_NAMES


def test_always_available_set_includes_registered_skills() -> None:
    # Any skill with a markdown definition and index.json entry must be
    # treated as valid by the validator, since the harness can actually
    # execute it. This is the general fix for retrieval-subset dropouts.
    assert "prokka_annotate" in _ALWAYS_AVAILABLE_SKILL_NAMES
    assert "prodigal_annotate" in _ALWAYS_AVAILABLE_SKILL_NAMES
    assert "bwa_mem_align" in _ALWAYS_AVAILABLE_SKILL_NAMES
    assert "freebayes_call" in _ALWAYS_AVAILABLE_SKILL_NAMES


def test_registered_but_unretrieved_skill_is_allowed() -> None:
    validator = _ValidatorHarness()
    plan = {"plan": [{"tool_name": "prokka_annotate"}]}
    # Retrieval dropped prokka_annotate but it is a real registered skill.
    unknown = validator._unknown_plan_tools(
        plan=plan, available_skills=[{"name": "bwa_mem_align"}]
    )
    assert unknown == []


def test_empty_plan_returns_no_unknown() -> None:
    validator = _ValidatorHarness()
    assert validator._unknown_plan_tools(plan={"plan": []}, available_skills=[]) == []


def test_plan_without_plan_key_returns_no_unknown() -> None:
    validator = _ValidatorHarness()
    assert validator._unknown_plan_tools(plan={}, available_skills=[]) == []
