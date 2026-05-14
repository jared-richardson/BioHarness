"""Deterministic refinement helpers for onboarding tool cards.

The first refinement pass is intentionally rule-based: it records smoke-test
evidence, updates stable examples and outputs on success, and captures
structured failure guidance on failure. This keeps onboarding auditable while
still improving the persisted tool card over repeated attempts.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import re
from typing import Any, Mapping

from bio_harness.core.onboarding_fixtures import (
    SmokeProgressAssessment,
    SmokeTestRecipe,
    SmokeTestResult,
    assess_smoke_test_progress,
)
from bio_harness.core.tool_cards import ToolCard
from bio_harness.core.tool_onboarding import (
    build_generic_skill_library_stub,
    build_template_skill_library_stub,
)

_DANGEROUS_FLAG_RE = re.compile(r"--(?:force|overwrite|delete|remove|clobber|replace|clean|rm)\b")


def refine_tool_card_from_smoke_result(
    card: ToolCard,
    smoke_result: SmokeTestResult,
    *,
    iteration: int,
    recipe: SmokeTestRecipe | None = None,
) -> ToolCard:
    """Return an updated tool card after one smoke-test attempt.

    Args:
        card: Existing tool card.
        smoke_result: Structured smoke-test outcome.
        iteration: One-based refinement iteration count.
        recipe: Optional smoke-test recipe that produced the result.

    Returns:
        Updated immutable tool card.
    """

    smoke_entries = list(card.smoke_test_results)
    assessment = assess_smoke_test_progress(recipe, smoke_result) if recipe is not None else None
    focus = _refinement_focus(smoke_result, assessment=assessment)
    smoke_entries.append(
        _smoke_result_payload(
            smoke_result,
            assessment=assessment,
            focus=focus,
        )
    )
    history = list(card.refinement_history)
    history.append(
        _history_entry(
            smoke_result,
            iteration=iteration,
            recipe=recipe,
            assessment=assessment,
            focus=focus,
        )
    )

    canonical_outputs = list(card.canonical_outputs)
    for token in _smoke_output_tokens(smoke_result):
        if token and token not in canonical_outputs:
            canonical_outputs.append(token)

    common_errors = list(card.common_errors)
    if not smoke_result.passed:
        error_entry = _error_entry(smoke_result, assessment=assessment, focus=focus)
        if error_entry and error_entry not in common_errors:
            common_errors.append(error_entry)

    dangerous_flags = list(card.dangerous_flags)
    for token in _extract_dangerous_flags_from_command(smoke_result.command):
        if token not in dangerous_flags:
            dangerous_flags.append(token)

    safe_example = card.safe_example
    if smoke_result.passed and smoke_result.command.strip():
        safe_example = smoke_result.command.strip()

    return replace(
        card,
        safe_example=safe_example,
        canonical_outputs=tuple(canonical_outputs),
        dangerous_flags=tuple(dangerous_flags),
        common_errors=tuple(common_errors),
        smoke_test_results=tuple(smoke_entries),
        refinement_history=tuple(history),
    )


def apply_refined_card_to_draft(
    draft: Mapping[str, Any],
    card: ToolCard,
) -> dict[str, Any]:
    """Project refined card fields back into a skill draft and wrapper stub.

    Args:
        draft: Existing skill draft.
        card: Refined tool card.

    Returns:
        Updated draft with deterministic wrapper code refreshed.
    """

    updated = dict(draft)
    updated["when_to_use"] = card.when_to_use
    updated["when_not_to_use"] = card.when_not_to_use
    updated["output_types"] = list(card.canonical_outputs)
    if card.safe_example.strip():
        updated["command_template"] = card.safe_example.strip()

    skill_name = str(updated.get("skill_name", "") or updated.get("name", "")).strip()
    command_template = str(updated.get("command_template", "") or "").strip()
    if command_template:
        updated["wrapper_code"] = build_template_skill_library_stub(skill_name, command_template)
    else:
        default_tool = str(
            list(updated.get("tools_required", []) or [skill_name])[0]
            if isinstance(updated.get("tools_required", []), list)
            else skill_name
        ).strip()
        updated["wrapper_code"] = build_generic_skill_library_stub(skill_name, default_tool)
    return updated


def _smoke_result_payload(
    smoke_result: SmokeTestResult,
    *,
    assessment: SmokeProgressAssessment | None = None,
    focus: str = "",
) -> dict[str, Any]:
    """Convert a smoke result into a compact persisted mapping."""

    payload = asdict(smoke_result)
    payload["expected_outputs"] = list(smoke_result.expected_outputs)
    payload["produced_outputs"] = list(smoke_result.produced_outputs)
    if assessment is not None:
        payload["progress_assessment"] = asdict(assessment)
    if focus:
        payload["refinement_focus"] = focus
    return payload


def _history_entry(
    smoke_result: SmokeTestResult,
    *,
    iteration: int,
    recipe: SmokeTestRecipe | None = None,
    assessment: SmokeProgressAssessment | None = None,
    focus: str = "",
) -> str:
    """Build one human-readable refinement history entry."""

    status = "passed" if smoke_result.passed else f"failed ({smoke_result.failure_reason})"
    expected = ", ".join(_smoke_output_tokens(smoke_result)) or "no declared outputs"
    recipe_desc = ""
    if recipe is not None and recipe.description.strip():
        recipe_desc = f"; recipe={recipe.description.strip()}"
    score_desc = ""
    if assessment is not None:
        score_desc = f"; progress_score={assessment.score}"
    focus_desc = f"; focus={focus}" if focus else ""
    return (
        f"iteration {iteration}: smoke test `{smoke_result.name}` {status}; "
        f"outputs={expected}; command=`{smoke_result.command}`{score_desc}{focus_desc}{recipe_desc}"
    )


def _error_entry(
    smoke_result: SmokeTestResult,
    *,
    assessment: SmokeProgressAssessment | None = None,
    focus: str = "",
) -> dict[str, str]:
    """Build one structured common-error entry from a failed smoke test."""

    evidence = str(smoke_result.stderr or smoke_result.stdout).strip()
    if not evidence:
        evidence = smoke_result.failure_reason
    entry = {
        "pattern": evidence[:240],
        "cause": smoke_result.failure_reason,
        "fix": _fix_hint(smoke_result, assessment=assessment),
    }
    if focus:
        entry["focus"] = focus
    return entry


def _smoke_output_tokens(smoke_result: SmokeTestResult) -> tuple[str, ...]:
    """Return stable canonical output tokens derived from one smoke result."""

    tokens: list[str] = []
    for raw in list(smoke_result.expected_outputs) + list(smoke_result.produced_outputs):
        token = _canonical_output_token(raw)
        if token and token not in tokens:
            tokens.append(token)
    return tuple(tokens)


def _canonical_output_token(raw: str) -> str:
    """Normalize one output token into a stable, non-temporary label."""

    token = str(raw).strip()
    if not token:
        return ""
    if "/" in token or "\\" in token:
        return Path(token).name.strip()
    return token


def _extract_dangerous_flags_from_command(command: str) -> tuple[str, ...]:
    """Extract destructive or overwrite flags from one rendered command."""

    flags: list[str] = []
    for match in _DANGEROUS_FLAG_RE.finditer(str(command or "")):
        token = match.group(0)
        if token not in flags:
            flags.append(token)
    return tuple(flags)


def _fix_hint(
    smoke_result: SmokeTestResult,
    *,
    assessment: SmokeProgressAssessment | None = None,
) -> str:
    """Derive a deterministic fix hint from a failed smoke test."""

    failure_reason = str(smoke_result.failure_reason or "")
    stderr = str(smoke_result.stderr or "").lower()
    stdout = str(smoke_result.stdout or "").lower()
    combined = f"{stdout}\n{stderr}"
    outputs = ", ".join(_smoke_output_tokens(smoke_result))
    remaining_outputs = ", ".join(_missing_output_tokens(smoke_result))

    if smoke_result.timed_out or failure_reason == "timed_out":
        return "reduce smoke-test fixture size or runtime expectations before retrying"
    if failure_reason == "wrapper_render_failed":
        return "repair wrapper parameter names or command-template placeholders before retrying"
    if failure_reason == "missing_expected_outputs":
        if assessment is not None and assessment.return_code_matched:
            if assessment.output_hits > 0 and remaining_outputs:
                return (
                    "preserve the existing command behavior and add the remaining expected outputs: "
                    f"{remaining_outputs}"
                )
            if outputs:
                return f"focus on output-path binding so the command writes {outputs} to the requested locations"
        if outputs:
            return f"ensure the command writes {outputs} to the requested output locations"
        return "ensure the command writes the expected output artifacts"
    if failure_reason.startswith("missing_expected_substring:"):
        if assessment is not None and assessment.output_total == assessment.output_hits and assessment.return_code_matched:
            return "outputs are present; align stdout/stderr expectations or emit the required marker text"
        return "verify smoke-test expectations for stdout/stderr markers or adjust the command behavior"
    if failure_reason.startswith("forbidden_substring_present:"):
        return "remove the conflicting flag or error-producing behavior from the command template"
    if failure_reason.startswith("unexpected_return_code:"):
        if assessment is not None and assessment.output_hits > 0:
            return "the command is partly working; inspect non-zero exit handling or warnings after outputs are written"
    if "unknown option" in combined or "unrecognized option" in combined:
        return "remove unsupported flags or update the command template to match the tool help text"
    if "missing index" in combined or "no index" in combined:
        return "build the required index before rerunning the smoke test"
    if "missing" in combined:
        return "verify required inputs, references, and derived files exist before rerunning"
    return "review smoke-test inputs, outputs, and command-template expectations"


def _refinement_focus(
    smoke_result: SmokeTestResult,
    *,
    assessment: SmokeProgressAssessment | None = None,
) -> str:
    """Return the main refinement focus for a failed smoke-test attempt."""

    failure_reason = str(smoke_result.failure_reason or "")
    combined = f"{smoke_result.stdout}\n{smoke_result.stderr}".lower()
    if smoke_result.passed:
        return "complete"
    if smoke_result.timed_out or failure_reason == "timed_out":
        return "timeout_budget"
    if failure_reason == "wrapper_render_failed":
        return "wrapper_rendering"
    if "unknown option" in combined or "unrecognized option" in combined:
        return "command_flags"
    if failure_reason.startswith("forbidden_substring_present:"):
        return "forbidden_output"
    if "missing index" in combined or "no index" in combined or "missing" in combined:
        return "input_prerequisites"
    if failure_reason.startswith("missing_expected_substring:"):
        return "output_markers"
    if failure_reason == "missing_expected_outputs":
        if assessment is not None and assessment.output_hits > 0:
            return "output_completeness"
        return "output_paths"
    if failure_reason.startswith("unexpected_return_code:"):
        return "return_code_behavior"
    return "command_template"


def _missing_output_tokens(smoke_result: SmokeTestResult) -> tuple[str, ...]:
    """Return the expected output tokens that were still missing."""

    expected = set(_smoke_output_tokens(smoke_result))
    produced = {
        _canonical_output_token(path)
        for path in smoke_result.produced_outputs
        if _canonical_output_token(path)
    }
    missing = [token for token in _smoke_output_tokens(smoke_result) if token in expected and token not in produced]
    return tuple(missing)


__all__ = [
    "apply_refined_card_to_draft",
    "refinement_state_signature",
    "refine_tool_card_from_smoke_result",
]


def refinement_state_signature(card: ToolCard) -> tuple[Any, ...]:
    """Return a stable signature of meaningful refinement state.

    Evidence lists such as `smoke_test_results` and `refinement_history` are
    intentionally excluded so the signature only changes when the tool card's
    actionable guidance changes.
    """

    error_signature = tuple(
        (
            str(entry.get("pattern", "")),
            str(entry.get("cause", "")),
            str(entry.get("fix", "")),
        )
        for entry in card.common_errors
    )
    return (
        card.safe_example,
        tuple(card.canonical_outputs),
        tuple(card.dangerous_flags),
        error_signature,
        card.when_to_use,
        card.when_not_to_use,
    )
