"""Bounded onboarding refinement loop for unfamiliar tools.

This module coordinates draft validation, smoke-test execution, deterministic
tool-card refinement, and optional installation of the final onboarded skill.
The loop is intentionally bounded and auditable so it can improve onboarding
artifacts without changing strict benchmark behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from bio_harness.core.onboarding_advisories import (
    build_tool_advisory_proposal,
    persist_tool_advisory_proposal,
)
from bio_harness.core.onboarding_fixtures import (
    SmokeProgressAssessment,
    SmokeTestRecipe,
    SmokeTestResult,
    assess_smoke_test_progress,
    run_wrapper_smoke_test,
)
from bio_harness.core.tool_card_refinement import (
    apply_refined_card_to_draft,
    refinement_state_signature,
    refine_tool_card_from_smoke_result,
)
from bio_harness.core.onboarding_variants import select_best_wrapper_variant
from bio_harness.core.tool_cards import ToolCard, tool_card_from_draft
from bio_harness.core.tool_onboarding import install_tool_onboarding_draft
from bio_harness.core.skill_generator import validate_skill

_FOCUS_STALL_THRESHOLDS = {
    "output_paths": 2,
    "output_completeness": 2,
    "output_markers": 2,
    "return_code_behavior": 2,
}
_FOCUS_RECIPE_ALIASES = {
    "output_paths": frozenset({"output_paths", "output_completeness"}),
    "output_completeness": frozenset({"output_completeness", "output_paths"}),
    "output_markers": frozenset({"output_markers"}),
    "command_flags": frozenset({"command_flags", "return_code_behavior"}),
    "return_code_behavior": frozenset({"return_code_behavior", "command_flags"}),
    "input_prerequisites": frozenset({"input_prerequisites"}),
    "forbidden_output": frozenset({"forbidden_output"}),
    "timeout_budget": frozenset({"timeout_budget"}),
    "wrapper_rendering": frozenset({"wrapper_rendering"}),
}


@dataclass(frozen=True)
class OnboardingBudget:
    """Hard limits for one onboarding refinement session."""

    max_iterations: int = 3
    max_total_seconds: int = 60
    max_subprocess_calls_per_cycle: int = 5


@dataclass(frozen=True)
class OnboardingOutcome:
    """Summary of one bounded onboarding refinement session."""

    success: bool
    installed: bool
    iterations: int
    budget_exhausted: bool
    stalled: bool
    message: str
    final_draft: dict[str, Any]
    final_card: ToolCard
    smoke_results: tuple[SmokeTestResult, ...]
    advisory_path: Path | None = None


def run_onboarding_refinement_loop(
    draft: Mapping[str, Any],
    source_meta: Mapping[str, Any],
    *,
    manual_summary: Mapping[str, Any] | None = None,
    smoke_recipes: Sequence[SmokeTestRecipe] = (),
    budget: OnboardingBudget | None = None,
    command_runner: Callable[..., Any] | None = None,
    time_fn: Callable[[], float] | None = None,
    install: bool = False,
    skills_definitions_dir: Path | None = None,
    skills_library_dir: Path | None = None,
    capability_catalog_path: Path | None = None,
    tool_cards_dir: Path | None = None,
    advisory_catalog_path: Path | None = None,
    advisory_repeat_threshold: int = 2,
    select_variants: bool = True,
    install_workflow: str = "tool_onboarding_refinement",
) -> OnboardingOutcome:
    """Run a bounded onboarding refinement loop.

    Args:
        draft: Initial skill draft.
        source_meta: Source metadata for onboarding.
        manual_summary: Optional doc-derived enrichment summary.
        smoke_recipes: Ordered smoke-test recipes.
        budget: Optional onboarding budget override.
        command_runner: Optional injected command runner.
        time_fn: Optional clock for deterministic tests.
        install: Whether to install the final refined draft on success.
        skills_definitions_dir: Optional skill definition directory.
        skills_library_dir: Optional skill library directory.
        capability_catalog_path: Optional capability catalog path.
        tool_cards_dir: Optional tool-card persistence directory.
        advisory_catalog_path: Optional repair-advisory catalog to update.
        advisory_repeat_threshold: Minimum repeated pattern count before an
            onboarding advisory is persisted.
        select_variants: Whether to run deterministic wrapper-variant
            selection before iterative refinement starts.
        install_workflow: Install workflow label written to metadata.

    Returns:
        Structured onboarding outcome.
    """

    policy = budget or OnboardingBudget()
    clock = time_fn or time.monotonic
    current_draft = dict(draft)
    valid, error = validate_skill(current_draft)
    if not valid:
        card = tool_card_from_draft(
            current_draft,
            source_meta=source_meta,
            manual_summary=manual_summary,
            validated=False,
        )
        return OnboardingOutcome(
            success=False,
            installed=False,
            iterations=0,
            budget_exhausted=False,
            stalled=False,
            message=f"Initial draft validation failed: {error}",
            final_draft=current_draft,
            final_card=card,
            smoke_results=(),
        )

    card = tool_card_from_draft(
        current_draft,
        source_meta=source_meta,
        manual_summary=manual_summary,
        validated=not smoke_recipes,
    )
    if select_variants and _should_run_variant_selection(smoke_recipes):
        selection = select_best_wrapper_variant(
            current_draft,
            smoke_recipes=smoke_recipes,
            command_runner=command_runner,
        )
        current_draft = dict(selection.best_draft)
        card = tool_card_from_draft(
            current_draft,
            source_meta=source_meta,
            manual_summary=manual_summary,
            validated=False,
        )
        if selection.runner_up_wrappers:
            card = replace(
                card,
                runner_up_wrappers=selection.runner_up_wrappers,
                probe_observations=(
                    {
                        "variant_selection": {
                            "best_label": selection.best_label,
                            "evaluations": [
                                {
                                    "label": item.label,
                                    "pass_count": item.pass_count,
                                    "total_score": item.total_score,
                                    "evaluated_recipes": item.evaluated_recipes,
                                }
                                for item in selection.evaluations
                            ],
                        }
                    },
                ),
            )
    results: list[SmokeTestResult] = []
    started = clock()
    budget_exhausted = False
    stalled = False
    success = not smoke_recipes
    previous_assessment: SmokeProgressAssessment | None = None
    previous_signature: tuple[Any, ...] | None = None
    previous_focus = ""
    non_improving_focus_repeats = 0
    current_focus = ""
    pending_recipes = list(smoke_recipes)
    latest_smoke_result: SmokeTestResult | None = None

    for iteration in range(1, policy.max_iterations + 1):
        if not pending_recipes:
            break
        recipe = _select_next_recipe(pending_recipes, current_focus=current_focus)
        pending_recipes.remove(recipe)
        recipe = _adapt_recipe_for_focus(
            recipe,
            draft=current_draft,
            current_focus=current_focus,
            latest_smoke_result=latest_smoke_result,
        )
        if recipe.subprocess_calls > policy.max_subprocess_calls_per_cycle:
            budget_exhausted = True
            break
        if clock() - started > policy.max_total_seconds:
            budget_exhausted = True
            break

        smoke_result = run_wrapper_smoke_test(
            current_draft,
            recipe,
            command_runner=command_runner,
        )
        results.append(smoke_result)
        assessment = assess_smoke_test_progress(recipe, smoke_result)
        card = refine_tool_card_from_smoke_result(
            card,
            smoke_result,
            iteration=iteration,
            recipe=recipe,
        )
        current_draft = apply_refined_card_to_draft(current_draft, card)
        current_signature = refinement_state_signature(card)
        current_focus = _result_focus(card)
        if smoke_result.passed:
            if not recipe.diagnostic_only:
                success = True
                break
            current_focus = ""
        no_progress = (
            previous_assessment is not None
            and assessment.score <= previous_assessment.score
            and previous_signature == current_signature
        )
        if no_progress:
            if current_focus == previous_focus:
                non_improving_focus_repeats += 1
            else:
                non_improving_focus_repeats = 1
                previous_focus = current_focus
        else:
            non_improving_focus_repeats = 0
            previous_focus = current_focus
        if no_progress and non_improving_focus_repeats >= _focus_stall_threshold(current_focus):
            stalled = True
            break
        previous_assessment = assessment
        previous_signature = current_signature
        latest_smoke_result = smoke_result
    else:
        if smoke_recipes and results and not results[-1].passed:
            budget_exhausted = len(results) >= policy.max_iterations

    if smoke_recipes and not success and clock() - started > policy.max_total_seconds:
        budget_exhausted = True

    advisory_path = _maybe_persist_onboarding_advisory(
        card,
        advisory_catalog_path=advisory_catalog_path,
        advisory_repeat_threshold=advisory_repeat_threshold,
    )

    installed = False
    if success and install:
        if not (skills_definitions_dir and skills_library_dir and capability_catalog_path):
            return OnboardingOutcome(
                success=False,
                installed=False,
                iterations=len(results),
                budget_exhausted=budget_exhausted,
                stalled=stalled,
                message="Install requested without required onboarding directories.",
                final_draft=current_draft,
                final_card=card,
                smoke_results=tuple(results),
                advisory_path=advisory_path,
            )
        installed, message = install_tool_onboarding_draft(
            current_draft,
            source_meta,
            manual_summary=manual_summary,
            tool_card=card,
            skills_definitions_dir=skills_definitions_dir,
            skills_library_dir=skills_library_dir,
            capability_catalog_path=capability_catalog_path,
            tool_cards_dir=tool_cards_dir,
            install_workflow=install_workflow,
        )
        return OnboardingOutcome(
            success=success and installed,
            installed=installed,
            iterations=len(results),
            budget_exhausted=budget_exhausted,
            stalled=stalled,
            message=message if installed else f"Install failed: {message}",
            final_draft=current_draft,
            final_card=card,
            smoke_results=tuple(results),
            advisory_path=advisory_path,
        )

    if success:
        message = "Smoke-test refinement passed."
    elif stalled:
        message = "Smoke-test refinement stopped after repeated identical failures."
    elif budget_exhausted:
        message = "Smoke-test refinement stopped after budget exhaustion."
    else:
        message = "Smoke-test refinement failed without exhausting the budget."
    return OnboardingOutcome(
        success=success,
        installed=False,
        iterations=len(results),
        budget_exhausted=budget_exhausted,
        stalled=stalled,
        message=message,
        final_draft=current_draft,
        final_card=card,
        smoke_results=tuple(results),
        advisory_path=advisory_path,
    )


def _result_focus(card: ToolCard) -> str:
    """Return the latest recorded refinement focus from a tool card."""

    if not card.common_errors:
        return "complete"
    latest = card.common_errors[-1]
    return str(latest.get("focus", "") or "").strip()


def _focus_stall_threshold(focus: str) -> int:
    """Return the allowed count of non-improving repeats for one focus."""

    return _FOCUS_STALL_THRESHOLDS.get(str(focus or "").strip(), 1)


def _select_next_recipe(
    pending_recipes: Sequence[SmokeTestRecipe],
    *,
    current_focus: str,
) -> SmokeTestRecipe:
    """Select the next recipe, prioritizing focus-matching coverage."""

    focus = str(current_focus or "").strip()
    if not focus:
        return pending_recipes[0]
    desired_tags = _FOCUS_RECIPE_ALIASES.get(focus, frozenset({focus}))
    for recipe in pending_recipes:
        recipe_tags = {str(tag).strip() for tag in recipe.focus_tags if str(tag).strip()}
        if recipe_tags & desired_tags:
            return recipe
    return pending_recipes[0]


def _adapt_recipe_for_focus(
    recipe: SmokeTestRecipe,
    *,
    draft: Mapping[str, Any],
    current_focus: str,
    latest_smoke_result: SmokeTestResult | None,
) -> SmokeTestRecipe:
    """Return a focus-adapted smoke recipe when deterministic narrowing helps.

    Adaptations remain conservative and only narrow expectations. They never
    change the command kwargs or increase execution scope.
    """

    focus = str(current_focus or "").strip()
    if not focus or latest_smoke_result is None:
        return recipe
    if focus in {"output_paths", "output_completeness"}:
        missing_outputs = _missing_expected_outputs(recipe, latest_smoke_result)
        if missing_outputs and missing_outputs != recipe.expected_outputs:
            return replace(
                recipe,
                expected_outputs=missing_outputs,
                description=_append_adaptation_note(
                    recipe.description,
                    f"adapted for {focus}: narrowed to missing outputs",
                ),
            )
        return recipe
    if focus == "output_markers":
        missing_markers = _missing_expected_substrings(recipe, latest_smoke_result)
        expected_outputs = recipe.expected_outputs
        if set(latest_smoke_result.produced_outputs) >= set(recipe.expected_outputs):
            expected_outputs = ()
        if (
            missing_markers and missing_markers != recipe.expected_substrings
        ) or expected_outputs != recipe.expected_outputs:
            return replace(
                recipe,
                expected_outputs=expected_outputs,
                expected_substrings=missing_markers or recipe.expected_substrings,
                description=_append_adaptation_note(
                    recipe.description,
                    "adapted for output_markers: narrowed to missing markers",
                ),
            )
        return recipe
    if focus in {"input_prerequisites", "command_flags", "wrapper_rendering"}:
        narrowed_kwargs = _required_recipe_kwargs(draft, recipe)
        if (
            narrowed_kwargs != dict(recipe.kwargs)
            or recipe.expected_outputs
            or recipe.expected_substrings
            or not recipe.diagnostic_only
        ):
            return replace(
                recipe,
                kwargs=narrowed_kwargs,
                expected_outputs=(),
                expected_substrings=(),
                diagnostic_only=True,
                description=_append_adaptation_note(
                    recipe.description,
                    f"adapted for {focus}: diagnostic required-args probe",
                ),
            )
        return recipe
    return recipe


def _missing_expected_outputs(
    recipe: SmokeTestRecipe,
    smoke_result: SmokeTestResult,
) -> tuple[str, ...]:
    """Return expected outputs that were still missing in the last attempt."""

    produced = {str(path) for path in smoke_result.produced_outputs}
    return tuple(str(path) for path in recipe.expected_outputs if str(path) not in produced)


def _missing_expected_substrings(
    recipe: SmokeTestRecipe,
    smoke_result: SmokeTestResult,
) -> tuple[str, ...]:
    """Return expected output markers that were still missing."""

    combined_output = f"{smoke_result.stdout}\n{smoke_result.stderr}"
    return tuple(str(token) for token in recipe.expected_substrings if str(token) not in combined_output)


def _append_adaptation_note(description: str, note: str) -> str:
    """Append one adaptation note without duplicating existing text."""

    base = str(description or "").strip()
    suffix = f"[{note}]"
    if suffix in base:
        return base
    if not base:
        return suffix
    return f"{base} {suffix}"


def _required_recipe_kwargs(
    draft: Mapping[str, Any],
    recipe: SmokeTestRecipe,
) -> dict[str, Any]:
    """Return a kwargs subset containing only required declared parameters.

    The `command` kwarg is preserved whenever present because it represents a
    fully specified override supplied by the caller.
    """

    parameters = draft.get("parameters", {})
    if not isinstance(parameters, Mapping):
        return dict(recipe.kwargs)
    required_names = {
        str(name).strip()
        for name, spec in parameters.items()
        if str(name).strip() and isinstance(spec, Mapping) and bool(spec.get("required", False))
    }
    if not required_names:
        return dict(recipe.kwargs)
    narrowed: dict[str, Any] = {}
    for key, value in dict(recipe.kwargs).items():
        token = str(key).strip()
        if token == "command" or token in required_names:
            narrowed[token] = value
    return narrowed or dict(recipe.kwargs)


def _maybe_persist_onboarding_advisory(
    card: ToolCard,
    *,
    advisory_catalog_path: Path | None,
    advisory_repeat_threshold: int,
) -> Path | None:
    """Persist a repeated onboarding lesson into the advisory catalog.

    Args:
        card: Final refined tool card for the onboarding session.
        advisory_catalog_path: Advisory catalog to update. When omitted, the
            refinement loop stays side-effect free.
        advisory_repeat_threshold: Minimum repeated failure count required to
            write an advisory proposal.

    Returns:
        Written advisory catalog path, or `None` when no proposal qualified.
    """

    if advisory_catalog_path is None:
        return None
    proposal = build_tool_advisory_proposal(
        card.name,
        card,
        min_repeats=advisory_repeat_threshold,
    )
    return persist_tool_advisory_proposal(
        proposal,
        catalog_path=advisory_catalog_path,
    )


def _should_run_variant_selection(
    smoke_recipes: Sequence[SmokeTestRecipe],
) -> bool:
    """Return whether the provided recipes can meaningfully score variants."""

    for recipe in smoke_recipes:
        if recipe.diagnostic_only:
            continue
        if recipe.expected_outputs or recipe.expected_substrings or recipe.forbidden_substrings:
            return True
    return False


__all__ = [
    "OnboardingBudget",
    "OnboardingOutcome",
    "run_onboarding_refinement_loop",
]
