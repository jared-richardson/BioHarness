"""Shared helpers for post-plan runtime validation before execution.

These helpers keep the plan-validation mixin focused on orchestration while
preserving the existing deterministic fallback and FASTQ-rebinding behavior
used before the executor starts.
"""

from __future__ import annotations

from typing import Any, Callable

from scripts.run_agent_e2e_plan_application_support import (
    install_candidate_plan,
    plans_are_distinct,
)
from scripts.run_agent_e2e_support import (
    _collect_planned_output_paths,
    _emit,
)

PlanDict = dict[str, Any]
AssessContractFn = Callable[[PlanDict, PlanDict], PlanDict]
AppendEventFn = Callable[..., None]


def apply_runtime_fallback_if_distinct(
    *,
    run: dict[str, Any],
    current_plan: dict[str, Any] | None,
    contract: dict[str, Any],
    fallback_plan: dict[str, Any] | None,
    fallback_action: str,
    fallback_details: dict[str, Any],
    message: str,
    detail_key: str,
    detail_value: list[str],
    quiet: bool,
    assess_contract_for_plan: AssessContractFn,
    append_event: AppendEventFn,
) -> bool:
    """Install a deterministic fallback plan when it differs from the current one."""

    if fallback_plan is None:
        return False
    if not plans_are_distinct(current_plan, fallback_plan):
        return False
    install_candidate_plan(
        run,
        fallback_plan,
        reset_step_state=False,
        mark_planned=False,
        clear_error=False,
    )
    run["contract_validation"] = assess_contract_for_plan(
        fallback_plan,
        contract,
    )
    _emit(message, quiet=quiet)
    append_event(
        step_id=None,
        agent="RecoveryAgent",
        event_type="REPAIR_APPLIED",
        severity="warning",
        payload={
            "run_id": run.get("run_uid", ""),
            "failure_class": "runtime_step_failure",
            "attempt": 0,
            "action": f"preexecution_{fallback_action}",
            "details": {detail_key: detail_value, **fallback_details},
        },
    )
    return True


def apply_fastq_rebinding_if_changed(
    *,
    run: dict[str, Any],
    repaired_plan: dict[str, Any],
    repair_meta: dict[str, Any],
    quiet: bool,
    append_event: AppendEventFn,
) -> bool:
    """Install a repaired FASTQ-bound plan when rebinding changed it."""

    if not repair_meta.get("changed", False):
        return False
    run["plan"] = repaired_plan
    _emit(
        "Rebound guessed FASTQ inputs to discovered files: "
        f"{repair_meta.get('diff_summary', {})}",
        quiet=quiet,
    )
    append_event(
        step_id=None,
        agent="RecoveryAgent",
        event_type="REPAIR_APPLIED",
        severity="info",
        payload={
            "run_id": run.get("run_uid", ""),
            "failure_class": "runtime_step_failure",
            "attempt": 0,
            "action": "rebind_missing_fastq_inputs",
            "details": repair_meta,
        },
    )
    return True


def filter_missing_plan_inputs(
    missing_plan_inputs: list[str],
    *,
    plan: dict[str, Any] | None,
    selected_dir: str,
    quiet: bool,
) -> list[str]:
    """Drop missing inputs that are actually outputs of prior plan steps."""

    if not missing_plan_inputs:
        return []
    plan_output_paths = _collect_planned_output_paths(
        plan if isinstance(plan, dict) else {},
        selected_dir,
    )
    truly_missing = [
        missing_path
        for missing_path in missing_plan_inputs
        if not any(output_path in missing_path for output_path in plan_output_paths)
    ]
    if truly_missing:
        _emit(
            f"Missing plan inputs (not intermediate): {truly_missing[:4]}",
            quiet=quiet,
        )
    else:
        _emit(
            "All "
            f"{len(missing_plan_inputs)} missing inputs are intermediate outputs "
            "of prior steps — skipping template replacement",
            quiet=quiet,
        )
    return truly_missing


__all__ = [
    "apply_fastq_rebinding_if_changed",
    "apply_runtime_fallback_if_distinct",
    "filter_missing_plan_inputs",
]
