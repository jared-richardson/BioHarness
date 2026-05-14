"""Shared helpers for pre-execution validation and repair candidate adoption.

These helpers keep the preexecution-repair mixin focused on repair strategy
selection while centralizing the repeated normalize/validate/install flow for
candidate plans accepted before execution starts.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from bio_harness.core.bash_placeholder_resolution import resolve_bash_placeholders
from scripts.run_agent_e2e_plan_application_support import (
    install_candidate_plan,
    plan_step_count,
    plan_step_diff_summary,
)
from scripts.run_agent_e2e_support import _is_actionable_executable_plan

PlanDict = dict[str, Any]
NormalizePlanFn = Callable[[PlanDict], tuple[PlanDict, PlanDict, PlanDict]]
ValidatePlanFn = Callable[[PlanDict], PlanDict]
AssessSemanticFn = Callable[[PlanDict], PlanDict]


def protocol_repair_strategy(deterministic_meta: PlanDict) -> str:
    """Return the concrete strategy name recorded in deterministic repair metadata."""

    repair_entries = deterministic_meta.get("repairs", [])
    for entry in repair_entries if isinstance(repair_entries, list) else []:
        strategy = str(
            entry.get("strategy", "") if isinstance(entry, dict) else ""
        ).strip()
        if strategy:
            return strategy
    return "guided_patch"


def _placeholder_defaults(*, selected_dir: str) -> dict[str, str]:
    """Return deterministic placeholder defaults available during validation."""

    root = str(selected_dir or "").strip()
    if not root:
        return {}
    return {
        "cwd": root,
        "output_dir": root,
        "results_dir": root,
        "selected_dir": root,
        "workspace_dir": root,
    }


def resolve_bash_placeholders_in_plan(
    plan: PlanDict,
    *,
    path_graph: Any = None,
    selected_dir: str = "",
) -> tuple[PlanDict, list[PlanDict], list[PlanDict]]:
    """Resolve safe template placeholders in ``bash_run`` command text.

    Args:
        plan: Candidate executable plan.
        path_graph: Optional mapping-like artifact lookup surface.
        selected_dir: Selected output directory used for deterministic defaults.

    Returns:
        Tuple of ``(resolved_plan, sidecar_entries, unresolved_issues)``.
    """

    if not isinstance(plan, dict):
        return {}, [], []
    raw_steps = plan.get("plan", [])
    if not isinstance(raw_steps, list):
        return dict(plan), [], []

    resolved_plan = deepcopy(plan)
    steps = resolved_plan.get("plan", [])
    sidecar_entries: list[PlanDict] = []
    unresolved_issues: list[PlanDict] = []
    prior_step_arguments: list[dict[str, Any]] = []
    defaults = _placeholder_defaults(selected_dir=selected_dir)

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        arguments = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        step_id = int(step.get("step_id", index + 1) or (index + 1))
        if tool_name != "bash_run":
            prior_step_arguments.append(dict(arguments))
            continue

        command = str(arguments.get("command", "") or "")
        resolution = resolve_bash_placeholders(
            command,
            prior_step_arguments=prior_step_arguments,
            path_graph=path_graph,
            wrapper_parameter_defaults=defaults,
            selected_dir=selected_dir,
        )
        if resolution.resolved_command != command:
            patched_arguments = dict(arguments)
            patched_arguments["command"] = resolution.resolved_command
            step["arguments"] = patched_arguments
        if resolution.resolutions or resolution.unresolved:
            sidecar_entries.append(
                {
                    "step_id": step_id,
                    "resolved": list(resolution.resolutions),
                    "unresolved": list(resolution.unresolved),
                }
            )
        for token_name in resolution.unresolved:
            unresolved_issues.append(
                {
                    "step_id": step_id,
                    "tool_name": "bash_run",
                    "issue": "unresolved_placeholder",
                    "type": "unresolved_placeholder",
                    "token": token_name,
                    "suggestion": (
                        "Resolve template placeholders such as <reference_fasta> "
                        "to concrete values before emitting command arguments."
                    ),
                }
            )
        prior_step_arguments.append(
            dict(step.get("arguments", {})) if isinstance(step.get("arguments", {}), dict) else {}
        )
    return resolved_plan, sidecar_entries, unresolved_issues


def assess_plan_semantic_guards_with_bash_placeholders(
    *,
    plan: PlanDict,
    assess_semantic_guards: AssessSemanticFn,
    path_graph: Any = None,
    selected_dir: str = "",
) -> tuple[PlanDict, PlanDict, list[PlanDict]]:
    """Resolve bash placeholders before running semantic validation."""

    resolved_plan, sidecar_entries, unresolved_issues = resolve_bash_placeholders_in_plan(
        plan,
        path_graph=path_graph,
        selected_dir=selected_dir,
    )
    validation = assess_semantic_guards(resolved_plan)
    issues = validation.get("issues", []) if isinstance(validation.get("issues", []), list) else []
    merged_issues = list(issues) + list(unresolved_issues)
    merged_validation = dict(validation)
    merged_validation["issues"] = merged_issues
    merged_validation["passed"] = bool(validation.get("passed", False)) and not unresolved_issues
    return resolved_plan, merged_validation, sidecar_entries


def adopt_preexecution_candidate_if_valid(
    *,
    run: PlanDict,
    candidate: Any,
    normalize_plan_for_execution: NormalizePlanFn,
    validate_plan: ValidatePlanFn,
    mark_planned: bool,
    clear_error: bool,
    include_diff_summary: bool,
) -> PlanDict | None:
    """Normalize, validate, and install one repair candidate if it is usable."""

    if not (isinstance(candidate, dict) and _is_actionable_executable_plan(candidate)):
        return None
    normalized, canonical_meta, fc_meta = normalize_plan_for_execution(candidate)
    validation_after = validate_plan(normalized)
    if not validation_after.get("passed", False):
        return None

    before_step_count = plan_step_count(run.get("plan", {}))
    install_candidate_plan(
        run,
        normalized,
        reset_step_state=True,
        mark_planned=mark_planned,
        clear_error=clear_error,
    )
    result: PlanDict = {
        "normalized_plan": normalized,
        "canonicalization": canonical_meta,
        "featurecounts_normalization": fc_meta,
        "validation_after": validation_after,
    }
    if include_diff_summary:
        result["diff_summary"] = plan_step_diff_summary(
            before_step_count=before_step_count,
            after_plan=normalized,
        )
    return result


__all__ = [
    "assess_plan_semantic_guards_with_bash_placeholders",
    "adopt_preexecution_candidate_if_valid",
    "protocol_repair_strategy",
    "resolve_bash_placeholders_in_plan",
]
