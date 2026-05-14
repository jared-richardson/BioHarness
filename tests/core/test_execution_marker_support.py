from __future__ import annotations

from scripts.run_agent_e2e_execution_marker_support import (
    process_execution_marker_line,
)


def _base_run() -> dict[str, object]:
    return {
        "step_statuses": ["pending", "pending"],
        "next_step_idx": 0,
        "status": "running",
        "error": "",
        "stream_counters": {},
        "recent_markers": [],
        "missing_tools_detected": [],
        "missing_reference_detected": [],
        "failure_signatures": [],
        "observed_sample_groups": [],
        "missing_sample_groups": [],
        "last_executor_event_ts": 0.0,
    }


def test_process_execution_marker_line_tracks_step_start_and_finish() -> None:
    run = _base_run()
    signatures: list[str] = []

    process_execution_marker_line(
        run,
        "--- Executing Step 2: bash_run ---",
        now_ts=10.0,
        note_failure_signature=signatures.append,
    )
    process_execution_marker_line(
        run,
        "--- Step 2 (bash_run) finished ---",
        now_ts=11.0,
        note_failure_signature=signatures.append,
    )

    assert run["step_statuses"] == ["pending", "completed"]
    assert run["next_step_idx"] == 2
    assert signatures == []


def test_process_execution_marker_line_marks_manifest_zero_and_group_missing() -> None:
    run = _base_run()

    process_execution_marker_line(
        run,
        "[Step 1 Output] [stdout] __FASTQ_MANIFEST_COUNT__:0 __NO_CONTROL_FASTQ__",
        now_ts=25.0,
        note_failure_signature=lambda _sig: None,
    )

    assert run["no_fastq_found"] is True
    assert "control" in list(run.get("missing_sample_groups", []))
    assert run["stream_counters"]["stdout_lines"] == 1


def test_process_execution_marker_line_marks_bcftools_mpileup_failure() -> None:
    run = _base_run()
    signatures: list[str] = []

    process_execution_marker_line(
        run,
        "[Step 1 Output] [stderr] [mpileup] failed to read from input file",
        now_ts=30.0,
        note_failure_signature=signatures.append,
    )

    assert run["status"] == "failed"
    assert "bcftools mpileup failed" in str(run["error"]).lower()
    assert "bcftools_mpileup_input_error" in signatures


def test_process_execution_marker_line_marks_planner_timeout_before_completion() -> None:
    run = _base_run()
    signatures: list[str] = []

    process_execution_marker_line(
        run,
        "PlannerNode failed because request timed out",
        now_ts=40.0,
        note_failure_signature=signatures.append,
    )

    assert run["planner_timeout_detected"] is True
    assert run["status"] == "failed"
    assert "planner_timeout" in signatures


def test_process_execution_marker_line_updates_last_executor_event_for_stdout() -> None:
    run = _base_run()

    process_execution_marker_line(
        run,
        "[Step 1 Output] [stdout] hello world",
        now_ts=55.0,
        note_failure_signature=lambda _sig: None,
    )

    assert run["last_executor_event_ts"] == 55.0


def test_process_execution_marker_line_marks_format_input_error() -> None:
    run = _base_run()

    process_execution_marker_line(
        run,
        "[Step 1 Output] [stderr] __FORMAT_INPUT_ERROR__:Spatial coordinates contain missing or non-finite values.",
        now_ts=60.0,
        note_failure_signature=lambda _sig: None,
    )

    assert run["format_input_error_detected"] is True
    assert run["status"] == "failed"
    assert "input validation issue" in str(run["error"]).lower()
