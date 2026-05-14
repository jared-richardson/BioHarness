from __future__ import annotations

from typing import Any


def latest_error_event_detail(events: list[dict[str, Any]]) -> tuple[bool, str]:
    """Scan events in reverse for the most recent error event.

    Args:
        events: List of event dicts, each with optional 'severity' and 'payload' keys.

    Returns:
        Tuple of (found, detail_string). If no error is found, returns (False, "").
    """
    for ev in reversed(events):
        if str(ev.get("severity", "")).strip().lower() != "error":
            continue
        event_type = str(ev.get("event_type", "error")).strip() or "error"
        payload = ev.get("payload", {}) if isinstance(ev.get("payload", {}), dict) else {}
        detail = payload.get("reason") or payload.get("issues") or payload.get("status_line") or payload
        return True, f"{event_type}: {detail}"
    return False, ""


def should_mark_stalled(
    *,
    plan_running: bool,
    thread_alive: bool,
    queue_empty: bool,
    last_progress_ts: float,
    now_ts: float,
    timeout_seconds: int,
    stall_event_emitted: bool,
    has_live_executor_process: bool = False,
    live_process_grace_seconds: int = 900,
    live_process_is_progressing: bool = False,
    live_process_allow_full_grace_on_idle: bool = False,
    startup_phase_active: bool = False,
    startup_phase_grace_seconds: int = 0,
    planner_phase_active: bool = False,
    planner_phase_grace_seconds: int = 0,
) -> tuple[bool, int]:
    """Determine if a running plan has stalled based on timing heuristics.

    Uses the elapsed time since last progress update and compares against
    timeout thresholds, with extended grace for live executor processes.

    Args:
        plan_running: Whether the plan is currently marked as running.
        thread_alive: Whether the execution thread is still alive.
        queue_empty: Whether the log queue is empty.
        last_progress_ts: Timestamp of the last observed progress.
        now_ts: Current timestamp.
        timeout_seconds: Base stall timeout in seconds.
        stall_event_emitted: Whether a stall event has already been emitted.
        has_live_executor_process: Whether a subprocess PID is still alive.
        live_process_grace_seconds: Extended grace period for live processes.
        live_process_is_progressing: Whether the live process shows output activity.
        live_process_allow_full_grace_on_idle: Allow full grace even when idle.
        startup_phase_active: Whether execution is still in a pre-PID startup phase.
        startup_phase_grace_seconds: Timeout budget for the active startup phase.
        planner_phase_active: Whether the harness is currently waiting on planner work.
        planner_phase_grace_seconds: Timeout budget for the active planner phase.

    Returns:
        Tuple of (is_stalled, stall_age_seconds).
    """
    if not plan_running:
        return False, 0
    if not thread_alive:
        return False, 0
    if not queue_empty:
        return False, 0
    if stall_event_emitted:
        return False, 0
    if last_progress_ts <= 0:
        return False, 0
    stall_age = int(max(0.0, now_ts - last_progress_ts))
    if startup_phase_active:
        effective_timeout = max(int(timeout_seconds), int(startup_phase_grace_seconds))
        return stall_age > effective_timeout, stall_age
    if planner_phase_active:
        effective_timeout = max(int(timeout_seconds), int(planner_phase_grace_seconds))
        return stall_age > effective_timeout, stall_age
    # While an executor PID is still live:
    # - if we still observe progress, allow extended grace
    # - if process appears alive but idle, fail faster to trigger recovery
    if has_live_executor_process:
        if live_process_is_progressing or live_process_allow_full_grace_on_idle:
            effective_timeout = max(int(timeout_seconds), int(live_process_grace_seconds))
        else:
            effective_timeout = max(int(timeout_seconds), min(int(live_process_grace_seconds), 120))
        return stall_age > effective_timeout, stall_age
    return stall_age > int(timeout_seconds), stall_age


