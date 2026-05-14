"""Inventory broader Bio-Harness capabilities for manuscript reporting."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from bio_harness.core.uncommon_skill_framework import uncommon_skill_specs
from bio_harness.workflows.fallback_catalog import build_ranked_fallback_catalog


@dataclass(frozen=True)
class HarnessCapabilitySummary:
    """Compact manuscript-facing summary of harness capabilities."""

    skill_wrappers: int
    implemented_skill_modules: int
    analysis_categories: int
    capability_ids: int
    uncommon_skills_with_safe_fallback: int
    deterministic_fallback_templates: int
    scientific_tool_catalog_entries: int
    researcher_review_skills: int
    figure_spec_types: int


def _project_root(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root.resolve()
    return Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def _skill_index(project_root: Path) -> dict[str, Any]:
    return _load_json(project_root / "bio_harness" / "skills" / "definitions" / "index.json")


def build_harness_capability_summary(project_root: Path | None = None) -> HarnessCapabilitySummary:
    """Build a compact summary of the non-benchmark harness surface."""
    root = _project_root(project_root)
    skill_index = _skill_index(root)
    skills = skill_index.get("skills", []) if isinstance(skill_index.get("skills", []), list) else []
    skill_rows = [row for row in skills if isinstance(row, dict)]
    skill_library = [
        path
        for path in (root / "bio_harness" / "skills" / "library").glob("*.py")
        if not path.name.startswith("_")
    ]
    analysis_categories = {
        str(category).strip()
        for row in skill_rows
        for category in row.get("analysis_categories", [])
        if str(category).strip()
    }
    capability_ids = {
        str(capability).strip()
        for row in skill_rows
        for capability in row.get("capabilities", [])
        if str(capability).strip()
    }
    scientific_tool_catalog = _load_json(root / "bio_harness" / "capabilities" / "scientific_tools.json")
    scientific_tools = scientific_tool_catalog.get("tools", []) if isinstance(scientific_tool_catalog.get("tools", []), list) else []
    researcher_review_skills = list((root / "docs" / "agent_skills").glob("*/SKILL.md"))
    return HarnessCapabilitySummary(
        skill_wrappers=len(skill_rows),
        implemented_skill_modules=len(skill_library),
        analysis_categories=len(analysis_categories),
        capability_ids=len(capability_ids),
        uncommon_skills_with_safe_fallback=len(uncommon_skill_specs()),
        deterministic_fallback_templates=len(build_ranked_fallback_catalog()),
        scientific_tool_catalog_entries=len([row for row in scientific_tools if isinstance(row, dict)]),
        researcher_review_skills=len(researcher_review_skills),
        figure_spec_types=4,
    )


def build_harness_layer_counts(project_root: Path | None = None) -> pd.DataFrame:
    """Build a table of top-level harness capability layers."""
    summary = build_harness_capability_summary(project_root)
    rows = [
        {"layer": "Skill wrappers", "count": summary.skill_wrappers},
        {"layer": "Implemented skill modules", "count": summary.implemented_skill_modules},
        {"layer": "Analysis categories", "count": summary.analysis_categories},
        {"layer": "Capability IDs", "count": summary.capability_ids},
        {"layer": "Uncommon safe-fallback skills", "count": summary.uncommon_skills_with_safe_fallback},
        {"layer": "Deterministic fallback templates", "count": summary.deterministic_fallback_templates},
        {"layer": "Scientific tool catalog entries", "count": summary.scientific_tool_catalog_entries},
        {"layer": "Researcher review skills", "count": summary.researcher_review_skills},
        {"layer": "Figure spec types", "count": summary.figure_spec_types},
    ]
    return pd.DataFrame(rows)


def build_skill_category_counts(project_root: Path | None = None) -> pd.DataFrame:
    """Count skill coverage by analysis category."""
    root = _project_root(project_root)
    skill_index = _skill_index(root)
    skills = skill_index.get("skills", []) if isinstance(skill_index.get("skills", []), list) else []
    rows: list[dict[str, Any]] = []
    for row in skills:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        for category in row.get("analysis_categories", []) or []:
            token = str(category).strip()
            if token:
                rows.append({"skill_name": name, "analysis_category": token})
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["analysis_category", "skill_count"])
    summary = (
        frame.groupby("analysis_category", as_index=False)
        .agg(skill_count=("skill_name", "nunique"))
        .sort_values(["skill_count", "analysis_category"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return summary


def build_researcher_examples() -> pd.DataFrame:
    """Return manuscript-ready examples of researcher-facing harness use cases."""
    rows = [
        {
            "research_goal": "Plan and execute an RNA-seq differential expression study from paired-end FASTQ files.",
            "harness_components": "analysis-design-review, skill selection, alignment/count wrappers, monitored execution, deliverable export",
            "expected_outputs": "counts matrix, differential expression table, audit trail, figure-ready results",
        },
        {
            "research_goal": "Triage a metagenomics sample and generate a taxonomic summary with safe degraded behavior if niche tools are unavailable.",
            "harness_components": "analysis typing, uncommon-skill support, fallback catalog, heartbeat monitoring, final report export",
            "expected_outputs": "classification report, abundance summary, provenance log, deterministic degraded output when needed",
        },
        {
            "research_goal": "Review a completed run for biological plausibility without editing shell commands manually.",
            "harness_components": "analysis-output-review, semantic guards, repair context, validator-facing deliverables",
            "expected_outputs": "review note, rerun decision, localized repair target, preserved artifact lineage",
        },
        {
            "research_goal": "Onboard a new command-line bioinformatics tool and expose it to the planner.",
            "harness_components": "tool_onboarding, skill_generator, capability catalog, skill index regeneration",
            "expected_outputs": "new skill definition, Python wrapper stub, capability hints, planner-visible skill metadata",
        },
        {
            "research_goal": "Generate publication-ready workflow or quantitative figures from structured outputs.",
            "harness_components": "figure spec renderer, manuscript asset pipeline, PNG/SVG/PPT export",
            "expected_outputs": "publication-ready figure files plus reusable spec files for regeneration",
        },
        {
            "research_goal": "Profile the schema of a completed CSV, TSV, VCF, or GTF artifact before building a downstream step.",
            "harness_components": "artifact_schema_profile, schema inference, data-dictionary export",
            "expected_outputs": "column list, inferred types, example values, reusable schema JSON",
        },
        {
            "research_goal": "Check whether the current workstation has enough RAM, CPU, and disk for a planned workflow before launching it.",
            "harness_components": "resource_preflight, skill metadata, local system inspection",
            "expected_outputs": "resource summary, risk warnings, machine-readable preflight JSON",
        },
        {
            "research_goal": "Compare two completed runs to understand what changed in outputs, repairs, and plan structure.",
            "harness_components": "run_compare, reportable run context, artifact diffing",
            "expected_outputs": "JSON and Markdown run comparison bundle with output deltas and repair changes",
        },
        {
            "research_goal": "Audit a reference directory to confirm FASTA, annotation, and common index assets are staged correctly.",
            "harness_components": "reference_manager, reference bundle audit, index discovery",
            "expected_outputs": "reference inventory, index summary, compatibility-oriented staging report",
        },
    ]
    return pd.DataFrame(rows)


def build_harness_summary_payload(project_root: Path | None = None) -> dict[str, Any]:
    """Build a JSON-serializable manuscript payload."""
    summary = build_harness_capability_summary(project_root)
    return {
        "summary": asdict(summary),
        "layer_counts": build_harness_layer_counts(project_root).to_dict(orient="records"),
        "category_counts": build_skill_category_counts(project_root).to_dict(orient="records"),
        "researcher_examples": build_researcher_examples().to_dict(orient="records"),
    }
