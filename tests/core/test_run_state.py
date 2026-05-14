import pytest

from bio_harness.core.run_state import (
    evaluate_existing_plan_resume,
    latest_error_event_detail,
    mark_running_items_failed,
    normalize_step_statuses_for_resume,
    resume_index_from_statuses,
    should_mark_stalled,
)


# ---------------------------------------------------------------------------
# should_mark_stalled — parametrized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expect_stalled, expect_age",
    [
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=170.0,
                timeout_seconds=45, stall_event_emitted=False,
            ),
            True, 70,
            id="idle_past_threshold",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=False,
                last_progress_ts=100.0, now_ts=170.0,
                timeout_seconds=45, stall_event_emitted=False,
            ),
            False, 0,
            id="queue_active",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=250.0,
                timeout_seconds=45, stall_event_emitted=False,
                has_live_executor_process=True,
                live_process_grace_seconds=900,
                live_process_is_progressing=True,
            ),
            False, 150,
            id="live_process_within_grace",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=1300.0,
                timeout_seconds=45, stall_event_emitted=False,
                has_live_executor_process=True,
                live_process_grace_seconds=900,
                live_process_is_progressing=True,
            ),
            True, 1200,
            id="live_process_exceeds_grace",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=145.0,
                timeout_seconds=120, stall_event_emitted=False,
                has_live_executor_process=True,
                live_process_grace_seconds=30,
                live_process_is_progressing=True,
            ),
            False, 45,
            id="uses_max_of_timeout_and_grace",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=250.0,
                timeout_seconds=45, stall_event_emitted=False,
                has_live_executor_process=True,
                live_process_grace_seconds=900,
                live_process_is_progressing=False,
            ),
            True, 150,
            id="idle_uses_faster_timeout",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=250.0,
                timeout_seconds=45, stall_event_emitted=False,
                has_live_executor_process=True,
                live_process_grace_seconds=900,
                live_process_is_progressing=False,
                live_process_allow_full_grace_on_idle=True,
            ),
            False, 150,
            id="full_grace_for_long_alignment",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=220.0,
                timeout_seconds=45, stall_event_emitted=False,
                planner_phase_active=True,
                planner_phase_grace_seconds=180,
            ),
            False, 120,
            id="planner_phase_extends_timeout_without_executor_pid",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=400.0,
                timeout_seconds=45, stall_event_emitted=False,
                planner_phase_active=True,
                planner_phase_grace_seconds=180,
            ),
            True, 300,
            id="planner_phase_can_still_time_out",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=220.0,
                timeout_seconds=45, stall_event_emitted=False,
                startup_phase_active=True,
                startup_phase_grace_seconds=180,
            ),
            False, 120,
            id="startup_phase_extends_timeout_before_first_pid",
        ),
        pytest.param(
            dict(
                plan_running=True, thread_alive=True, queue_empty=True,
                last_progress_ts=100.0, now_ts=340.0,
                timeout_seconds=45, stall_event_emitted=False,
                startup_phase_active=True,
                startup_phase_grace_seconds=180,
            ),
            True, 240,
            id="startup_phase_can_still_time_out",
        ),
    ],
)
def test_should_mark_stalled(kwargs, expect_stalled, expect_age):
    stalled, age = should_mark_stalled(**kwargs)
    assert stalled is expect_stalled
    assert age == expect_age


# ---------------------------------------------------------------------------
# mark_running_items_failed
# ---------------------------------------------------------------------------


def test_mark_running_items_failed_updates_step_and_process_states():
    run = {
        "step_statuses": ["completed", "running", "pending"],
        "process_order": ["1", "2"],
        "process_tracker": {
            "1": {"status": "completed", "status_text": "done"},
            "2": {"status": "running", "status_text": "in progress"},
        },
    }
    mark_running_items_failed(run, process_status_text="Stalled")
    assert run["step_statuses"] == ["completed", "failed", "pending"]
    assert run["process_tracker"]["1"]["status"] == "completed"
    assert run["process_tracker"]["2"]["status"] == "failed"
    assert run["process_tracker"]["2"]["status_text"] == "Stalled"


# ---------------------------------------------------------------------------
# latest_error_event_detail
# ---------------------------------------------------------------------------


def test_latest_error_event_detail_prefers_last_error_even_with_later_heartbeats():
    events = [
        {"event_type": "STEP_HEARTBEAT", "severity": "info", "payload": {"status_line": "[status] running pid=11 elapsed=5s"}},
        {"event_type": "STALL_DETECTED", "severity": "error", "payload": {"stall_seconds": 128, "status": "failed"}},
        {"event_type": "STEP_HEARTBEAT", "severity": "info", "payload": {"status_line": "[status] running pid=11 elapsed=130s"}},
    ]
    has_error, detail = latest_error_event_detail(events)
    assert has_error is True
    assert detail.startswith("STALL_DETECTED:")
    assert "128" in detail


