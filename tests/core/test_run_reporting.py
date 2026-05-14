from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bio_harness.core import tool_env
from bio_harness.reporting.report_bundle import build_run_report_bundle
from bio_harness.reporting.ro_crate import export_run_ro_crate
from bio_harness.reporting.run_context import (
    build_artifact_inventory,
    build_completed_run_context_payload,
    build_live_result_payload,
    render_step_command,
    resolve_run_context,
)
from bio_harness.reporting.workflow_exchange import export_workflow_exchange_bundle, load_trs_tool_metadata


def _make_fake_run(tmp_path: Path) -> tuple[Path, Path]:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True)
    (selected_dir / "final").mkdir()
    (selected_dir / "final" / "results.csv").write_text("gene,log2fc\nA,1.2\n", encoding="utf-8")
    (selected_dir / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")
    (selected_dir / "harness.log").write_text("planning-heartbeat\n", encoding="utf-8")

    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    (run_dir / "planner").mkdir()
    (run_dir / "events.jsonl").write_text('{"event_type":"PLANNER_ATTEMPT_STARTED","payload":{"attempt":1}}\n', encoding="utf-8")
    (run_dir / "execution.log").write_text("step complete\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "selected_dir": str(selected_dir),
                "data_root": str(data_root),
                "benchmark_policy": "bioagentbench_planning_strict",
                "path_graph_db": str(selected_dir / "knowledge" / "path_graph.sqlite"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "in_run_quality_summary.json").write_text(
        json.dumps(
            {
                "active_step_id": 1,
                "tool_name": "bash_run",
                "recent_output_count": 1,
                "new_output_count": 1,
                "expected_output_count": 1,
                "expected_outputs_present": [],
                "expected_outputs_missing": ["final/results.csv"],
                "zero_byte_outputs": ["final/results.csv"],
                "suspicious_event_count": 1,
                "latest_output_mtime": 100.0,
                "scanned_files": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "analysis_spec": {
                    "analysis_type": "rna_seq_differential_expression",
                    "data_root": str(data_root),
                },
                "in_run_quality_summary": {
                    "active_step_id": 1,
                    "tool_name": "bash_run",
                    "recent_output_count": 1,
                    "new_output_count": 1,
                    "expected_output_count": 1,
                    "expected_outputs_present": [],
                    "expected_outputs_missing": ["final/results.csv"],
                    "zero_byte_outputs": ["final/results.csv"],
                    "suspicious_event_count": 1,
                    "latest_output_mtime": 100.0,
                    "scanned_files": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "planner" / "0001_hierarchical_plan_success.txt").write_text(
        json.dumps(
            {
                "thought_process": "fake plan",
                "plan": [
                    {
                        "tool_name": "bash_run",
                        "arguments": {"command": "echo 'hello world' > final.txt"},
                        "step_id": 1,
                    },
                    {
                        "tool_name": "vep_annotate",
                        "arguments": {
                            "assembly": "GRCh38",
                            "input_vcf": "/tmp/input.vcf.gz",
                            "output_vcf": "/tmp/output.vcf.gz",
                        },
                        "step_id": 2,
                    },
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (selected_dir / "result.json").write_text(
        json.dumps(
            {
                "selected_dir": str(selected_dir),
                "run_dir": str(run_dir),
                "status": "completed",
                "benchmark_policy": "bioagentbench_planning_strict",
                "auto_repair_history_count": 0,
                "planning_attempts": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return selected_dir, run_dir


def _make_failed_run(tmp_path: Path) -> Path:
    selected_dir, run_dir = _make_fake_run(tmp_path)
    stderr_path = run_dir / "stderr.log"
    stderr_path.write_text("Permission denied: /tmp/protected.bam\n", encoding="utf-8")
    (selected_dir / "result.json").write_text(
        json.dumps(
            {
                "selected_dir": str(selected_dir),
                "run_dir": str(run_dir),
                "status": "failed",
                "benchmark_policy": "bioagentbench_planning_strict",
                "auto_repair_history_count": 0,
                "planning_attempts": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "error": "Step 1 failed with exit code 13",
                "step_statuses": ["failed", "planned"],
                "analysis_spec": {"analysis_type": "rna_seq_differential_expression"},
                "run_files": {"stderr": str(stderr_path)},
                "plan": {
                    "plan": [
                        {
                            "tool_name": "samtools_flagstat",
                            "arguments": {"input_bam": "/tmp/protected.bam"},
                            "step_id": 1,
                        }
                    ]
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return selected_dir


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_run_context_resolves_final_plan_and_artifacts(tmp_path: Path) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)

    context = resolve_run_context(selected_dir)
    inventory = build_artifact_inventory(context)

    assert context.final_plan_path is not None
    assert context.resolution_mode == "result_json"
    assert len(context.final_plan["plan"]) == 2
    assert any(row["category"] == "final_output" for row in inventory)
    assert render_step_command(context.final_plan["plan"][0]) == "echo 'hello world' > final.txt"
    rendered = render_step_command(context.final_plan["plan"][1])
    assert "vep" in rendered
    assert "--cache --offline" in rendered


def test_render_step_command_supports_grouped_skill_modules_without_side_effects(tmp_path: Path) -> None:
    fastq = tmp_path / "reads.fastq.gz"
    fastq.write_text("x", encoding="utf-8")
    output_dir = tmp_path / "qc"

    rendered = render_step_command(
        {
            "tool_name": "fastqc_run",
            "arguments": {
                "input_file": str(fastq),
                "output_dir": str(output_dir),
            },
        }
    )

    assert rendered.startswith(f"mkdir -p {output_dir} && fastqc --outdir {output_dir}")
    assert output_dir.exists() is False


def test_export_run_ro_crate_writes_expected_bundle(tmp_path: Path) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)

    crate_dir = export_run_ro_crate(selected_dir)

    assert (crate_dir / "ro-crate-metadata.json").exists()
    assert (crate_dir / "run_summary.json").exists()
    assert (crate_dir / "workflow_plan.json").exists()
    assert (crate_dir / "artifact_manifest.csv").exists()


def test_export_workflow_exchange_bundle_writes_exchange_files(tmp_path: Path) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)

    export_dir = export_workflow_exchange_bundle(selected_dir)

    assert (export_dir / "workflow.cwl").exists()
    assert (export_dir / "workflow.wdl").exists()
    assert (export_dir / "steps" / "step_01.cwl").exists()
    assert (export_dir / "trs_tool.json").exists()
    assert (export_dir / "wes_requests" / "cwl_request.json").exists()
    assert (export_dir / "tes_tasks.json").exists()
    trs = load_trs_tool_metadata(export_dir / "trs_tool.json")
    assert trs["toolclass"]["name"] == "Workflow"


def test_build_run_report_bundle_writes_summary_and_figures(tmp_path: Path) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)

    report_dir = build_run_report_bundle(selected_dir)

    assert (report_dir / "summary.json").exists()
    assert (report_dir / "summary.md").exists()
    assert (report_dir / "report.qmd").exists()
    assert (report_dir / "output_catalog.json").exists()
    assert (report_dir / "output_catalog.md").exists()
    assert (report_dir / "preflight_summary.json").exists()
    assert (report_dir / "preflight_summary.md").exists()
    assert (report_dir / "in_run_quality_summary.json").exists()
    assert (report_dir / "in_run_quality_summary.md").exists()
    assert (report_dir / "interpretation.json").exists()
    assert (report_dir / "interpretation.md").exists()
    assert (report_dir / "result_review.json").exists()
    assert (report_dir / "result_review.md").exists()
    assert (report_dir / "figures" / "run_overview.png").exists()
    tooling = json.loads((report_dir / "tooling_status.json").read_text(encoding="utf-8"))
    assert tooling["multiqc"]["attempted"] is False
    assert tooling["quarto"]["attempted"] is False
    catalog = json.loads((report_dir / "output_catalog.json").read_text(encoding="utf-8"))
    assert {Path(item["path"]).name for item in catalog["reviewable_entries"]} == {"results.csv"}
    preflight = json.loads((report_dir / "preflight_summary.json").read_text(encoding="utf-8"))
    assert preflight["recommendation"] == "proceed"
    assert preflight["input_scan_source"] == "rescanned"
    in_run_quality = json.loads((report_dir / "in_run_quality_summary.json").read_text(encoding="utf-8"))
    assert in_run_quality["suspicious_event_count"] == 1
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["context_mode"] == "result_json"
    assert summary["in_run_quality"]["available"] is True
    assert summary["in_run_quality"]["zero_byte_output_count"] == 1
    assert (report_dir / "completed_run_context.json").exists()
    summary_md = (report_dir / "summary.md").read_text(encoding="utf-8")
    assert "In-run quality:" in summary_md
    assert "Context mode:" in summary_md
    interpretation = json.loads((report_dir / "interpretation.json").read_text(encoding="utf-8"))
    assert interpretation["analysis_type"] == "rna_seq_differential_expression"
    review = json.loads((report_dir / "result_review.json").read_text(encoding="utf-8"))
    assert review["decision"]["decision"]


def test_build_run_report_bundle_handles_list_planning_attempts(tmp_path: Path) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)
    result_path = selected_dir / "result.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["planning_attempts"] = [{"attempt": 1}, {"attempt": 2}]
    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    report_dir = build_run_report_bundle(selected_dir)
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))

    assert summary["planner_attempts"] == 2


def test_build_run_report_bundle_uses_shared_tool_resolution_for_optional_tools(tmp_path: Path, monkeypatch) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "bio_harness.reporting.report_bundle.requirement_available",
        lambda name: name in {"multiqc", "quarto"},
    )
    monkeypatch.setattr(
        "bio_harness.reporting.report_bundle.which_with_pixi",
        lambda name: f"/sidecar/bin/{name}",
    )

    def _fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("bio_harness.reporting.report_bundle.subprocess.run", _fake_run)

    report_dir = build_run_report_bundle(selected_dir, run_multiqc=True, render_quarto=True)
    tooling = json.loads((report_dir / "tooling_status.json").read_text(encoding="utf-8"))

    assert tooling["multiqc_available"] is True
    assert tooling["quarto_available"] is True
    assert calls[0][0] == "/sidecar/bin/multiqc"
    assert calls[1][0] == "/sidecar/bin/quarto"


def test_build_run_report_bundle_resolves_fake_pixi_sidecar_multiqc(monkeypatch, tmp_path: Path) -> None:
    selected_dir, _ = _make_fake_run(tmp_path)
    multiqc_bin = _make_executable(tmp_path / ".pixi" / "envs" / "reports" / "bin" / "multiqc")
    calls: list[list[str]] = []

    monkeypatch.setattr(tool_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: None)

    def _fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("bio_harness.reporting.report_bundle.subprocess.run", _fake_run)

    report_dir = build_run_report_bundle(selected_dir, run_multiqc=True, render_quarto=False)
    tooling = json.loads((report_dir / "tooling_status.json").read_text(encoding="utf-8"))

    assert tooling["multiqc_available"] is True
    assert calls[0][0] == str(multiqc_bin)


def test_build_run_report_bundle_supports_generic_artifact_directory(monkeypatch, tmp_path: Path) -> None:
    source_dir = tmp_path / "fastqc_outputs"
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "sample_fastqc.html").write_text("<html></html>\n", encoding="utf-8")
    (source_dir / "sample_fastqc.zip").write_text("zip-placeholder\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(
        "bio_harness.reporting.report_bundle.requirement_available",
        lambda name: name == "multiqc",
    )
    monkeypatch.setattr(
        "bio_harness.reporting.report_bundle.which_with_pixi",
        lambda name: f"/sidecar/bin/{name}",
    )

    def _fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("bio_harness.reporting.report_bundle.subprocess.run", _fake_run)

    report_dir = build_run_report_bundle(source_dir, source_dir, run_multiqc=True, render_quarto=False)
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    tooling = json.loads((report_dir / "tooling_status.json").read_text(encoding="utf-8"))

    assert report_dir == source_dir
    assert summary["status"] == "artifact_directory"
    assert summary["context_mode"] == "artifact_directory_only"
    assert {row["relative_to_selected_dir"] for row in summary["final_outputs"]} == {
        "sample_fastqc.html",
        "sample_fastqc.zip",
    }
    assert tooling["multiqc"]["attempted"] is True
    assert calls[0][0] == "/sidecar/bin/multiqc"
    assert calls[0][1] == str(source_dir)


def test_run_context_resolves_run_dir_input_via_manifest(tmp_path: Path) -> None:
    selected_dir, run_dir = _make_fake_run(tmp_path)

    context = resolve_run_context(run_dir)

    assert context.resolution_mode == "run_dir_inferred"
    assert context.selected_dir == selected_dir
    assert context.run_dir == run_dir
    assert context.result["status"] == "completed"


def test_build_run_report_bundle_supports_run_dir_input(tmp_path: Path) -> None:
    selected_dir, run_dir = _make_fake_run(tmp_path)

    report_dir = build_run_report_bundle(run_dir)
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    context_payload = json.loads((report_dir / "completed_run_context.json").read_text(encoding="utf-8"))

    assert summary["status"] == "completed"
    assert summary["context_mode"] == "run_dir_inferred"
    assert context_payload["resolution_mode"] == "run_dir_inferred"
    assert summary["final_output_count"] == 1
    assert {row["relative_to_selected_dir"] for row in summary["final_outputs"]} == {
        "final/results.csv",
    }
    assert context_payload["selected_dir"] == str(selected_dir)


def test_resolve_run_context_prefers_persisted_completed_run_context(tmp_path: Path) -> None:
    selected_dir, run_dir = _make_fake_run(tmp_path)
    state_payload = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    manifest_payload = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    persisted_payload = build_completed_run_context_payload(
        selected_dir=selected_dir,
        run_dir=run_dir,
        result=build_live_result_payload(
            run={
                "status": "completed",
                "planning_attempts": [{"attempt": 1}],
                "auto_repair_history": [],
                "input_quality": {"summary": "persisted"},
                "in_run_quality_summary": {"suspicious_event_count": 1},
            },
            selected_dir=selected_dir,
            run_dir=run_dir,
            benchmark_policy="bioagentbench_planning_strict",
            data_root=tmp_path / "data",
            analysis_type="rna_seq_differential_expression",
            result_path=selected_dir / "result.json",
        ),
        manifest=manifest_payload,
        state=state_payload,
        final_plan={"plan": [{"tool_name": "bash_run", "arguments": {}, "step_id": 1}]},
        result_path=selected_dir / "result.json",
        manifest_path=run_dir / "manifest.json",
        state_path=run_dir / "state.json",
        events_path=run_dir / "events.jsonl",
        execution_log_path=run_dir / "execution.log",
    )
    (run_dir / "completed_run_context.json").write_text(
        json.dumps(persisted_payload, indent=2),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"selected_dir": str(tmp_path / "wrong-selected")}, indent=2),
        encoding="utf-8",
    )

    context = resolve_run_context(run_dir)

    assert context.resolution_mode == "completed_run_context"
    assert context.selected_dir == selected_dir
    assert context.result["input_quality"]["summary"] == "persisted"


def test_run_context_prefers_run_dir_when_archived_manifest_points_to_workspace_root(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    run_dir = workspace_root / "runs" / "20260408_task_demo"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "selected_dir": str(workspace_root),
                "data_root": str(workspace_root / "data"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps({"status": "completed"}, indent=2),
        encoding="utf-8",
    )
    (run_dir / "gene_abundances.tsv").write_text(
        "gene,tpm\nA,1.0\n",
        encoding="utf-8",
    )

    context = resolve_run_context(run_dir)

    assert context.selected_dir == run_dir
    assert context.resolution_mode == "run_dir_inferred"


def test_build_run_report_bundle_writes_failure_diagnosis_for_failed_run(tmp_path: Path) -> None:
    selected_dir = _make_failed_run(tmp_path)

    report_dir = build_run_report_bundle(selected_dir)
    diagnosis = json.loads((report_dir / "failure_diagnosis.json").read_text(encoding="utf-8"))

    assert diagnosis["tool_name"] == "samtools_flagstat"
    assert (report_dir / "failure_diagnosis.md").exists()
