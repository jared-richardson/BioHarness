"""Helpers for runtime repair policy decisions.

These helpers centralize standards-sensitive repair policy choices such as
direct-skill smoke exclusions, unrecoverable signature handling, and the
ordered runtime mutation repair ladder.
"""

from __future__ import annotations

from typing import Any, Callable

RepairResult = tuple[bool, str, dict[str, Any]]
DetailsDict = dict[str, Any]
GuardFn = Callable[[str], dict[str, Any]]
RepairStepFn = Callable[[], tuple[bool, dict[str, Any]]]


def direct_skill_smoke_guard(
    *,
    failure_class: str,
    is_direct_skill_smoke: bool,
    details: DetailsDict,
) -> RepairResult:
    """Block runtime mutation repair for direct skill smoke runs.

    Args:
        failure_class: Failure class being repaired.
        is_direct_skill_smoke: Whether the run is a direct skill smoke run.
        details: Mutable details payload for the repair attempt.

    Returns:
        A repair result tuple. When the guard does not apply, `action` is an
        empty string and the original details are returned unchanged.
    """

    if not is_direct_skill_smoke:
        return False, "", details
    guarded = dict(details)
    guarded.update(
        {
            "why": "direct_skill_smoke_requires_reporting_the_requested_skill_result_without_repair",
            "failure_class": failure_class,
        }
    )
    return False, "direct_skill_smoke_repair_disabled", guarded


def unrecoverable_signature_guard(
    *,
    signatures: set[str],
    details: DetailsDict,
) -> RepairResult:
    """Block runtime repair when signatures indicate unrecoverable bad inputs.

    Args:
        signatures: Normalized failure signatures recorded for the run.
        details: Mutable details payload for the repair attempt.

    Returns:
        A repair result tuple. When no unrecoverable signature is present,
        `action` is an empty string and the original details are returned.
    """

    unrecoverable = {
        "deseq2_all_zero_counts",
        "format_input_error_marker",
        "spatial_coordinates_invalid",
    }
    matched = sorted(unrecoverable.intersection(signatures))
    if not matched:
        return False, "", details
    guarded = dict(details)
    guarded.update(
        {
            "why": "unrecoverable_input_or_count_signature",
            "failure_signatures": matched,
        }
    )
    return False, "unrecoverable_bad_input", guarded


def apply_runtime_mutation_repair_ladder(
    *,
    failure_class: str,
    details: DetailsDict,
    runtime_plan_mutation_guard: GuardFn,
    repair_steps: list[tuple[str, RepairStepFn]],
) -> RepairResult:
    """Apply the ordered runtime mutation repair ladder for one failure class.

    Args:
        failure_class: Failure class being repaired.
        details: Mutable details payload for the repair attempt.
        runtime_plan_mutation_guard: Guard callback controlling whether plan
            mutation repairs are allowed for this failure class.
        repair_steps: Ordered `(action, repair_fn)` entries for the repair ladder.

    Returns:
        A repair result tuple. When no repair applies, `action` is an empty
        string and the original or guard-augmented details are returned.
    """

    if failure_class not in {"runtime_step_failure", "validation_block"}:
        return False, "", details

    mutation_guard = runtime_plan_mutation_guard(failure_class)
    if not mutation_guard.get("allowed", False):
        blocked = dict(details)
        blocked.update(mutation_guard)
        return False, "", blocked

    merged = dict(details)
    for action, repair_fn in repair_steps:
        repaired, repair_details = repair_fn()
        if not repaired:
            continue
        merged.update(repair_details)
        return True, action, merged
    return False, "", merged


__all__ = [
    "apply_runtime_mutation_repair_ladder",
    "direct_skill_smoke_guard",
    "unrecoverable_signature_guard",
]
