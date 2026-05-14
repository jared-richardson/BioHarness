"""Tests for summarize-only in-run quality monitoring."""

from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.in_run_quality_monitor import (
    assess_in_run_quality,
    update_in_run_quality_state,
)


def test_assess_in_run_quality_detects_zero_byte_recent_output(tmp_path: Path) -> None:
    """Zero-byte recent outputs should emit summarize-only warning events."""

    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    (selected_dir / "final").mkdir()
    zero_byte = selected_dir / "final" / "results.tsv"
    zero_byte.write_text("", encoding="utf-8")

    summary, events, seen = assess_in_run_quality(
        selected_dir=selected_dir,
        artifact_tier={
            "recent_files": [{"path": "final/results.tsv", "size_bytes": 0, "mtime_epoch": 100.0}],
            "latest_mtime": 100.0,
            "scanned_files": 1,
        },
        plan={"plan": [{"step_id": 1, "tool_name": "bash_run", "expected_files": ["final/results.tsv"]}]},
        active_step_id=1,
        active_tool_name="bash_run",
    )

    assert summary.recent_output_count == 1
    assert summary.expected_output_count == 1
    assert summary.expected_outputs_present == ("final/results.tsv",)
    assert summary.zero_byte_outputs == ("final/results.tsv",)
    assert len(events) == 1
    assert events[0].category == "zero_byte_output"
    assert seen["final/results.tsv"] == 0


def test_update_in_run_quality_state_writes_summary_and_deduplicates_events(tmp_path: Path) -> None:
    """Repeated heartbeats should not re-emit the same suspicious event."""

    selected_dir = tmp_path / "selected"
    selected_dir.mkdir()
    (selected_dir / "final").mkdir()
    (selected_dir / "final" / "results.tsv").write_text("", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    summary_path = run_dir / "in_run_quality_summary.json"
    events_path = run_dir / "in_run_quality_events.jsonl"
    events_path.write_text("", encoding="utf-8")

    run = {
        "plan": {"plan": [{"step_id": 1, "tool_name": "bash_run", "expected_files": ["final/results.tsv"]}]},
        "in_run_quality_seen_files": {},
        "in_run_quality_emitted_event_keys": [],
        "in_run_quality_recent_events": [],
    }
    artifact_tier = {
        "recent_files": [{"path": "final/results.tsv", "size_bytes": 0, "mtime_epoch": 100.0}],
        "latest_mtime": 100.0,
        "scanned_files": 1,
    }
    run_files = {
        "in_run_quality_summary": str(summary_path),
        "in_run_quality_events": str(events_path),
    }

    summary_payload, event_payloads = update_in_run_quality_state(
        run,
        selected_dir=selected_dir,
        artifact_tier=artifact_tier,
        active_step_id=1,
        active_tool_name="bash_run",
        run_files=run_files,
    )

    assert summary_payload["suspicious_event_count"] == 1
    assert len(event_payloads) == 1
    assert json.loads(summary_path.read_text(encoding="utf-8"))["zero_byte_outputs"] == ["final/results.tsv"]
    assert len(events_path.read_text(encoding="utf-8").strip().splitlines()) == 1

    second_summary, second_events = update_in_run_quality_state(
        run,
        selected_dir=selected_dir,
        artifact_tier=artifact_tier,
        active_step_id=1,
        active_tool_name="bash_run",
        run_files=run_files,
    )

    assert second_summary["suspicious_event_count"] == 0
    assert second_events == ()
    assert len(events_path.read_text(encoding="utf-8").strip().splitlines()) == 1
