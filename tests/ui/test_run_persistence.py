from __future__ import annotations

import json
from pathlib import Path

from bio_harness.ui.run_persistence import (
    init_run_files,
    load_all_events,
    load_recent_events,
    merge_recent_persisted_runs,
    parse_event_epoch,
    persist_run_state,
    read_text_tail,
    tail_items,
    write_terminal_artifacts_if_needed,
)


def test_init_run_files_bootstraps_expected_state(tmp_path: Path) -> None:
    run = {"user_request": "test task"}

    run_files = init_run_files(run, tmp_path)

    assert Path(run_files["state"]).exists()
    assert Path(run_files["events"]).exists()
    assert run["run_uid"]
    assert run["async_status"] == "running"
    assert run["live_tail"].maxlen == 4000


def test_persist_run_state_and_terminal_artifacts_write_expected_files(tmp_path: Path) -> None:
    run = {
        "user_request": "test task",
        "status": "completed",
        "error": "",
        "next_step_idx": 2,
        "step_statuses": ["completed", "completed"],
    }
    run_files = init_run_files(run, tmp_path)
    run["auto_repair_promotions"] = ["legacy promotion note"]

    persist_run_state(run)
    write_terminal_artifacts_if_needed(run)

    state = json.loads(Path(run_files["state"]).read_text(encoding="utf-8"))
    exit_payload = json.loads(Path(run_files["exit"]).read_text(encoding="utf-8"))
    summary = Path(run_files["summary"]).read_text(encoding="utf-8")

    assert state["status"] == "completed"
    assert exit_payload["status"] == "completed"
    assert "Run Summary" in summary


def test_persist_run_state_normalizes_legacy_auto_repair_promotion_strings(tmp_path: Path) -> None:
    run = {
        "user_request": "test task",
        "status": "completed",
        "auto_repair_promotions": ["legacy promotion note"],
    }
    run_files = init_run_files(run, tmp_path)
    run["auto_repair_promotions"] = ["legacy promotion note"]

    persist_run_state(run)

    state = json.loads(Path(run_files["state"]).read_text(encoding="utf-8"))
    assert state["auto_repair_promotions"] == [{"note": "legacy promotion note"}]


def test_load_events_helpers_read_jsonl_tail(tmp_path: Path) -> None:
    run = {"user_request": "test task"}
    run_files = init_run_files(run, tmp_path)
    events_path = Path(run_files["events"])
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-01-01T00:00:00", "event": 1}),
                json.dumps({"ts": "2026-01-01T00:00:01", "event": 2}),
                json.dumps({"ts": "2026-01-01T00:00:02", "event": 3}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert [event["event"] for event in load_recent_events(run, limit=2)] == [2, 3]
    assert [event["event"] for event in load_all_events(run)] == [1, 2, 3]


def test_small_helpers_handle_tail_and_timestamp_parsing(tmp_path: Path) -> None:
    text_path = tmp_path / "sample.txt"
    text_path.write_text("abcdef", encoding="utf-8")

    assert parse_event_epoch("2026-01-01T00:00:00") > 0
    assert tail_items([1, 2, 3], 2) == [2, 3]
    assert read_text_tail(text_path, max_chars=3) == "def"


def test_merge_recent_persisted_runs_rehydrates_nonterminal_run(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    runs_root = workspace_root / "runs"
    run_dir = runs_root / "20260327_000000_task_abcd"
    planner_dir = run_dir / "planner"
    planner_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    (run_dir / "stdout.log").write_text("", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")
    (run_dir / "execution.log").write_text("", encoding="utf-8")
    (run_dir / "summary.md").write_text("# Run Summary\n", encoding="utf-8")
    (run_dir / "path_decisions.json").write_text("{}", encoding="utf-8")
    (run_dir / "assistance_manifest.json").write_text("{}", encoding="utf-8")
    (run_dir / "exit.json").write_text('{"status":"planning"}', encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "plan_id": 7,
                "plan_kind": "executable",
                "user_request": "Run DESeq2 on airway counts",
                "chat_session_id": "run-7-123",
                "selected_dir": "/tmp/selected",
                "requested_data_root": "/tmp/data",
                "benchmark_policy": "scientific_harness",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "status": "planning",
                "error": "",
                "next_step_idx": 0,
                "step_statuses": [],
                "chat_session_id": "run-7-123",
                "planner_status": "planning",
                "planning_started_at": "2026-03-27T00:00:00",
                "requested_data_root": "/tmp/data",
                "selected_dir": "/tmp/selected",
                "updated_at": "2026-03-27T00:00:01",
            }
        ),
        encoding="utf-8",
    )

    merged, suggested_active_id = merge_recent_persisted_runs([], workspace_root=workspace_root)

    assert suggested_active_id == 1
    assert len(merged) == 1
    run = merged[0]
    assert run["status"] == "planning"
    assert run["user_request"] == "Run DESeq2 on airway counts"
    assert run["chat_session_id"] == "run-7-123"
    assert run["requested_data_root"] == "/tmp/data"


def test_merge_recent_persisted_runs_normalizes_legacy_auto_repair_promotions(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    runs_root = workspace_root / "runs"
    run_dir = runs_root / "20260327_000000_task_abcd"
    planner_dir = run_dir / "planner"
    planner_dir.mkdir(parents=True)
    for rel in [
        "events.jsonl",
        "stdout.log",
        "stderr.log",
        "execution.log",
        "summary.md",
        "path_decisions.json",
        "assistance_manifest.json",
    ]:
        (run_dir / rel).write_text("" if rel.endswith(".log") else "{}", encoding="utf-8")
    (run_dir / "summary.md").write_text("# Run Summary\n", encoding="utf-8")
    (run_dir / "exit.json").write_text('{"status":"planning"}', encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "plan_id": 7,
                "plan_kind": "executable",
                "user_request": "Run DESeq2 on airway counts",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": run_dir.name,
                "status": "planning",
                "auto_repair_promotions": ["legacy promotion note"],
                "updated_at": "2026-03-27T00:00:01",
            }
        ),
        encoding="utf-8",
    )

    merged, _ = merge_recent_persisted_runs([], workspace_root=workspace_root)

    assert merged[0]["auto_repair_promotions"] == [{"note": "legacy promotion note"}]


