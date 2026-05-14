"""UI-specific stall-detection policy helpers."""

from __future__ import annotations

from bio_harness.core.run_state import should_mark_stalled


def should_fail_ui_run_for_stall(
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
) -> tuple[bool, int]:
    """Evaluate UI stall state while preserving live-process grace.

    The Streamlit UI can temporarily miss queue updates even while a live
    executor PID continues working. In that case, the UI should honor the full
    live-process grace window instead of failing early on an idle queue.

    Args:
        plan_running: Whether the plan is still marked as running.
        thread_alive: Whether the execution thread is still alive.
        queue_empty: Whether the UI log queue is currently empty.
        last_progress_ts: Timestamp of the last observed executor progress.
        now_ts: Current wall-clock timestamp.
        timeout_seconds: Base stall timeout in seconds.
        stall_event_emitted: Whether the run already emitted a stall event.
        has_live_executor_process: Whether a subprocess PID is still alive.
        live_process_grace_seconds: Maximum grace window for a live process.

    Returns:
        The same ``(should_fail, stall_age_seconds)`` tuple returned by
        :func:`bio_harness.core.run_state.should_mark_stalled`, except the UI
        never fails a run while a live executor PID is still present.
    """
    stalled, age = should_mark_stalled(
        plan_running=plan_running,
        thread_alive=thread_alive,
        queue_empty=queue_empty,
        last_progress_ts=last_progress_ts,
        now_ts=now_ts,
        timeout_seconds=timeout_seconds,
        stall_event_emitted=stall_event_emitted,
        has_live_executor_process=has_live_executor_process,
        live_process_grace_seconds=live_process_grace_seconds,
        live_process_allow_full_grace_on_idle=True,
    )
    if has_live_executor_process:
        return False, age
    return stalled, age
