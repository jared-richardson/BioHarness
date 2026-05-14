from __future__ import annotations

import os
from pathlib import Path

import pytest

import bio_harness.ui.bioagentbench_ui_runner as ui_runner
from bio_harness.ui.bioagentbench_ui_runner import (
    UiBenchmarkAttemptResult,
    _build_error_result,
    _is_terminal_status,
    _latest_run_activity_ts,
    _parse_args,
    _prepare_attempt_code,
    _prompt_submission_code,
    _render_summary_markdown,
    _safe_session_name,
    _wait_for_terminal_status,
)


def test_is_terminal_status_accepts_known_end_states() -> None:
    assert _is_terminal_status("completed") is True
    assert _is_terminal_status("failed") is True
    assert _is_terminal_status("blocked_input") is True
    assert _is_terminal_status("running") is False


def test_render_summary_markdown_includes_rows() -> None:
    markdown = _render_summary_markdown(
        [
            UiBenchmarkAttemptResult(
                task_id="phylogenetics",
                attempt_index=1,
                prompt="Proceed with execution now.",
                run_dir="/tmp/run",
                harness_status="completed",
                validator_exit_code=0,
                validator_passed=True,
                validator_log="/tmp/validator.log",
                screenshot_path="/tmp/ui.png",
                console_errors_path="/tmp/errors.log",
                console_warnings_path="/tmp/warnings.log",
                duration_seconds=12.5,
                benchmark_policy="official_bioagentbench",
                streamlit_url="http://127.0.0.1:8540",
                error_message="",
            )
        ]
    )

    assert "| phylogenetics | 1 | completed | pass | 12.5 | `/tmp/run` |" in markdown


def test_safe_session_name_shortens_long_names() -> None:
    assert _safe_session_name("ui_bioagentbench_reliability") == "ui_bioagentbench_reliabi"
    assert _safe_session_name(" weird session name ") == "weird_session_name"


def test_prompt_submission_code_waits_for_text_and_send_enablement() -> None:
    code = _prompt_submission_code("Proceed with execution now.")

    assert "await page.keyboard.type(promptText, { delay: 0 });" in code
    assert "field.value === expected" in code
    assert "Send message" in code
    assert "!button.disabled" in code
    assert "await page.getByRole('button', { name: 'Send message' }).click();" in code


def test_prepare_attempt_code_always_starts_from_new_chat() -> None:
    code = _prepare_attempt_code()

    assert "await newChat.click();" in code
    assert "existingMessages" not in code
    assert "Use small-sample subset" in code


def test_build_error_result_marks_failed_attempt() -> None:
    result = _build_error_result(
        task_id="alzheimer-mouse",
        attempt_index=1,
        prompt="Proceed with execution now.",
        benchmark_policy="official_bioagentbench",
        streamlit_url="http://127.0.0.1:8540",
        duration_seconds=301.0,
        error_message="Timed out waiting for a new UI run directory.",
        screenshot_path=Path("/tmp/ui.png"),
        console_errors_path=Path("/tmp/errors.log"),
        console_warnings_path=Path("/tmp/warnings.log"),
    )

    assert result.harness_status == "runner_error"
    assert result.validator_passed is False
    assert result.error_message.startswith("Timed out")


def test_parse_args_accepts_ui_plan_timeout_override() -> None:
    args = _parse_args(["--task-id", "phylogenetics", "--ui-plan-timeout-seconds", "600"])

    assert args.task_id == ["phylogenetics"]
    assert args.ui_plan_timeout_seconds == 600


def test_latest_run_activity_ts_uses_newest_artifact_timestamp(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    events = run_dir / "events.jsonl"
    state = run_dir / "state.json"
    exit_json = run_dir / "exit.json"
    events.write_text("", encoding="utf-8")
    state.write_text("{}", encoding="utf-8")
    exit_json.write_text('{"status":"running"}', encoding="utf-8")
    events_ts = 100.0
    state_ts = 125.0
    exit_ts = 150.0
    os.utime(events, (events_ts, events_ts))
    os.utime(state, (state_ts, state_ts))
    os.utime(exit_json, (exit_ts, exit_ts))

    assert _latest_run_activity_ts(run_dir) == exit_ts


def test_wait_for_terminal_status_times_out_after_inactivity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(ui_runner, "_read_exit_payload", lambda _: {"status": "running"})
    monkeypatch.setattr(ui_runner, "_latest_run_activity_ts", lambda _: 100.0)
    clock = iter([100.0, 221.0])
    monkeypatch.setattr(ui_runner.time, "time", lambda: next(clock))
    monkeypatch.setattr(ui_runner.time, "sleep", lambda _: None)

    with pytest.raises(TimeoutError, match="without run activity"):
        _wait_for_terminal_status(run_dir, timeout_seconds=120)


def test_wait_for_terminal_status_keeps_waiting_when_activity_advances(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    payloads = iter([{"status": "running"}, {"status": "completed"}])
    monkeypatch.setattr(ui_runner, "_read_exit_payload", lambda _: next(payloads))
    activity = iter([100.0, 170.0])
    monkeypatch.setattr(ui_runner, "_latest_run_activity_ts", lambda _: next(activity))
    clock = iter([100.0, 180.0])
    monkeypatch.setattr(ui_runner.time, "time", lambda: next(clock))
    monkeypatch.setattr(ui_runner.time, "sleep", lambda _: None)

    assert _wait_for_terminal_status(run_dir, timeout_seconds=60) == "completed"
