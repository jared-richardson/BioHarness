from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from bio_harness.core.schemas import ARTIFACT_SCHEMA_VERSION
from bio_harness.ui.planning_run_state import (
    cancel_planner_job,
    ensure_planning_run_initialized,
    launch_planner_job,
    load_planner_result,
    load_planner_status,
    planner_result_path,
    planner_status_path,
    planning_is_orphaned,
)


def test_ensure_planning_run_initialized_creates_run_artifacts(tmp_path: Path) -> None:
    run = {
        "id": 7,
        "plan_kind": "executable",
        "user_request": "Run differential expression",
        "chat_session_id": "run-7-123",
    }

    files = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": False},
        benchmark_policy="official_bioagentbench",
    )

    assert run["run_uid"]
    assert Path(files["run_dir"]).exists()
    manifest = json.loads(Path(files["manifest"]).read_text(encoding="utf-8"))
    assert manifest["plan_id"] == 7
    assert manifest["requested_data_root"] == "/tmp/data"
    assert manifest["benchmark_policy"] == "official_bioagentbench"
    assert manifest["chat_session_id"] == "run-7-123"

    events_lines = Path(files["events"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(events_lines) == 1
    event = json.loads(events_lines[0])
    assert event["event_type"] == "PLAN_STARTED"
    exit_payload = json.loads(Path(files["exit"]).read_text(encoding="utf-8"))
    assert exit_payload["status"] == "planning"
    planner_status = json.loads(planner_status_path(files).read_text(encoding="utf-8"))
    assert planner_status["status"] == "planning"


def test_ensure_planning_run_initialized_reuses_existing_run_dir(tmp_path: Path) -> None:
    run = {"id": 8, "plan_kind": "executable", "user_request": "Run phylogenetics"}

    first = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": True},
        benchmark_policy="official_bioagentbench",
    )
    second = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected2",
        requested_data_root="/tmp/data2",
        execution_options={"use_test_subset": False},
        benchmark_policy="scientific_harness",
    )

    assert first["run_dir"] == second["run_dir"]
    events_lines = Path(first["events"]).read_text(encoding="utf-8").strip().splitlines()
    assert len(events_lines) == 1
    manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
    assert manifest["selected_dir"] == "/tmp/selected2"
    assert manifest["requested_data_root"] == "/tmp/data2"
    assert manifest["benchmark_policy"] == "scientific_harness"


def test_ensure_planning_run_initialized_refreshes_manifest_user_request(tmp_path: Path) -> None:
    run = {"id": 8, "plan_kind": "executable", "user_request": ""}

    first = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": True},
        benchmark_policy="scientific_harness",
    )
    run["user_request"] = "Proceed with execution now. Quantify transcripts from /tmp/sample.bam using /tmp/genes.gtf."
    ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": True},
        benchmark_policy="scientific_harness",
    )

    manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
    assert manifest["user_request"] == run["user_request"]


def test_launch_planner_job_persists_result_and_final_status(tmp_path: Path) -> None:
    run = {"id": 9, "plan_kind": "executable", "user_request": "Run phylogenetics"}
    files = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": False},
        benchmark_policy="scientific_harness",
    )

    started = launch_planner_job(
        run,
        planning_fn=lambda: {"plan": {"plan": [{"step": 1}]}, "contract_validation": {"passed": True}},
        timeout_seconds=5,
    )

    assert started is True
    for _ in range(50):
        status = load_planner_status(files)
        if status.get("status") == "planned":
            break
        time.sleep(0.05)

    status = load_planner_status(files)
    assert status["status"] == "planned"
    assert status["result_ready"] is True
    assert planner_result_path(files).exists()
    result = load_planner_result(files)
    assert result["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert result["plan"]["plan"][0]["step"] == 1


def test_launch_planner_job_persists_failures(tmp_path: Path) -> None:
    run = {"id": 10, "plan_kind": "executable", "user_request": "Run failing planner"}
    files = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": False},
        benchmark_policy="scientific_harness",
    )

    started = launch_planner_job(
        run,
        planning_fn=lambda: (_ for _ in ()).throw(RuntimeError("planner boom")),
        timeout_seconds=5,
    )

    assert started is True
    for _ in range(50):
        status = load_planner_status(files)
        if status.get("status") == "planning_failed":
            break
        time.sleep(0.05)

    status = load_planner_status(files)
    assert status["status"] == "planning_failed"
    assert "planner boom" in status["error"]