def mark_running_items_failed(run: dict[str, Any], *, process_status_text: str) -> None:
    """Set all running steps and processes to failed status.

    Args:
        run: Mutable run state dict containing 'step_statuses' and 'process_tracker'.
        process_status_text: Human-readable reason for the failure.
    """
    for i, step_state in enumerate(run.get("step_statuses", [])):
        if step_state == "running":
            run["step_statuses"][i] = "failed"
    for key in run.get("process_order", []):
        proc = run.get("process_tracker", {}).get(key, {})
        if proc.get("status") == "running":
            proc["status"] = "failed"
            proc["status_text"] = process_status_text


def normalize_step_statuses_for_resume(run: dict[str, Any]) -> list[str]:
    """Convert running statuses to pending so a plan can be safely resumed.

    Mutates run['step_statuses'] in place.

    Args:
        run: Mutable run state dict.

    Returns:
        The normalized list of step status strings.
    """
    plan_obj = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    steps = plan_obj.get("plan", []) if isinstance(plan_obj.get("plan", []), list) else []
    if not steps:
        return []

    statuses = run.get("step_statuses", [])
    if not isinstance(statuses, list) or len(statuses) != len(steps):
        statuses = ["pending"] * len(steps)

    normalized = ["pending" if str(status) == "running" else str(status) for status in statuses]
    run["step_statuses"] = normalized
    return normalized


def resume_index_from_statuses(step_statuses: list[str]) -> int:
    """Find the first non-completed step index for resuming execution.

    Args:
        step_statuses: List of step status strings (e.g. 'completed', 'pending').

    Returns:
        Zero-based index of the first non-completed step, or len(step_statuses)
        if all steps are completed.
    """
    for idx, status in enumerate(step_statuses):
        if str(status) != "completed":
            return idx
    return len(step_statuses)


def evaluate_existing_plan_resume(
    run: dict[str, Any],
    *,
    plan_running: bool,
    allow_terminal_resume: bool = False,
) -> dict[str, Any]:
    """Check if an existing plan can be resumed and determine the resume strategy.

    Args:
        run: Run state dict with 'plan', 'step_statuses', 'status', etc.
        plan_running: Whether the plan is currently running.
        allow_terminal_resume: Whether to allow resuming failed/completed runs.

    Returns:
        Dict with 'reusable' bool and, if reusable, 'action' ('complete',
        'single_step', or 'full_plan'), 'resume_idx', 'message', and 'step_count'.
    """
    if plan_running:
        return {"reusable": False}
    status = str(run.get("status", "")).strip().lower()
    if status in {"failed", "completed"} and not allow_terminal_resume:
        return {"reusable": False}
    if str(run.get("plan_kind", "")).strip().lower() != "executable":
        return {"reusable": False}

    plan_obj = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    steps = plan_obj.get("plan", []) if isinstance(plan_obj.get("plan", []), list) else []
    if not steps:
        return {"reusable": False}

    statuses = normalize_step_statuses_for_resume(run)
    if not statuses:
        return {"reusable": False}

    try:
        resume_idx = int(run.get("next_step_idx", 0))
    except Exception:
        resume_idx = 0
    computed_idx = resume_index_from_statuses(statuses)
    if resume_idx < 0 or resume_idx > len(statuses):
        resume_idx = computed_idx
    elif computed_idx < resume_idx:
        resume_idx = computed_idx
    run["next_step_idx"] = resume_idx

    step_count = len(steps)
    if resume_idx >= step_count:
        return {
            "reusable": True,
            "action": "complete",
            "resume_idx": resume_idx,
            "message": "Plan is already complete. No further execution steps remain.",
            "step_count": step_count,
        }

    has_progress = any(status in {"completed", "failed"} for status in statuses) or resume_idx > 0
    if has_progress:
        return {
            "reusable": True,
            "action": "single_step",
            "resume_idx": resume_idx,
            "message": f"Proceeding with existing plan at step {resume_idx + 1}/{step_count}.",
            "step_count": step_count,
        }

    return {
        "reusable": True,
        "action": "full_plan",
        "resume_idx": resume_idx,
        "message": f"Starting existing executable plan ({step_count} steps).",
        "step_count": step_count,
    }
