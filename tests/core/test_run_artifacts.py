"""Tests for bio_harness.core.run_artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.core.file_manifest import FileManifest, ManifestEntry
from bio_harness.core.run_artifacts import (
    append_event,
    append_line,
    init_run_artifacts,
    make_run_id,
    slugify_task,
    write_exit,
    write_manifest,
    write_path_decisions,
    write_state,
)
from bio_harness.core.schemas import ARTIFACT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# slugify_task
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Align Reads", "align_reads"),
        ("  Hello World!  ", "hello_world"),
        ("STAR --runMode genomeGenerate", "star_runmode_genomegenerate"),
        ("", "task"),
        ("   ", "task"),
        (None, "task"),
        ("a" * 100, "a" * 28),
    ],
    ids=[
        "simple_phrase",
        "whitespace_special_chars",
        "command_like",
        "empty_string",
        "whitespace_only",
        "none_value",
        "long_string_truncated",
    ],
)
def test_slugify_task(text: str | None, expected: str):
    assert slugify_task(text) == expected


def test_slugify_task_custom_max_len():
    assert slugify_task("align reads to genome", max_len=10) == "align_read"


# ---------------------------------------------------------------------------
# make_run_id
# ---------------------------------------------------------------------------


def test_make_run_id_format():
    run_id = make_run_id("Align Reads")
    parts = run_id.split("_")
    # Expected format: YYYYMMDD_HHMMSS_<slug>_<hex>
    assert len(parts) >= 4
    # Date part should be 8 digits
    assert len(parts[0]) == 8 and parts[0].isdigit()
    # Time part should be 6 digits
    assert len(parts[1]) == 6 and parts[1].isdigit()
    # Slug portion
    assert "align" in run_id.lower()
    # Hex suffix should be 4 chars (token_hex(2))
    assert len(parts[-1]) == 4


def test_make_run_id_unique():
    id1 = make_run_id("task")
    id2 = make_run_id("task")
    assert id1 != id2


# ---------------------------------------------------------------------------
# init_run_artifacts
# ---------------------------------------------------------------------------


def test_init_run_artifacts_creates_files(tmp_path: Path):
    files = init_run_artifacts(tmp_path, "Test Task")

    assert "run_id" in files
    assert "run_dir" in files
    assert "executor_runtime" in files
    assert "preflight_summary" in files
    assert "preflight_summary_md" in files
    assert "completed_run_context" in files
    assert "in_run_quality_events" in files
    assert "in_run_quality_summary" in files
    assert files["run_dir"].is_dir()

    # Verify core files exist
    for key in (
        "state",
        "events",
        "stdout",
        "stderr",
        "exec",
        "exit",
        "manifest",
        "in_run_quality_events",
        "in_run_quality_summary",
    ):
        assert files[key].exists(), f"Missing artifact: {key}"

    assert files["preflight_summary"].exists() is False
    assert files["preflight_summary_md"].exists() is False
    assert files["completed_run_context"].exists() is False

    # Planner directory should be created
    assert files["planner"].is_dir()


def test_init_run_artifacts_state_json(tmp_path: Path):
    files = init_run_artifacts(tmp_path, "Test Task")
    state = json.loads(files["state"].read_text(encoding="utf-8"))
    assert state["status"] == "initialized"
    assert state["run_id"] == files["run_id"]
    assert state["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_init_run_artifacts_exit_json(tmp_path: Path):
    files = init_run_artifacts(tmp_path, "Test Task")
    exit_data = json.loads(files["exit"].read_text(encoding="utf-8"))
    assert exit_data["status"] == "running"
    assert exit_data["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_init_run_artifacts_path_decisions_json(tmp_path: Path):
    files = init_run_artifacts(tmp_path, "Test Task")
    pd = json.loads(files["path_decisions"].read_text(encoding="utf-8"))
    assert pd["user_requested_root"] == ""
    assert pd["rejected_candidates"] == []


def test_init_run_artifacts_runs_subdir(tmp_path: Path):
    files = init_run_artifacts(tmp_path, "Test Task")
    # Run dir should be under workspace/runs/
    assert files["run_dir"].parent == tmp_path / "runs"


# ---------------------------------------------------------------------------
# append_event
# ---------------------------------------------------------------------------


def test_append_event_writes_jsonl(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("", encoding="utf-8")

    event = append_event(
        events_file,
        run_id="run_001",
        step_id=1,
        agent="test_agent",
        event_type="step_complete",
        severity="info",
        payload={"status": "ok"},
    )

    assert event["run_id"] == "run_001"
    assert event["step_id"] == 1
    assert "ts" in event

    # Verify it was written as JSONL
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "step_complete"
    assert parsed["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_append_event_appends_multiple(tmp_path: Path):
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("", encoding="utf-8")

    for i in range(3):
        append_event(
            events_file,
            run_id="run_001",
            step_id=i,
            agent="test",
            event_type="progress",
            severity="info",
            payload={"step": i},
        )

    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3


# ---------------------------------------------------------------------------
# append_line
# ---------------------------------------------------------------------------


def test_append_line(tmp_path: Path):
    log = tmp_path / "test.log"
    log.write_text("", encoding="utf-8")

    append_line(log, "first line\n")
    append_line(log, "second line\n")

    content = log.read_text(encoding="utf-8")
    assert content == "first line\nsecond line\n"


# ---------------------------------------------------------------------------
# write_state / write_exit / write_manifest
# ---------------------------------------------------------------------------


def test_write_state(tmp_path: Path):
    state_file = tmp_path / "state.json"
    write_state(state_file, {"run_id": "test_run", "status": "running", "step": 2})

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert data["step"] == 2
    assert data["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_write_state_serializes_dataclass_extra_fields(tmp_path: Path):
    state_file = tmp_path / "state.json"
    manifest = FileManifest(
        entries=[
            ManifestEntry(
                role="input_fastq_r1",
                resolved_path="/data/sample_R1.fastq.gz",
                file_type="fastq",
                sample_id="sample",
            )
        ],
        output_dir="/workspace/out",
    )

    write_state(
        state_file,
        {
            "run_id": "test_run",
            "status": "failed",
            "input_quality": {"file_manifest": manifest},
        },
    )

    data = json.loads(state_file.read_text(encoding="utf-8"))
    manifest_payload = data["input_quality"]["file_manifest"]
    assert manifest_payload["output_dir"] == "/workspace/out"
    assert manifest_payload["entries"][0]["role"] == "input_fastq_r1"


def test_write_exit(tmp_path: Path):
    exit_file = tmp_path / "exit.json"
    write_exit(exit_file, {"run_id": "test_run", "status": "completed", "exit_code": 0})

    data = json.loads(exit_file.read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    assert data["schema_version"] == ARTIFACT_SCHEMA_VERSION


def test_write_manifest(tmp_path: Path):
    manifest_file = tmp_path / "manifest.json"
    write_manifest(manifest_file, {"run_id": "test_run", "tools": ["samtools"]})

    data = json.loads(manifest_file.read_text(encoding="utf-8"))
    assert data["run_id"] == "test_run"
    assert data["schema_version"] == ARTIFACT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# write_path_decisions
# ---------------------------------------------------------------------------


def test_write_path_decisions(tmp_path: Path):
    pd_file = tmp_path / "path_decisions.json"
    write_path_decisions(
        pd_file,
        user_requested_root="/data/input",
        resolved_root="/workspace/inputs_readonly",
        resolution_reason="symlink_resolved",
        rejected_candidates=[
            {"candidate": "/bad/path", "reason": "outside_root"}
        ],
    )

    data = json.loads(pd_file.read_text(encoding="utf-8"))
    assert data["user_requested_root"] == "/data/input"
    assert data["resolved_root"] == "/workspace/inputs_readonly"
    assert len(data["rejected_candidates"]) == 1
    assert data["rejected_candidates"][0]["reason"] == "outside_root"
