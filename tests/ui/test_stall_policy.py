from __future__ import annotations

from bio_harness.ui.stall_policy import should_fail_ui_run_for_stall


def test_should_fail_ui_run_for_stall_keeps_full_grace_for_live_executor() -> None:
    stalled, age = should_fail_ui_run_for_stall(
        plan_running=True,
        thread_alive=True,
        queue_empty=True,
        last_progress_ts=100.0,
        now_ts=250.0,
        timeout_seconds=45,
        stall_event_emitted=False,
        has_live_executor_process=True,
        live_process_grace_seconds=900,
    )

    assert stalled is False
    assert age == 150


def test_should_fail_ui_run_for_stall_never_fails_while_live_executor_pid_exists() -> None:
    stalled, age = should_fail_ui_run_for_stall(
        plan_running=True,
        thread_alive=True,
        queue_empty=True,
        last_progress_ts=100.0,
        now_ts=1300.0,
        timeout_seconds=45,
        stall_event_emitted=False,
        has_live_executor_process=True,
        live_process_grace_seconds=900,
    )

    assert stalled is False
    assert age == 1200


def test_should_fail_ui_run_for_stall_without_live_executor_uses_base_timeout() -> None:
    stalled, age = should_fail_ui_run_for_stall(
        plan_running=True,
        thread_alive=True,
        queue_empty=True,
        last_progress_ts=100.0,
        now_ts=190.0,
        timeout_seconds=45,
        stall_event_emitted=False,
        has_live_executor_process=False,
        live_process_grace_seconds=900,
    )

    assert stalled is True
    assert age == 90