def test_merge_recent_persisted_runs_prefers_most_recent_run_after_refresh(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    runs_root = workspace_root / "runs"
    old_run_dir = runs_root / "20260327_000000_task_old"
    new_run_dir = runs_root / "20260327_000100_task_new"

    for run_dir in (old_run_dir, new_run_dir):
        (run_dir / "planner").mkdir(parents=True)
        (run_dir / "events.jsonl").write_text("", encoding="utf-8")
        (run_dir / "stdout.log").write_text("", encoding="utf-8")
        (run_dir / "stderr.log").write_text("", encoding="utf-8")
        (run_dir / "execution.log").write_text("", encoding="utf-8")
        (run_dir / "summary.md").write_text("# Run Summary\n", encoding="utf-8")
        (run_dir / "path_decisions.json").write_text("{}", encoding="utf-8")
        (run_dir / "assistance_manifest.json").write_text("{}", encoding="utf-8")

    (old_run_dir / "exit.json").write_text('{"status":"planning"}', encoding="utf-8")
    (old_run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": old_run_dir.name,
                "plan_id": 3,
                "plan_kind": "executable",
                "user_request": "Older stalled planning run",
                "chat_session_id": "run-3-old",
            }
        ),
        encoding="utf-8",
    )
    (old_run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": old_run_dir.name,
                "status": "planning",
                "chat_session_id": "run-3-old",
                "planner_status": "",
                "updated_at": "2026-03-27T13:06:00",
            }
        ),
        encoding="utf-8",
    )

    (new_run_dir / "exit.json").write_text('{"status":"completed"}', encoding="utf-8")
    (new_run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": new_run_dir.name,
                "plan_id": 4,
                "plan_kind": "executable",
                "user_request": "Most recent completed reconnect target",
                "chat_session_id": "run-4-new",
            }
        ),
        encoding="utf-8",
    )
    (new_run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": new_run_dir.name,
                "status": "completed",
                "chat_session_id": "run-4-new",
                "planner_status": "planned",
                "updated_at": "2026-03-27T13:05:00",
            }
        ),
        encoding="utf-8",
    )

    merged, suggested_active_id = merge_recent_persisted_runs([], workspace_root=workspace_root)

    assert len(merged) == 2
    new_run = next(run for run in merged if run["run_uid"] == new_run_dir.name)
    assert suggested_active_id == new_run["id"]
    assert new_run["chat_session_id"] == "run-4-new"
