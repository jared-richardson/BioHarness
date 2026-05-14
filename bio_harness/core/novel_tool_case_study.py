"""Helpers for cold novel-tool onboarding case studies.

This module packages a bounded, auditable workflow for testing whether a tool
that is absent from the current skill registry can be onboarded, persisted, and
retrieved through the standard onboarding pipeline. The initial concrete target
is the ``sylph`` metagenomics case study described in the research plan.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, Callable

from bio_harness.core.onboarding_fixtures import SmokeTestRecipe
from bio_harness.core.onboarding_orchestrator import (
    OnboardingBudget,
    OnboardingOutcome,
    run_onboarding_refinement_loop,
)
from bio_harness.core.tool_probe import build_probe_env
from bio_harness.core.tool_onboarding import slugify_skill_name
from bio_harness.skills.registry import SkillRegistry


@dataclass(frozen=True)
class ColdNovelToolCaseStudyOutcome:
    """Structured result of one cold onboarding case study."""

    skill_name: str
    tool_name: str
    tool_found: bool
    retrieval_before: bool
    retrieval_after: bool
    removed_artifacts: tuple[str, ...]
    onboarding_outcome: OnboardingOutcome | None
    summary_path: Path


def resolve_probe_tool_path(tool_name: str) -> str | None:
    """Resolve a tool binary using the bounded onboarding probe environment.

    Args:
        tool_name: Tool binary name to resolve.

    Returns:
        Resolved binary path when available, otherwise ``None``.
    """

    forced_missing = {
        item.strip().lower()
        for item in str(os.environ.get("BIO_HARNESS_FORCE_MISSING_TOOLS", "")).split(",")
        if item.strip()
    }
    normalized_name = str(tool_name or "").strip().lower()
    if normalized_name in forced_missing:
        return None
    probe_env = build_probe_env(tool_name)
    try:
        return shutil.which(tool_name, path=probe_env.get("PATH", ""))
    except TypeError:
        return shutil.which(tool_name)


def build_sylph_seed_draft() -> dict[str, Any]:
    """Return the seed draft used for the ``sylph`` cold-onboarding case study.

    Returns:
        Seed onboarding draft with a narrow ``sketch``/``profile`` wrapper.
    """

    wrapper_code = (
        "from __future__ import annotations\n\n"
        "import shlex\n\n\n"
        "def _require(kwargs: dict, key: str) -> str:\n"
        "    value = str(kwargs.get(key, '') or '').strip()\n"
        "    if not value:\n"
        "        raise ValueError(f'Missing required parameter for sylph wrapper: {key}')\n"
        "    return value\n\n\n"
        "def sylph_classify(**kwargs) -> str:\n"
        "    mode = str(kwargs.get('mode', '') or '').strip().lower()\n"
        "    threads = str(kwargs.get('threads', 1) or 1).strip()\n"
        "    if mode == 'sketch':\n"
        "        reads = _require(kwargs, 'reads_fastq')\n"
        "        reference = _require(kwargs, 'reference_fasta')\n"
        "        prefix = _require(kwargs, 'database_prefix')\n"
        "        sample_dir = shlex.quote(str(kwargs.get('sample_output_dir', '.')))\n"
        "        parts = ['sylph', 'sketch', '-r', reads, '-g', reference, '-t', threads, '-o', prefix, '-d', sample_dir]\n"
        "        return ' '.join(shlex.quote(part) for part in parts)\n"
        "    if mode == 'profile':\n"
        "        database = _require(kwargs, 'database_path')\n"
        "        sample = _require(kwargs, 'sample_path')\n"
        "        output_tsv = _require(kwargs, 'output_tsv')\n"
        "        parts = ['sylph', 'profile', database, sample, '-t', threads, '-o', output_tsv]\n"
        "        return ' '.join(shlex.quote(part) for part in parts)\n"
        "    raise ValueError(f'Unsupported sylph mode: {mode}')\n"
    )
    return {
        "skill_name": "sylph_classify",
        "name": "sylph_classify",
        "description": "Sketch and profile metagenomic samples with sylph.",
        "risk_level": "low",
        "tools_required": ["sylph"],
        "capabilities": ["taxonomic_profiling", "metagenomics"],
        "parameters": {
            "mode": {
                "type": "string",
                "description": "sylph subcommand mode: sketch or profile.",
                "required": True,
            },
            "reads_fastq": {
                "type": "path",
                "description": "Input reads FASTQ path for sketch mode.",
                "required": False,
            },
            "reference_fasta": {
                "type": "path",
                "description": "Reference FASTA path for sketch mode.",
                "required": False,
            },
            "database_prefix": {
                "type": "path",
                "description": "Output database prefix for sketch mode.",
                "required": False,
            },
            "sample_output_dir": {
                "type": "path",
                "description": "Output directory for sketch-derived sample sketches.",
                "required": False,
            },
            "database_path": {
                "type": "path",
                "description": "Input syldb path for profile mode.",
                "required": False,
            },
            "sample_path": {
                "type": "path",
                "description": "Input sylsp path for profile mode.",
                "required": False,
            },
            "output_tsv": {
                "type": "path",
                "description": "Output TSV path for profile mode.",
                "required": False,
            },
            "threads": {
                "type": "integer",
                "description": "Thread count for sylph execution.",
                "required": False,
                "default": 1,
            },
        },
        "command_template": "",
        "wrapper_code": wrapper_code,
        "when_to_use": "Use for species-level metagenomic profiling with sylph.",
        "when_not_to_use": "Do not use for read-by-read classification or 16S workflows.",
        "output_types": ["profiling.tsv", "syldb", "sylsp"],
        "analysis_categories": ["metagenomics"],
        "input_types": ["fastq", "fasta"],
    }


def build_sylph_smoke_recipes(
    *,
    fixtures_dir: Path,
    work_dir: Path,
    threads: int = 1,
) -> tuple[SmokeTestRecipe, ...]:
    """Build the default smoke-test recipes for the ``sylph`` case study.

    Args:
        fixtures_dir: Directory containing the tiny case-study fixtures.
        work_dir: Directory receiving smoke outputs.
        threads: Thread count passed to the generated wrapper.

    Returns:
        Tuple of smoke recipes for ``sketch`` and ``profile``.

    Raises:
        FileNotFoundError: If required fixture files are missing.
    """

    fixtures = sylph_fixture_paths(fixtures_dir=fixtures_dir, work_dir=work_dir)
    required_inputs = (
        fixtures["reads_fastq"],
        fixtures["reference_fasta"],
    )
    missing = [path for path in required_inputs if not path.exists()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing sylph fixture file(s): {missing_text}")

    return (
        SmokeTestRecipe(
            name="sylph_sketch",
            kwargs={
                "mode": "sketch",
                "reads_fastq": str(fixtures["reads_fastq"]),
                "reference_fasta": str(fixtures["reference_fasta"]),
                "database_prefix": str(fixtures["database_prefix"]),
                "sample_output_dir": str(work_dir),
                "threads": threads,
            },
            expected_outputs=(
                str(fixtures["database_syldb"]),
                str(fixtures["sample_sylsp"]),
            ),
            timeout_seconds=30,
            subprocess_calls=1,
            cwd=str(work_dir),
            description="Sketch the tiny reference genome and sample reads.",
            focus_tags=("output_paths", "output_completeness"),
        ),
        SmokeTestRecipe(
            name="sylph_profile",
            kwargs={
                "mode": "profile",
                "database_path": str(fixtures["database_syldb"]),
                "sample_path": str(fixtures["sample_sylsp"]),
                "output_tsv": str(fixtures["profiling_tsv"]),
                "threads": threads,
            },
            expected_outputs=(str(fixtures["profiling_tsv"]),),
            timeout_seconds=30,
            subprocess_calls=1,
            cwd=str(work_dir),
            description="Profile the tiny sample against the sketched database.",
            focus_tags=("output_paths", "output_markers"),
        ),
    )


def sylph_fixture_paths(*, fixtures_dir: Path, work_dir: Path) -> dict[str, Path]:
    """Resolve the canonical fixture and output paths for the ``sylph`` case study.

    Args:
        fixtures_dir: Fixture directory containing tiny FASTA/FASTQ inputs.
        work_dir: Work directory for generated smoke outputs.

    Returns:
        Mapping of stable logical names to filesystem paths.
    """

    reads_fastq = fixtures_dir / "sample_reads.fastq"
    reference_fasta = fixtures_dir / "reference_genome.fa"
    database_prefix = work_dir / "toy_database"
    sample_sylsp = work_dir / f"{reads_fastq.name}.sylsp"
    return {
        "reads_fastq": reads_fastq,
        "reference_fasta": reference_fasta,
        "database_prefix": database_prefix,
        "database_syldb": work_dir / "toy_database.syldb",
        "sample_sylsp": sample_sylsp,
        "profiling_tsv": work_dir / "profiling.tsv",
    }


def remove_cold_start_artifacts(
    skill_name: str,
    *,
    skills_definitions_dir: Path,
    skills_library_dir: Path,
    tool_cards_dir: Path | None = None,
) -> tuple[str, ...]:
    """Remove persisted artifacts for one onboarded skill.

    Args:
        skill_name: Skill identifier to remove.
        skills_definitions_dir: Skill-definition directory.
        skills_library_dir: Skill-library directory.
        tool_cards_dir: Optional tool-card directory.

    Returns:
        Tuple of removed file paths as strings.
    """

    normalized = slugify_skill_name(skill_name)
    candidates = [
        skills_definitions_dir / f"{normalized}.md",
        skills_library_dir / f"{normalized}.py",
    ]
    if tool_cards_dir is not None:
        candidates.append(tool_cards_dir / f"{normalized}.json")
    removed: list[str] = []
    for candidate in candidates:
        if candidate.exists():
            candidate.unlink()
            removed.append(str(candidate))
    registry_index = skills_definitions_dir / "index.json"
    if registry_index.exists():
        SkillRegistry(skills_definitions_dir).generate_index()
    return tuple(removed)


def registry_contains_skill(
    skill_name: str,
    *,
    skills_definitions_dir: Path,
    tool_cards_dir: Path | None = None,
    query: str | None = None,
) -> bool:
    """Return whether a skill is present in the registry or retrieval results.

    Args:
        skill_name: Skill name to check.
        skills_definitions_dir: Skill-definition directory.
        tool_cards_dir: Optional tool-card directory for search enrichment.
        query: Optional retrieval query. Defaults to the skill name.

    Returns:
        ``True`` if the skill exists in the registry or search results.
    """

    registry = SkillRegistry(skills_definitions_dir)
    normalized = slugify_skill_name(skill_name)
    if registry.get_skill(normalized) is not None:
        return True
    results = registry.search_skills(
        query or normalized,
        limit=5,
        tool_cards_dir=tool_cards_dir,
    )
    return any(str(row.get("name", "")).strip() == normalized for row in results)


def write_cold_onboarding_summary(
    output_path: Path,
    *,
    source_url: str,
    fixtures_dir: Path,
    removed_artifacts: tuple[str, ...],
    outcome: OnboardingOutcome | None,
    retrieval_before: bool,
    retrieval_after: bool,
) -> Path:
    """Write a Markdown summary for one cold-onboarding run.

    Args:
        output_path: Markdown output path.
        source_url: Source URL for the tool under study.
        fixtures_dir: Fixture directory used for the run.
        removed_artifacts: Artifacts removed to establish the cold state.
        outcome: Optional onboarding outcome.
        retrieval_before: Whether the skill was retrievable before onboarding.
        retrieval_after: Whether the skill was retrievable after onboarding.

    Returns:
        Path to the written Markdown file.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Cold Onboarding Sylph Run",
        "",
        f"- Source URL: `{source_url}`",
        f"- Fixtures: `{fixtures_dir}`",
        f"- Retrieval before onboarding: `{retrieval_before}`",
        f"- Retrieval after onboarding: `{retrieval_after}`",
        "",
        "## Cold-State Cleanup",
    ]
    if removed_artifacts:
        lines.extend(f"- Removed `{path}`" for path in removed_artifacts)
    else:
        lines.append("- No prior artifacts were present.")
    lines.extend(["", "## Outcome"])
    if outcome is None:
        lines.append("- No onboarding run was attempted.")
    else:
        lines.extend(
            [
                f"- Success: `{outcome.success}`",
                f"- Installed: `{outcome.installed}`",
                f"- Iterations: `{outcome.iterations}`",
                f"- Budget exhausted: `{outcome.budget_exhausted}`",
                f"- Stalled: `{outcome.stalled}`",
                f"- Message: `{outcome.message}`",
                f"- Advisory path: `{outcome.advisory_path or ''}`",
            ]
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def run_cold_sylph_case_study(
    *,
    fixtures_dir: Path,
    work_dir: Path,
    skills_definitions_dir: Path,
    skills_library_dir: Path,
    capability_catalog_path: Path,
    tool_cards_dir: Path,
    summary_path: Path,
    advisory_catalog_path: Path | None = None,
    source_url: str = "https://github.com/bluenote-1577/sylph",
    budget: OnboardingBudget | None = None,
    install: bool = True,
    command_runner: Callable[..., Any] | None = None,
) -> ColdNovelToolCaseStudyOutcome:
    """Run the cold-state ``sylph`` onboarding case study.

    Args:
        fixtures_dir: Tiny case-study fixture directory.
        work_dir: Work directory for smoke outputs.
        skills_definitions_dir: Skill-definition directory.
        skills_library_dir: Skill-library directory.
        capability_catalog_path: Capability catalog path.
        tool_cards_dir: Tool-card output directory.
        summary_path: Markdown narrative output path.
        advisory_catalog_path: Optional advisory catalog for repeated failures.
        source_url: Source URL recorded in onboarding metadata.
        budget: Optional onboarding budget override.
        install: Whether to persist installed artifacts on success.
        command_runner: Optional injected smoke-test command runner.

    Returns:
        Structured case-study outcome.
    """

    skill_name = "sylph_classify"
    work_dir.mkdir(parents=True, exist_ok=True)
    skills_definitions_dir.mkdir(parents=True, exist_ok=True)
    skills_library_dir.mkdir(parents=True, exist_ok=True)
    capability_catalog_path.parent.mkdir(parents=True, exist_ok=True)
    tool_cards_dir.mkdir(parents=True, exist_ok=True)

    removed = remove_cold_start_artifacts(
        skill_name,
        skills_definitions_dir=skills_definitions_dir,
        skills_library_dir=skills_library_dir,
        tool_cards_dir=tool_cards_dir,
    )
    retrieval_before = registry_contains_skill(
        skill_name,
        skills_definitions_dir=skills_definitions_dir,
        tool_cards_dir=tool_cards_dir,
    )
    tool_path = resolve_probe_tool_path("sylph")
    if not tool_path:
        summary = write_cold_onboarding_summary(
            summary_path,
            source_url=source_url,
            fixtures_dir=fixtures_dir,
            removed_artifacts=removed,
            outcome=None,
            retrieval_before=retrieval_before,
            retrieval_after=False,
        )
        return ColdNovelToolCaseStudyOutcome(
            skill_name=skill_name,
            tool_name="sylph",
            tool_found=False,
            retrieval_before=retrieval_before,
            retrieval_after=False,
            removed_artifacts=removed,
            onboarding_outcome=None,
            summary_path=summary,
        )

    recipes = build_sylph_smoke_recipes(fixtures_dir=fixtures_dir, work_dir=work_dir)
    outcome = run_onboarding_refinement_loop(
        build_sylph_seed_draft(),
        {"source": source_url, "mode": "cold"},
        smoke_recipes=recipes,
        budget=budget,
        command_runner=command_runner,
        install=install,
        skills_definitions_dir=skills_definitions_dir,
        skills_library_dir=skills_library_dir,
        capability_catalog_path=capability_catalog_path,
        tool_cards_dir=tool_cards_dir,
        advisory_catalog_path=advisory_catalog_path,
        install_workflow="cold_novel_tool_case_study",
    )
    retrieval_after = registry_contains_skill(
        skill_name,
        skills_definitions_dir=skills_definitions_dir,
        tool_cards_dir=tool_cards_dir,
    )
    summary = write_cold_onboarding_summary(
        summary_path,
        source_url=source_url,
        fixtures_dir=fixtures_dir,
        removed_artifacts=removed,
        outcome=outcome,
        retrieval_before=retrieval_before,
        retrieval_after=retrieval_after,
    )
    return ColdNovelToolCaseStudyOutcome(
        skill_name=skill_name,
        tool_name="sylph",
        tool_found=True,
        retrieval_before=retrieval_before,
        retrieval_after=retrieval_after,
        removed_artifacts=removed,
        onboarding_outcome=outcome,
        summary_path=summary,
    )


def outcome_to_json_ready(outcome: ColdNovelToolCaseStudyOutcome) -> dict[str, Any]:
    """Render a cold case-study outcome as a JSON-serializable dictionary.

    Args:
        outcome: Structured case-study outcome.

    Returns:
        JSON-ready dictionary.
    """

    onboarding = outcome.onboarding_outcome
    return {
        "skill_name": outcome.skill_name,
        "tool_name": outcome.tool_name,
        "tool_found": outcome.tool_found,
        "retrieval_before": outcome.retrieval_before,
        "retrieval_after": outcome.retrieval_after,
        "removed_artifacts": list(outcome.removed_artifacts),
        "summary_path": str(outcome.summary_path),
        "onboarding_outcome": None
        if onboarding is None
        else {
            "success": onboarding.success,
            "installed": onboarding.installed,
            "iterations": onboarding.iterations,
            "budget_exhausted": onboarding.budget_exhausted,
            "stalled": onboarding.stalled,
            "message": onboarding.message,
            "advisory_path": str(onboarding.advisory_path or ""),
            "smoke_results": [dict(item) for item in onboarding.final_card.smoke_test_results],
        },
    }


def write_case_study_json(output_path: Path, outcome: ColdNovelToolCaseStudyOutcome) -> Path:
    """Write a JSON summary for one cold onboarding case study.

    Args:
        output_path: JSON output path.
        outcome: Structured case-study outcome.

    Returns:
        Path to the written JSON file.
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(outcome_to_json_ready(outcome), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


__all__ = [
    "ColdNovelToolCaseStudyOutcome",
    "build_sylph_seed_draft",
    "build_sylph_smoke_recipes",
    "registry_contains_skill",
    "remove_cold_start_artifacts",
    "run_cold_sylph_case_study",
    "sylph_fixture_paths",
    "write_case_study_json",
    "write_cold_onboarding_summary",
]
