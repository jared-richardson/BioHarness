"""Tests for durable executor runtime tracking."""

from __future__ import annotations

import json
import os
from pathlib import Path

from bio_harness.core.executor_runtime import (
    executor_runtime_is_live,
    finish_executor_runtime,
    heartbeat_executor_runtime,
    load_executor_runtime,
    start_executor_runtime,
)
from bio_harness.core.schemas import ARTIFACT_SCHEMA_VERSION


def _run_files(tmp_path: Path) -> dict[str, str]:
    return {
        "run_dir": str(tmp_path),
        "executor_runtime": str(tmp_path / "executor.json"),
    }


def test_start_executor_runtime_writes_versioned_running_payload(tmp_path: Path) -> None:
    payload = start_executor_runtime(_run_files(tmp_path), run_id="run-1", pid=os.getpid())

    assert payload["run_id"] == "run-1"
    assert payload["pid"] == os.getpid()
    assert payload["status"] == "running"
    assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_heartbeat_executor_runtime_updates_last_event_fields(tmp_path: Path) -> None:
    run_files = _run_files(tmp_path)
    start_executor_runtime(run_files, run_id="run-2", pid=os.getpid())

    payload = heartbeat_executor_runtime(
        run_files,
        run_id="run-2",
        event_type="STEP_STARTED",
        step_id=3,
        tool_name="salmon_quant",
    )

    assert payload["status"] == "running"
    assert payload["last_event_type"] == "STEP_STARTED"
    assert payload["last_step_id"] == 3
    assert payload["last_tool_name"] == "salmon_quant"


def test_finish_executor_runtime_marks_terminal_status(tmp_path: Path) -> None:
    run_files = _run_files(tmp_path)
    start_executor_runtime(run_files, run_id="run-3", pid=os.getpid())

    payload = finish_executor_runtime(
        run_files,
        run_id="run-3",
        status="failed",
        error="boom",
    )

    assert payload["status"] == "failed"
    assert payload["finished_at"]
    assert payload["error"] == "boom"
    assert executor_runtime_is_live(run_files) is False


def test_executor_runtime_is_live_for_current_process(tmp_path: Path) -> None:
    run_files = _run_files(tmp_path)
    start_executor_runtime(run_files, run_id="run-4", pid=os.getpid())

    assert executor_runtime_is_live(run_files) is True


def test_load_executor_runtime_returns_empty_dict_for_invalid_json(tmp_path: Path) -> None:
    run_files = _run_files(tmp_path)
    Path(run_files["executor_runtime"]).write_text("{not-json", encoding="utf-8")

    assert load_executor_runtime(run_files) == {}


def test_executor_runtime_file_round_trips_json(tmp_path: Path) -> None:
    run_files = _run_files(tmp_path)
    start_executor_runtime(run_files, run_id="run-5", pid=os.getpid())

    payload = json.loads(Path(run_files["executor_runtime"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert payload["status"] == "running"
