"""Deterministic wrapper-variant generation and selection for onboarding.

This module builds a small set of candidate wrapper implementations from one
onboarding draft, scores them on bounded smoke tests, and selects the best
performing wrapper before the broader refinement loop begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Callable, Mapping, Sequence

from bio_harness.core.onboarding_fixtures import (
    SmokeTestRecipe,
    SmokeTestResult,
    assess_smoke_test_progress,
    run_wrapper_smoke_test,
)
from bio_harness.core.skill_generator import validate_skill
from bio_harness.core.tool_onboarding import (
    build_generic_skill_library_stub,
    build_template_skill_library_stub,
)

_VARIANT_PRIORITY = {
    "original": 0,
    "template_stub": 1,
    "generic_stub": 2,
}


@dataclass(frozen=True)
class WrapperVariantCandidate:
    """One deterministic wrapper candidate derived from an onboarding draft."""

    label: str
    draft: dict[str, Any]


@dataclass(frozen=True)
class WrapperVariantEvaluation:
    """Scored smoke-test outcome for one wrapper candidate."""

    label: str
    pass_count: int
    total_score: int
    evaluated_recipes: int
    wrapper_length: int
    results: tuple[SmokeTestResult, ...]


@dataclass(frozen=True)
class WrapperVariantSelection:
    """Selected best wrapper candidate and audit trail for runner-ups."""

    best_label: str
    best_draft: dict[str, Any]
    evaluations: tuple[WrapperVariantEvaluation, ...]
    runner_up_wrappers: tuple[str, ...]


def build_wrapper_variants(
    draft: Mapping[str, Any],
) -> tuple[WrapperVariantCandidate, ...]:
    """Build deterministic wrapper candidates from one onboarding draft.

    Args:
        draft: Source onboarding draft.

    Returns:
        Deduplicated wrapper candidates with valid wrapper syntax.
    """

    base = dict(draft)
    skill_name = str(base.get("skill_name", "") or base.get("name", "")).strip()
    tool_name = str(
        list(base.get("tools_required", []) or [skill_name])[0]
        if isinstance(base.get("tools_required", []), list)
        else skill_name
    ).strip()
    command_template = str(base.get("command_template", "") or "").strip()

    candidates: list[WrapperVariantCandidate] = [
        WrapperVariantCandidate(label="original", draft=base),
    ]
    if command_template and skill_name:
        template_draft = dict(base)
        template_draft["wrapper_code"] = build_template_skill_library_stub(skill_name, command_template)
        candidates.append(WrapperVariantCandidate(label="template_stub", draft=template_draft))
    if skill_name and tool_name:
        generic_draft = dict(base)
        generic_draft["wrapper_code"] = build_generic_skill_library_stub(skill_name, tool_name)
        candidates.append(WrapperVariantCandidate(label="generic_stub", draft=generic_draft))
    return _dedupe_valid_candidates(candidates)


def select_best_wrapper_variant(
    draft: Mapping[str, Any],
    *,
    smoke_recipes: Sequence[SmokeTestRecipe],
    command_runner: Callable[..., Any] | None = None,
    max_recipes: int = 2,
) -> WrapperVariantSelection:
    """Select the best deterministic wrapper candidate for onboarding.

    Args:
        draft: Source onboarding draft.
        smoke_recipes: Smoke-test recipes used for bounded scoring.
        command_runner: Optional injected command runner.
        max_recipes: Maximum number of recipes evaluated per candidate.

    Returns:
        Selection result containing the chosen draft and runner-up summaries.
    """

    candidates = build_wrapper_variants(draft)
    if len(candidates) == 1 or not smoke_recipes:
        only = candidates[0]
        evaluation = WrapperVariantEvaluation(
            label=only.label,
            pass_count=0,
            total_score=0,
            evaluated_recipes=0,
            wrapper_length=len(str(only.draft.get("wrapper_code", "") or "")),
            results=(),
        )
        return WrapperVariantSelection(
            best_label=only.label,
            best_draft=dict(only.draft),
            evaluations=(evaluation,),
            runner_up_wrappers=(),
        )

    limited_recipes = tuple(smoke_recipes[: max(1, int(max_recipes))])
    evaluations: list[WrapperVariantEvaluation] = []
    for candidate in candidates:
        evaluations.append(
            _evaluate_variant_candidate(
                candidate,
                smoke_recipes=limited_recipes,
                command_runner=command_runner,
            )
        )

    ranked = sorted(
        evaluations,
        key=lambda item: (
            -item.pass_count,
            -item.total_score,
            item.wrapper_length,
            _VARIANT_PRIORITY.get(item.label, 99),
        ),
    )
    best = ranked[0]
    by_label = {candidate.label: candidate for candidate in candidates}
    runner_ups = tuple(_runner_up_summary(item) for item in ranked[1:])
    return WrapperVariantSelection(
        best_label=best.label,
        best_draft=dict(by_label[best.label].draft),
        evaluations=tuple(ranked),
        runner_up_wrappers=runner_ups,
    )


def _evaluate_variant_candidate(
    candidate: WrapperVariantCandidate,
    *,
    smoke_recipes: Sequence[SmokeTestRecipe],
    command_runner: Callable[..., Any] | None = None,
) -> WrapperVariantEvaluation:
    """Run bounded smoke scoring for one wrapper candidate."""

    results: list[SmokeTestResult] = []
    total_score = 0
    pass_count = 0
    for recipe in smoke_recipes:
        _cleanup_expected_outputs(recipe.expected_outputs)
        result = run_wrapper_smoke_test(
            candidate.draft,
            recipe,
            command_runner=command_runner,
        )
        results.append(result)
        total_score += assess_smoke_test_progress(recipe, result).score
        if result.passed:
            pass_count += 1
            continue
        break
    return WrapperVariantEvaluation(
        label=candidate.label,
        pass_count=pass_count,
        total_score=total_score,
        evaluated_recipes=len(results),
        wrapper_length=len(str(candidate.draft.get("wrapper_code", "") or "")),
        results=tuple(results),
    )


def _dedupe_valid_candidates(
    candidates: Sequence[WrapperVariantCandidate],
) -> tuple[WrapperVariantCandidate, ...]:
    """Return stable-order valid candidates with duplicate wrappers removed."""

    deduped: list[WrapperVariantCandidate] = []
    seen_wrappers: set[str] = set()
    for candidate in candidates:
        wrapper_code = str(candidate.draft.get("wrapper_code", "") or "")
        if wrapper_code in seen_wrappers:
            continue
        valid, _ = validate_skill(candidate.draft)
        if not valid:
            continue
        seen_wrappers.add(wrapper_code)
        deduped.append(candidate)
    return tuple(deduped or candidates[:1])


def _cleanup_expected_outputs(paths: Sequence[str]) -> None:
    """Remove expected outputs before one candidate evaluation."""

    for raw in paths:
        path = Path(str(raw or "")).expanduser()
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def _runner_up_summary(item: WrapperVariantEvaluation) -> str:
    """Render one compact runner-up summary for tool-card audit storage."""

    return (
        f"{item.label}: passes={item.pass_count}, score={item.total_score}, "
        f"recipes={item.evaluated_recipes}"
    )


__all__ = [
    "WrapperVariantCandidate",
    "WrapperVariantEvaluation",
    "WrapperVariantSelection",
    "build_wrapper_variants",
    "select_best_wrapper_variant",
]
