from __future__ import annotations

from pathlib import Path

from bio_harness.analysis.harness_capabilities import (
    build_harness_capability_summary,
    build_harness_layer_counts,
    build_researcher_examples,
    build_skill_category_counts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_harness_capability_summary_reports_expected_inventory() -> None:
    summary = build_harness_capability_summary(PROJECT_ROOT)

    assert summary.skill_wrappers >= 40
    assert summary.implemented_skill_modules >= summary.skill_wrappers
    assert summary.analysis_categories >= 10
    assert summary.capability_ids >= 20
    assert summary.uncommon_skills_with_safe_fallback == 6
    assert summary.deterministic_fallback_templates >= 25
    assert summary.scientific_tool_catalog_entries >= 30
    assert summary.researcher_review_skills == 2
    assert summary.figure_spec_types == 4


def test_harness_layer_counts_are_non_empty() -> None:
    frame = build_harness_layer_counts(PROJECT_ROOT)

    assert not frame.empty
    assert set(frame.columns) == {"layer", "count"}
    assert "Skill wrappers" in set(frame["layer"])


def test_skill_category_counts_cover_multiple_domains() -> None:
    frame = build_skill_category_counts(PROJECT_ROOT)

    assert not frame.empty
    assert "analysis_category" in frame.columns
    assert "skill_count" in frame.columns
    assert frame["skill_count"].max() >= 1


def test_researcher_examples_include_figure_generation() -> None:
    frame = build_researcher_examples()

    assert not frame.empty
    assert any("figure" in str(goal).lower() for goal in frame["research_goal"])
