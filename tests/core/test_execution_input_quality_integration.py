from __future__ import annotations

from pathlib import Path

from bio_harness.core.benchmark_asset_integrity import (
    render_bioagentbench_deseq_sample_metadata,
)
from bio_harness.core.input_quality import InputIssue, InputScanResult
from scripts.run_agent_e2e_execution import AgentE2EExecutionMixin


class _DummyExecutionHarness(AgentE2EExecutionMixin):
    def __init__(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        self.cfg = type(
            "Cfg",
            (),
            {
                "data_root": tmp_path / "data",
                "selected_dir": tmp_path / "selected",
                "quiet": True,
                "heartbeat_seconds": 15,
            },
        )()
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        self.cfg.selected_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "in_run_quality_events.jsonl").write_text("", encoding="utf-8")
        self.run = {
            "run_uid": "run_dummy",
            "analysis_spec": {"analysis_type": "rna_seq_differential_expression"},
            "plan": {"plan": []},
            "plan_contract": {},
            "last_executor_event_ts": 0.0,
            "run_files": {
                "preflight_summary": str(run_dir / "preflight_summary.json"),
                "preflight_summary_md": str(run_dir / "preflight_summary.md"),
                "in_run_quality_events": str(run_dir / "in_run_quality_events.jsonl"),
                "in_run_quality_summary": str(run_dir / "in_run_quality_summary.json"),
            },
        }
        self.events: list[dict[str, object]] = []

    def _append_event(self, *, step_id, agent, event_type, severity, payload) -> None:  # noqa: ANN001
        self.events.append(
            {
                "step_id": step_id,
                "agent": agent,
                "event_type": event_type,
                "severity": severity,
                "payload": payload,
            }
        )

    def _adaptive_live_process_grace_seconds(self, **_kwargs) -> int:
        return 60


def test_preflight_records_input_quality_scan(monkeypatch, tmp_path: Path) -> None:
    harness = _DummyExecutionHarness(tmp_path)

    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution.scan_plan_inputs",
        lambda *args, **kwargs: InputScanResult(
            issues=(
                InputIssue(
                    path="/tmp/reads.fastq.gz",
                    severity="warning",
                    category="short_reads",
                    message="FASTQ contains very short reads.",
                    suggestion="Confirm the assay supports short reads.",
                ),
            ),
            has_blocking=False,
            summary="Detected 1 input issue(s); blocking=false.",
        ),
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution._preflight_execution_issues",
        lambda *args, **kwargs: {
            "missing_data_root": False,
            "missing_fastq": False,
            "missing_references": [],
            "missing_groups": [],
        },
    )

    ok, message = harness._preflight()

    assert ok is True
    assert message == ""
    assert harness.run["input_quality"]["summary"] == "Detected 1 input issue(s); blocking=false."
    assert harness.events[0]["event_type"] == "INPUT_QUALITY_SCAN"
    assert Path(harness.run["run_files"]["preflight_summary"]).exists()
    assert Path(harness.run["run_files"]["preflight_summary_md"]).exists()


def test_preflight_handles_deseq_design_formula_without_path_error(tmp_path: Path) -> None:
    harness = _DummyExecutionHarness(tmp_path)
    counts = harness.cfg.data_root / "counts.tsv"
    metadata = harness.cfg.data_root / "metadata.tsv"
    counts.write_text("gene\ts1\ts2\nGeneA\t1\t2\n")
    metadata.write_text("sample\tcondition\ns1\tcontrol\ns2\ttreatment\n")
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "step_id": 1,
                "arguments": {
                    "counts_matrix": str(counts),
                    "metadata_table": str(metadata),
                    "design_formula": "~ condition",
                    "contrast": '["condition", "treatment", "control"]',
                },
            }
        ]
    }

    ok, message = harness._preflight()

    assert ok is True
    assert message == ""
    assert "input_quality" in harness.run


def test_preflight_blocks_when_input_quality_has_blocking_issue(monkeypatch, tmp_path: Path) -> None:
    harness = _DummyExecutionHarness(tmp_path)

    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution.scan_plan_inputs",
        lambda *args, **kwargs: InputScanResult(
            issues=(
                InputIssue(
                    path="/tmp/reads.fastq.gz",
                    severity="error",
                    category="truncated_file",
                    message="FASTQ ends mid-record.",
                    suggestion="Replace the truncated FASTQ with a complete file.",
                ),
            ),
            has_blocking=True,
            summary="Detected 1 input issue(s); blocking=true.",
        ),
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution._preflight_execution_issues",
        lambda *args, **kwargs: {
            "missing_data_root": False,
            "missing_fastq": False,
            "missing_references": [],
            "missing_groups": [],
        },
    )

    ok, message = harness._preflight()

    assert ok is False
    assert "blocking input-quality issues" in message
    assert "truncated_file" in message
    assert harness.run["input_quality"]["has_blocking"] is True


