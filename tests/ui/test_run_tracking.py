from __future__ import annotations

from collections import deque

import bio_harness.ui.run_tracking as run_tracking
from bio_harness.ui.run_tracking import (
    append_run_log,
    append_tail,
    parse_log_channel,
    run_has_live_executor_process,
    summarize_command_for_ui,
    update_process_tracker_from_log,
)


def test_append_tail_and_run_log_trim_buffers() -> None:
    chunks: deque[str] = deque()
    append_tail(chunks, "abcdef", max_bytes=5)

    run = {}
    append_run_log(run, "line-1", max_lines=1)
    append_run_log(run, "line-2", max_lines=1)

    assert list(chunks) == []
    assert run["logs"] == ["line-2"]


def test_summarize_command_for_ui_prefers_known_tool_patterns() -> None:
    assert "STAR" in summarize_command_for_ui("STAR --genomeGenerate --genomeDir idx")
    assert summarize_command_for_ui("fastqc sample.fastq.gz") == "Running FastQC quality checks"
    assert summarize_command_for_ui("samtools flagstat sample.bam") == "Running `samtools`"


def test_update_process_tracker_from_log_tracks_step_lifecycle() -> None:
    run = {"step_statuses": ["pending", "pending"]}

    update_process_tracker_from_log(run, "--- Executing Step 1: bash_run ---")
    update_process_tracker_from_log(run, "[Step 1 Output] [command] fastqc sample.fastq.gz")
    update_process_tracker_from_log(run, "[Step 1 Output] [status] [status] running pid=123 elapsed=5s")
    update_process_tracker_from_log(run, "--- Step 1 (bash_run) finished ---")

    process = run["process_tracker"]["1"]
    assert process["title"] == "Running FastQC quality checks"
    assert process["active_pid"] is None
    assert run["step_statuses"][0] == "completed"
    assert run["next_step_idx"] == 1


def test_parse_log_channel_extracts_prefixed_channels() -> None:
    assert parse_log_channel("[Step 2 Output] [stdout] hello") == ("stdout", "hello")
    assert parse_log_channel("[Step 2 Output] [stderr] boom") == ("stderr", "boom")
    assert parse_log_channel("plain line") == ("live", "plain line")


def test_run_has_live_executor_process_uses_active_pid(monkeypatch) -> None:
    run = {
        "process_order": ["1"],
        "process_tracker": {
            "1": {
                "status": "running",
                "active_pid": 4242,
                "status_text": "",
            }
        },
    }
    monkeypatch.setattr(run_tracking, "_is_pid_live", lambda pid: pid == 4242)

    assert run_has_live_executor_process(run) is True