def test_planning_is_orphaned_when_no_live_job_and_status_is_stale(tmp_path: Path) -> None:
    run = {"id": 11, "plan_kind": "executable", "user_request": "Run stalled planner"}
    files = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": False},
        benchmark_policy="scientific_harness",
    )
    stale_status = load_planner_status(files)
    stale_status["updated_at"] = "2000-01-01T00:00:00"
    planner_status_path(files).write_text(json.dumps(stale_status), encoding="utf-8")

    assert planning_is_orphaned(files, run_uid=str(run["run_uid"]), orphan_after_seconds=1) is True


def test_planning_is_orphaned_uses_state_timestamp_when_planner_status_missing(tmp_path: Path) -> None:
    run = {"id": 12, "plan_kind": "executable", "user_request": "Legacy stalled planner"}
    files = ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={"use_test_subset": False},
        benchmark_policy="scientific_harness",
    )
    planner_status_path(files).unlink()
    state_path = Path(files["state"])
    state_payload = json.loads(state_path.read_text(encoding="utf-8"))
    state_payload["planning_started_at"] = "2000-01-01T00:00:00"
    state_payload["updated_at"] = "2000-01-01T00:00:00"
    state_path.write_text(json.dumps(state_payload), encoding="utf-8")

    assert planning_is_orphaned(files, run_uid=str(run["run_uid"]), orphan_after_seconds=1) is True


def test_launch_planner_job_sets_cancel_event_on_timeout(tmp_path: Path) -> None:
    """When planning times out, the cancel event should be set."""
    cancel = threading.Event()
    run = {"id": 13, "plan_kind": "executable", "user_request": "Slow planner"}
    ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={},
        benchmark_policy="scientific_harness",
    )

    def slow_planner() -> dict:
        time.sleep(10)
        return {"plan": []}

    started = launch_planner_job(
        run,
        planning_fn=slow_planner,
        timeout_seconds=1,
        cancel_event=cancel,
    )
    assert started is True

    for _ in range(60):
        status = load_planner_status(run["run_files"])
        if status.get("status") == "planning_timed_out":
            break
        time.sleep(0.1)

    assert cancel.is_set(), "Cancel event should be set after timeout"
    status = load_planner_status(run["run_files"])
    assert status["status"] == "planning_timed_out"


def test_cancel_planner_job_signals_running_job(tmp_path: Path) -> None:
    """Explicit cancel_planner_job() should set the cancel event."""
    cancel = threading.Event()
    run = {"id": 14, "plan_kind": "executable", "user_request": "Cancellable planner"}
    ensure_planning_run_initialized(
        run,
        workspace_root=tmp_path,
        selected_dir="/tmp/selected",
        requested_data_root="/tmp/data",
        execution_options={},
        benchmark_policy="scientific_harness",
    )

    def blocking_planner() -> dict:
        time.sleep(30)
        return {"plan": []}

    started = launch_planner_job(
        run,
        planning_fn=blocking_planner,
        timeout_seconds=30,
        cancel_event=cancel,
    )
    assert started is True
    time.sleep(0.2)

    cancelled = cancel_planner_job(run["run_uid"])
    assert cancelled is True
    assert cancel.is_set()


def test_cancel_planner_job_returns_false_for_unknown_run() -> None:
    assert cancel_planner_job("nonexistent-run-id") is False
