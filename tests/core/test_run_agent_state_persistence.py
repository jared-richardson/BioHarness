"""Tests for terminal run-state persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bio_harness.core.file_manifest import FileManifest
from scripts.run_agent_e2e_state import AgentE2EStateMixin


class _DummyStateHarness(AgentE2EStateMixin):
    def __init__(self, tmp_path: Path) -> None:
        self.cfg = SimpleNamespace(
            selected_dir=tmp_path / "selected",
            data_root=tmp_path / "data",
            path_graph_user_key="default",
            path_graph_scope="global",
            result_json=tmp_path / "selected" / "result.json",
        )
        self.cfg.selected_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.data_root.mkdir(parents=True, exist_ok=True)
        self.path_graph = SimpleNamespace(db_path=tmp_path / "path_graph.sqlite")
        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "selected_dir": str(self.cfg.selected_dir),
                    "data_root": str(self.cfg.data_root),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self.run = {
            "run_uid": "run_terminal",
            "status": "completed",
            "error": "",
            "analysis_spec": {
                "analysis_type": "transcript_quantification",
                "literature_planning_support": {
                    "status": "applied",
                    "visible_to_planner": True,
                    "query_class": "parameter_recommendation",
                    "trigger_reason": "parameter_question:alpha",
                    "json_path": str(run_dir / "literature_planning_support.json"),
                    "markdown_path": str(run_dir / "literature_planning_support.md"),
                },
            },
            "plan": {"plan": [{"tool_name": "salmon_quant", "arguments": {}, "step_id": 1}]},
            "input_quality": {"summary": "Detected 0 input issue(s); blocking=false.", "issues": []},
            "in_run_quality_summary": {"suspicious_event_count": 0, "zero_byte_outputs": []},
            "literature_planning_support": {
                "status": "applied",
                "visible_to_planner": True,
                "query_class": "parameter_recommendation",
                "trigger_reason": "parameter_question:alpha",
            },
            "planning_attempts": [{"attempt": 1}],
            "auto_repair_history": [],
            "next_step_idx": 0,
            "step_statuses": ["completed"],
            "protocol_validation": {},
            "semantic_validation": {},
            "preexecution_stage_repairs": {
                "repair_applied": True,
                "removed_step_ids": [11],
                "moved_step_ids": [9],
                "rebinds": [],
                "unresolved_issues": [
                    {"issue": "missing_stage_producer", "stage": "annotated", "identity": "sample_B"}
                ],
            },
            "bash_placeholder_resolutions": [
                {
                    "step_id": 13,
                    "resolved": [
                        {
                            "token": "<reference_fasta>",
                            "value": "/tmp/ref.fa",
                            "source": "prior_step_arguments",
                        }
                    ],
                    "unresolved": [],
                }
            ],
            "contract_validation": {},
            "plan_contract": {},
            "auto_repair_attempts": {},
            "fallback_catalog_summary": [],
            "excluded_fallback_pipeline_ids": [],
            "missing_tools_detected": [],
            "missing_reference_detected": [],
            "missing_sample_groups": [],
            "missing_sample_group_signals": [],
            "observed_sample_groups": [],
            "observed_sample_group_sources": {},
            "failure_signatures": [],
            "failure_diagnosis": {},
            "research_report": {},
            "process_monitor_last": {},
            "stream_counters": {},
            "recent_stream_markers": [],
            "last_artifact_probe": {},
            "in_run_quality_recent_events": [],
            "in_run_quality_seen_files": {},
            "in_run_quality_emitted_event_keys": [],
            "planner_trace_dir": str(run_dir / "planner"),
            "started_at": "2026-01-01T00:00:00",
            "finished_at": "2026-01-01T00:05:00",
            "run_files": {
                "run_dir": str(run_dir),
                "state": str(run_dir / "state.json"),
                "events": str(run_dir / "events.jsonl"),
                "exec": str(run_dir / "execution.log"),
                "exit": str(run_dir / "exit.json"),
                "manifest": str(manifest_path),
                "assistance_manifest": str(run_dir / "assistance_manifest.json"),
                "completed_run_context": str(run_dir / "completed_run_context.json"),
            },
        }

    def _benchmark_policy(self) -> str:
        return "scientific_harness"

    def _write_assistance_manifest(self) -> None:
        return


def test_persist_state_writes_completed_run_context(tmp_path: Path) -> None:
    harness = _DummyStateHarness(tmp_path)

    harness._persist_state()

    context_payload = json.loads(
        (Path(harness.run["run_files"]["completed_run_context"])).read_text(encoding="utf-8")
    )
    assert context_payload["resolution_mode"] == "completed_run_context"
    assert context_payload["selected_dir"] == str(harness.cfg.selected_dir)
    assert context_payload["result"]["analysis_type"] == "transcript_quantification"
    assert context_payload["preexecution_stage_repairs"]["repair_applied"] is True
    assert context_payload["preexecution_stage_repairs"]["removed_step_ids"] == [11]
    assert context_payload["bash_placeholder_resolutions"][0]["step_id"] == 13
    assert context_payload["bash_placeholder_resolutions"][0]["resolved"][0]["token"] == "<reference_fasta>"


def test_persist_state_serializes_analysis_spec_file_manifest(tmp_path: Path) -> None:
    """Persisted state must not leak live FileManifest objects into JSON."""

    harness = _DummyStateHarness(tmp_path)
    manifest = FileManifest.from_discovered_files(
        [{"path": str(harness.cfg.data_root / "sample.bam")}],
        analysis_type="transcript_quantification",
        output_dir=str(harness.cfg.selected_dir),
    )
    harness.run["analysis_spec"]["file_manifest"] = manifest

    harness._persist_state()

    state_payload = json.loads(
        Path(harness.run["run_files"]["state"]).read_text(encoding="utf-8")
    )
    persisted_manifest = state_payload["analysis_spec"]["file_manifest"]
    assert isinstance(persisted_manifest, dict)
    assert persisted_manifest["entries"][0]["resolved_path"].endswith("sample.bam")


def test_assistance_manifest_payload_includes_literature_support_fields(tmp_path: Path) -> None:
    harness = _DummyStateHarness(tmp_path)

    payload = harness._assistance_manifest_payload()

    assert payload["literature_planning_support_status"] == "applied"
    assert payload["literature_planning_support_visible_to_planner"] is True
    assert payload["literature_planning_support_query_class"] == "parameter_recommendation"


def test_write_exit_omits_terminal_fields_for_nonterminal_runs(tmp_path: Path) -> None:
    harness = _DummyStateHarness(tmp_path)
    harness.run["status"] = "planned"
    harness.run["error"] = "Transient step failure."
    harness.run["finished_at"] = ""

    harness._write_exit()

    exit_payload = json.loads(Path(harness.run["run_files"]["exit"]).read_text(encoding="utf-8"))
    assert exit_payload["status"] == "planned"
    assert exit_payload["started_at"] == "2026-01-01T00:00:00"
    assert exit_payload["finished_at"] is None
    assert exit_payload["error"] == ""


def test_write_exit_preserves_terminal_fields(tmp_path: Path) -> None:
    harness = _DummyStateHarness(tmp_path)
    harness.run["error"] = "Final failure."
    harness.run["status"] = "failed"

    harness._write_exit()

    exit_payload = json.loads(Path(harness.run["run_files"]["exit"]).read_text(encoding="utf-8"))
    assert exit_payload["status"] == "failed"
    assert exit_payload["started_at"] == "2026-01-01T00:00:00"
    assert exit_payload["finished_at"] == "2026-01-01T00:05:00"
    assert exit_payload["error"] == "Final failure."