def test_preflight_repairs_canonical_bioagentbench_deseq_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    harness = _DummyExecutionHarness(tmp_path)
    data_root = (
        tmp_path
        / "workspace"
        / "benchmarks"
        / "bioagent-bench"
        / "tasks"
        / "deseq"
        / "data"
    )
    data_root.mkdir(parents=True, exist_ok=True)
    metadata_path = data_root / "sample_metadata.tsv"
    metadata_path.write_text(
        "sample\tcondition\ncondition\tunknown\n",
        encoding="utf-8",
    )
    harness.cfg.data_root = data_root
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "step_id": 1,
                "arguments": {
                    "metadata_table": str(metadata_path),
                },
            }
        ]
    }
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution._preflight_execution_issues",
        lambda *args, **kwargs: {
            "missing_data_root": False,
            "missing_fastq": False,
            "missing_references": [],
            "missing_groups": [],
        },
    )

    ok, message = harness._preflight()

    assert ok is True
    assert message == ""
    assert metadata_path.read_text(encoding="utf-8") == (
        render_bioagentbench_deseq_sample_metadata()
    )
    assert harness.run["benchmark_asset_repairs"]["matched_profile"] == "bioagentbench_deseq_v1"
    assert harness.run["benchmark_asset_repairs"]["changed"] is True
    assert any(event["event_type"] == "BENCHMARK_ASSET_REPAIR" for event in harness.events)


def test_preflight_does_not_repair_arbitrary_metadata_roots(
    monkeypatch,
    tmp_path: Path,
) -> None:
    harness = _DummyExecutionHarness(tmp_path)
    metadata_path = harness.cfg.data_root / "sample_metadata.tsv"
    original_text = "sample\tcondition\ncondition\tunknown\n"
    metadata_path.write_text(original_text, encoding="utf-8")
    harness.run["plan"] = {
        "plan": [
            {
                "tool_name": "deseq2_run",
                "step_id": 1,
                "arguments": {
                    "metadata_table": str(metadata_path),
                },
            }
        ]
    }
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution._preflight_execution_issues",
        lambda *args, **kwargs: {
            "missing_data_root": False,
            "missing_fastq": False,
            "missing_references": [],
            "missing_groups": [],
        },
    )

    ok, message = harness._preflight()

    assert ok is False
    assert "insufficient_samples" in message
    assert metadata_path.read_text(encoding="utf-8") == original_text
    assert harness.run["benchmark_asset_repairs"]["matched_profile"] == ""
    assert harness.run["benchmark_asset_repairs"]["changed"] is False
    assert not any(event["event_type"] == "BENCHMARK_ASSET_REPAIR" for event in harness.events)


def test_emit_execution_heartbeat_records_in_run_quality_events(monkeypatch, tmp_path: Path) -> None:
    harness = _DummyExecutionHarness(tmp_path)
    output_path = harness.cfg.selected_dir / "final" / "results.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")
    harness.run["plan"] = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "expected_files": ["final/results.tsv"],
                "arguments": {},
            }
        ]
    }

    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution.collect_process_snapshot",
        lambda *_args, **_kwargs: {
            "tree_cpu_seconds": 0.0,
            "inferred_tool": "bash_run",
            "live_process_count": 1,
        },
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution._stream_evidence",
        lambda *_args, **_kwargs: {"tier": "quiet"},
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution.collect_recent_outputs",
        lambda *_args, **_kwargs: {
            "recent_files": [{"path": "final/results.tsv", "size_bytes": 0, "mtime_epoch": 100.0}],
            "latest_mtime": 100.0,
            "scanned_files": 1,
        },
    )

    state = harness._new_execution_monitor_state()
    state.active_step_id = 1
    state.active_tool_name = "bash_run"
    state.active_step_started_ts = 50.0
    state.last_progress_ts = 50.0

    harness._emit_execution_heartbeat(state, now_ts=120.0)

    assert harness.run["in_run_quality_summary"]["zero_byte_outputs"] == ("final/results.tsv",)
    assert any(event["event_type"] == "IN_RUN_QUALITY_EVENT" for event in harness.events)
