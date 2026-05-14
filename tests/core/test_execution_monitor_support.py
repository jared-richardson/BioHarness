from __future__ import annotations

import time

from bio_harness.core.execution_monitor_support import (
    has_live_executor_process,
    reset_execution_run_state,
    should_drain_completed_execution,
    startup_phase_grace_seconds,
    update_active_execution_context,
)
from scripts.run_agent_e2e_support import _ExecutionMonitorState


def test_update_active_execution_context_tracks_step_command_and_pid() -> None:
    state = _ExecutionMonitorState(
        last_progress_ts=time.time(),
        active_step_started_ts=time.time(),
        active_phase_started_ts=time.time(),
    )

    update_active_execution_context("--- Executing Step 2: bash_run ---", state)
    assert state.active_step_id == 2
    assert state.active_phase == "step_announced"
    assert state.first_pid_observed is False

    update_active_execution_context("[Step 2 Output] [command] python task.py", state)
    assert state.active_command == "python task.py"
    assert state.active_phase == "runner_dispatch"

    update_active_execution_context("[status] pid=12345 still running", state)
    assert state.active_pid == 12345
    assert state.first_pid_observed is True
    assert state.active_phase == "running_process"


def test_startup_phase_grace_seconds_uses_prestep_floor() -> None:
    state = _ExecutionMonitorState(
        last_progress_ts=0.0,
        active_step_started_ts=0.0,
        active_phase_started_ts=0.0,
    )
    state.active_phase = "executor_preflight"
    state.first_pid_observed = False

    grace = startup_phase_grace_seconds(
        state,
        stall_timeout_seconds=45,
        adaptive_live_process_grace_seconds=lambda **_: 30,
        prestep_execution_phases=frozenset({"executor_preflight"}),
    )

    assert grace == 120


def test_should_drain_completed_execution_requires_completion_and_idle_time() -> None:
    assert should_drain_completed_execution(
        step_statuses=["completed", "completed"],
        has_live_process=False,
        now_ts=100.0,
        last_progress_ts=80.0,
        drain_seconds=15,
    ) is True
    assert should_drain_completed_execution(
        step_statuses=["completed", "running"],
        has_live_process=False,
        now_ts=100.0,
        last_progress_ts=80.0,
        drain_seconds=15,
    ) is False


def test_reset_execution_run_state_initializes_transient_fields() -> None:
    run = {"status": "planned", "error": "old"}

    reset_execution_run_state(run)

    assert run["status"] == "running"
    assert run["error"] == ""
    assert run["stream_counters"] == {"stdout_lines": 0, "stderr_lines": 0, "live_lines": 0}
    assert run["recent_stream_markers"] == []


def test_has_live_executor_process_checks_pid_or_monitor_tree() -> None:
    assert has_live_executor_process(None, {"alive": True}) is True