def test_latest_error_event_detail_returns_false_when_no_errors():
    events = [
        {"event_type": "STEP_STARTED", "severity": "info", "payload": {}},
        {"event_type": "STEP_HEARTBEAT", "severity": "info", "payload": {"status_line": "running"}},
    ]
    has_error, detail = latest_error_event_detail(events)
    assert has_error is False
    assert detail == ""


# ---------------------------------------------------------------------------
# normalize_step_statuses_for_resume
# ---------------------------------------------------------------------------


def test_normalize_step_statuses_for_resume_pads_mismatch_and_converts_running():
    run = {"plan": {"plan": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}]}, "step_statuses": ["completed", "running"]}
    statuses = normalize_step_statuses_for_resume(run)
    assert statuses == ["pending", "pending", "pending"]
    assert run["step_statuses"] == ["pending", "pending", "pending"]


# ---------------------------------------------------------------------------
# resume_index_from_statuses
# ---------------------------------------------------------------------------


def test_resume_index_from_statuses_finds_first_non_completed():
    assert resume_index_from_statuses(["completed", "completed", "pending"]) == 2
    assert resume_index_from_statuses(["completed", "completed"]) == 2


# ---------------------------------------------------------------------------
# evaluate_existing_plan_resume — parametrized non-reusable cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run, plan_running, allow_terminal",
    [
        pytest.param(
            {"plan_kind": "executable", "plan": {"plan": [{"step_id": 1}]}, "step_statuses": ["pending"]},
            True, False,
            id="plan_currently_running",
        ),
        pytest.param(
            {"plan_kind": "analysis", "plan": {"plan": [{"step_id": 1}]}, "step_statuses": ["pending"]},
            False, False,
            id="non_executable_plan_kind",
        ),
        pytest.param(
            {"status": "failed", "plan_kind": "executable", "plan": {"plan": [{"step_id": 1}]}, "step_statuses": ["failed"], "next_step_idx": 0},
            False, False,
            id="failed_run_without_terminal_resume",
        ),
        pytest.param(
            {"status": "completed", "plan_kind": "executable", "plan": {"plan": [{"step_id": 1}]}, "step_statuses": ["completed"], "next_step_idx": 1},
            False, False,
            id="completed_run_without_terminal_resume",
        ),
    ],
)
def test_evaluate_plan_not_reusable(run, plan_running, allow_terminal):
    decision = evaluate_existing_plan_resume(
        run, plan_running=plan_running, allow_terminal_resume=allow_terminal
    )
    assert decision["reusable"] is False


def test_evaluate_existing_plan_resume_uses_computed_index_for_single_step_resume():
    run = {
        "plan_kind": "executable",
        "plan": {"plan": [{"step_id": 1}, {"step_id": 2}, {"step_id": 3}]},
        "step_statuses": ["completed", "running", "pending"],
        "next_step_idx": 2,
    }
    decision = evaluate_existing_plan_resume(run, plan_running=False)
    assert decision["reusable"] is True
    assert decision["action"] == "single_step"
    assert decision["resume_idx"] == 1
    assert run["next_step_idx"] == 1
    assert run["step_statuses"] == ["completed", "pending", "pending"]


def test_evaluate_existing_plan_resume_starts_full_plan_when_no_progress():
    run = {
        "plan_kind": "executable",
        "plan": {"plan": [{"step_id": 1}, {"step_id": 2}]},
        "step_statuses": ["pending", "pending"],
        "next_step_idx": 0,
    }
    decision = evaluate_existing_plan_resume(run, plan_running=False)
    assert decision["reusable"] is True
    assert decision["action"] == "full_plan"
    assert decision["resume_idx"] == 0


def test_evaluate_existing_plan_resume_returns_complete_when_done():
    run = {
        "plan_kind": "executable",
        "plan": {"plan": [{"step_id": 1}, {"step_id": 2}]},
        "step_statuses": ["completed", "completed"],
        "next_step_idx": 2,
    }
    decision = evaluate_existing_plan_resume(run, plan_running=False)
    assert decision["reusable"] is True
    assert decision["action"] == "complete"


def test_evaluate_existing_plan_resume_can_allow_terminal_resume_when_explicit():
    run_failed = {
        "status": "failed",
        "plan_kind": "executable",
        "plan": {"plan": [{"step_id": 1}, {"step_id": 2}]},
        "step_statuses": ["completed", "failed"],
        "next_step_idx": 1,
    }
    decision = evaluate_existing_plan_resume(run_failed, plan_running=False, allow_terminal_resume=True)
    assert decision["reusable"] is True
    assert decision["action"] == "single_step"
