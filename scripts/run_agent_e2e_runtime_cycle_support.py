"""Helpers for the runtime execution and auto-repair loop.

These helpers keep the runtime-repair action mixin focused on high-level
control flow while centralizing the state transitions used after preflight
failures and successful auto-repair applications.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bio_harness.core.artifact_inspectors import infer_resumable_step_index
from scripts.run_agent_e2e_support import (
    _now_utc_iso,
    build_repair_audit_entry,
)

RunDict = dict[str, Any]
AppendEventFn = Callable[..., None]
EmitFn = Callable[..., None]


def record_preflight_failure(
    run: RunDict,
    *,
    message: str,
    append_event: AppendEventFn,
) -> None:
    """Record a terminal preflight failure on the mutable run state.

    Args:
        run: Mutable harness run state.
        message: Human-readable preflight failure message.
        append_event: Harness event-appending callback.
    """

    run["status"] = "failed"
    run["error"] = message
    run["finished_at"] = _now_utc_iso()
    append_event(
        step_id=None,
        agent="PreflightAgent",
        event_type="PRECHECK_FAILED",
        severity="error",
        payload={"reason": message},
    )


def checkpoint_resume_after_repair(
    run: RunDict,
    *,
    selected_dir: Path,
    emit: EmitFn,
    quiet: bool,
) -> dict[str, Any]:
    """Advance the next step index when earlier outputs are already materialized.

    Args:
        run: Mutable harness run state.
        selected_dir: Selected run directory used to inspect expected outputs.
        emit: User/log emission callback.
        quiet: Whether user-facing emissions are suppressed.

    Returns:
        Metadata describing whether checkpoint resume advanced the plan.
    """

    plan = run.get("plan") or {}
    if not (isinstance(plan, dict) and plan.get("plan")):
        return {"changed": False, "resume_idx": int(run.get("next_step_idx", 0) or 0)}

    resume_idx = infer_resumable_step_index(selected_dir, plan)
    current_idx = int(run.get("next_step_idx", 0) or 0)
    if resume_idx <= current_idx:
        return {"changed": False, "resume_idx": current_idx}

    step_statuses = list(run.get("step_statuses", []))
    for idx in range(resume_idx):
        if idx < len(step_statuses):
            step_statuses[idx] = "completed"
    run["step_statuses"] = step_statuses
    run["next_step_idx"] = resume_idx
    emit(
        f"[recovery] Checkpoint resume: skipping to step {resume_idx + 1} "
        f"(outputs exist for steps 1-{resume_idx}).",
        quiet=quiet,
    )
    return {
        "changed": True,
        "resume_idx": resume_idx,
        "total_steps": len(plan.get("plan", [])),
    }


def apply_successful_repair_cycle(
    run: RunDict,
    *,
    failure_class: str,
    action: str,
    details: dict[str, Any],
    selected_dir: Path,
    append_event: AppendEventFn,
    emit: EmitFn,
    quiet: bool,
) -> dict[str, Any]:
    """Record one successful auto-repair cycle and reset the run for retry.

    Args:
        run: Mutable harness run state.
        failure_class: Failure class being repaired.
        action: Applied repair action identifier.
        details: Repair details payload.
        selected_dir: Selected run directory used for checkpoint resume.
        append_event: Harness event-appending callback.
        emit: User/log emission callback.
        quiet: Whether user-facing emissions are suppressed.

    Returns:
        Metadata describing the recorded repair event and any checkpoint resume
        updates applied to the run state.
    """

    attempts = dict(run.get("auto_repair_attempts", {}))
    attempts[failure_class] = int(attempts.get(failure_class, 0)) + 1
    run["auto_repair_attempts"] = attempts

    event = build_repair_audit_entry(
        run_id=run.get("run_uid", ""),
        failure_class=failure_class,
        attempt=attempts[failure_class],
        action=action,
        details=details,
    )
    history = list(run.get("auto_repair_history", []))
    history.append(event)
    run["auto_repair_history"] = history
    run["status"] = "planned"
    run["error"] = ""

    resume_meta = checkpoint_resume_after_repair(
        run,
        selected_dir=selected_dir,
        emit=emit,
        quiet=quiet,
    )
    append_event(
        step_id=None,
        agent="RecoveryAgent",
        event_type="REPAIR_APPLIED",
        severity="warning",
        payload=event,
    )
    return {
        "attempts": attempts,
        "event": event,
        "resume": resume_meta,
    }


__all__ = [
    "apply_successful_repair_cycle",
    "checkpoint_resume_after_repair",
    "record_preflight_failure",
]
