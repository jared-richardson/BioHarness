from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.benchmark_policy import OFFICIAL_BIOAGENTBENCH_POLICY
from bio_harness.core.file_manifest import FileManifest, ManifestEntry
from bio_harness.harness.config import HarnessConfig
from scripts.run_agent_e2e import _ensure_result_json, _write_result_json
from scripts.run_agent_e2e_harness import AgentE2EHarness


def _build_harness(tmp_path: Path) -> tuple[AgentE2EHarness, HarnessConfig]:
    selected_dir = tmp_path / "workspace"
    data_root = selected_dir / "inputs_readonly"
    result_json = tmp_path / "result.json"
    data_root.mkdir(parents=True, exist_ok=True)
    cfg = HarnessConfig(
        prompt="test result backstop",
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=selected_dir,
        max_repairs=0,
        heartbeat_seconds=15,
        stall_timeout_seconds=45,
        live_process_grace_seconds=900,
        model_name=None,
        host=None,
        llm_backend=None,
        auto_install_missing_tools=False,
        allow_replan=True,
        allow_canonicalize=True,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
        plan_path=None,
        result_json=result_json,
        quiet=True,
        print_plan=False,
        path_graph_db=selected_dir / "knowledge" / "path_graph.sqlite",
        path_graph_user_key="default",
        path_graph_scope="global",
        path_graph_persist_preference_updates=False,
        auto_setup_isolated_tools=False,
    )
    harness = AgentE2EHarness(cfg)
    harness._init_run()
    return harness, cfg


def test_ensure_result_json_backfills_failure_payload(tmp_path: Path) -> None:
    harness, cfg = _build_harness(tmp_path)
    stderr_path = Path(harness.run["run_files"]["stderr"])
    stderr_path.write_text("No such file or directory: /tmp/missing.fastq.gz\n", encoding="utf-8")
    harness.run["step_statuses"] = ["failed"]
    harness.run["plan"] = {"plan": [{"tool_name": "cutadapt_run", "arguments": {"input_fastq": "/tmp/missing.fastq.gz"}}]}
    harness.run["error"] = "Step 1 failed with exit code 1"

    _ensure_result_json(
        harness,
        cfg,
        fallback_error="unit test termination",
    )

    payload = json.loads(cfg.result_json.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert "unit test termination" in payload["error"]
    assert payload["run_id"] == harness.run["run_uid"]
    assert payload["failure_diagnosis"]["tool_name"] == "cutadapt_run"


def test_ensure_result_json_preserves_existing_payload(tmp_path: Path) -> None:
    harness, cfg = _build_harness(tmp_path)
    cfg.result_json.write_text(json.dumps({"status": "completed", "run_id": "done"}), encoding="utf-8")

    _ensure_result_json(
        harness,
        cfg,
        fallback_error="should not overwrite",
    )

    payload = json.loads(cfg.result_json.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["run_id"] == "done"


def test_write_result_json_serializes_file_manifest_payload(tmp_path: Path) -> None:
    result_json = tmp_path / "result.json"
    manifest = FileManifest(
        entries=[
            ManifestEntry(
                role="reference_genome",
                resolved_path="/data/ref.fa",
                file_type="fasta",
            )
        ],
        output_dir="/workspace/out",
    )

    _write_result_json(
        result_json,
        {
            "run_id": "run",
            "status": "failed",
            "input_quality": {"file_manifest": manifest},
        },
    )

    payload = json.loads(result_json.read_text(encoding="utf-8"))
    assert payload["input_quality"]["file_manifest"]["output_dir"] == "/workspace/out"
    assert (
        payload["input_quality"]["file_manifest"]["entries"][0]["resolved_path"]
        == "/data/ref.fa"
    )


def test_terminal_state_context_serializes_file_manifest_payload(tmp_path: Path) -> None:
    harness, _cfg = _build_harness(tmp_path)
    manifest = FileManifest(
        entries=[
            ManifestEntry(
                role="reference_genome",
                resolved_path="/data/ref.fa",
                file_type="fasta",
            )
        ],
        output_dir="/workspace/out",
    )
    harness.run["status"] = "failed"
    harness.run["error"] = "unit test terminal failure"
    harness.run["input_quality"] = {"file_manifest": manifest}

    harness._persist_state()

    context_path = Path(harness.run["run_files"]["completed_run_context"])
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["state"]["input_quality"]["file_manifest"]["output_dir"] == (
        "/workspace/out"
    )


def test_completed_finalize_clears_repaired_stepwise_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness, _cfg = _build_harness(tmp_path)
    harness.run["status"] = "completed"
    harness.run["error"] = "One or more steps failed."
    harness.run["stepwise_last_step_failed"] = True
    harness.run["step_statuses"] = ["completed", "failed", "completed"]
    harness.run["contract_validation"] = {"passed": True}

    harness._assess_completed_run_contract = lambda: {"passed": True}  # type: ignore[method-assign]
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution.package_deliverables",
        lambda **kwargs: {"exported": [], "failures": []},
    )
    monkeypatch.setattr(
        "scripts.run_agent_e2e_execution._verify_run_outputs",
        lambda selected_dir, plan: (True, ""),
    )

    harness._finalize_completed_run()
    harness.run["finished_at"] = "2026-04-25T00:00:00+00:00"
    harness._write_exit()

    summary = Path(harness.run["run_files"]["summary"]).read_text(encoding="utf-8")
    exit_payload = json.loads(Path(harness.run["run_files"]["exit"]).read_text())

    assert harness.run["error"] == ""
    assert harness.run["stepwise_last_step_failed"] is False
    assert exit_payload["error"] == ""
    assert "- Error: none" in summary
    assert "- Contract validation: passed" in summary
    assert "- Repaired failed attempts retained in trace: 1" in summary
