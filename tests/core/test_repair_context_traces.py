from __future__ import annotations

import json
from pathlib import Path

import pytest

from bio_harness.harness.repair_context import build_repair_context


@pytest.fixture()
def mock_run_with_step(tmp_path: Path) -> tuple[dict[str, object], Path]:
    selected = tmp_path / "outputs"
    selected.mkdir()

    step0 = selected / "step_0"
    step0.mkdir()
    manifest = {
        "tool_name": "bwa_mem_align",
        "success": True,
        "exit_code": 0,
        "outputs": ["out.bam"],
    }
    (step0 / ".step_completion.json").write_text(json.dumps(manifest), encoding="utf-8")

    step1 = selected / "step_1"
    step1.mkdir()
    (step1 / "stderr.log").write_text("Error: reference genome not found at /data/ref.fa\n", encoding="utf-8")
    (step1 / "stdout.log").write_text("Processing step 1...\nLoading index...\n", encoding="utf-8")

    input_dir = selected / "inputs"
    input_dir.mkdir()
    (input_dir / "out.bam").write_text("bam", encoding="utf-8")
    (input_dir / "out.bam.bai").write_text("index", encoding="utf-8")

    run = {
        "current_step_index": 1,
        "final_plan": {
            "plan": [
                {"tool_name": "bwa_mem_align", "arguments": {"reference_fasta": "/data/ref.fa"}},
                {"tool_name": "freebayes_call", "arguments": {"input_bam": str(input_dir / "out.bam")}},
            ]
        },
        "stderr_tail": [],
        "stdout_tail": [],
    }
    return run, selected


def test_diagnostic_traces_present(mock_run_with_step: tuple[dict[str, object], Path]) -> None:
    run, selected = mock_run_with_step
    ctx = build_repair_context(
        run=run,
        selected_dir=selected,
        failure_class="missing_reference",
        reason="Reference genome not found",
    )

    traces = ctx["diagnostic_traces"]
    assert "reference genome not found" in traces["stderr"]
    assert "Processing step 1" in traces["stdout"]
    assert "freebayes_call" in traces["executed_command"]
    assert "out.bam" in traces["input_file_listing"]


def test_prev_step_completion_included(mock_run_with_step: tuple[dict[str, object], Path]) -> None:
    run, selected = mock_run_with_step
    ctx = build_repair_context(
        run=run,
        selected_dir=selected,
        failure_class="missing_reference",
        reason="Reference genome not found",
    )

    prev = ctx["diagnostic_traces"]["prev_step_completion"]
    assert prev.get("tool_name") == "bwa_mem_align"
    assert prev.get("success") is True


def test_diagnostic_traces_empty_when_no_step(tmp_path: Path) -> None:
    ctx = build_repair_context(
        run={"current_step_index": -1},
        selected_dir=tmp_path,
        failure_class="unknown_failure",
        reason="Unknown",
    )

    traces = ctx["diagnostic_traces"]
    assert traces["stderr"] == ""
    assert traces["stdout"] == ""
    assert traces["executed_command"] == ""
    assert traces["prev_step_completion"] == {}


def test_stderr_from_run_tail_fallback(tmp_path: Path) -> None:
    run = {
        "current_step_index": 0,
        "final_plan": {"plan": [{"tool_name": "fastqc_run", "arguments": {}}]},
        "stderr_tail": ["WARN: low quality", "ERROR: truncated file"],
        "stdout_tail": [],
    }
    ctx = build_repair_context(
        run=run,
        selected_dir=tmp_path,
        failure_class="format_input_error",
        reason="Bad input",
    )

    assert "truncated file" in ctx["diagnostic_traces"]["stderr"]


def test_first_step_logs_are_loaded_when_current_step_index_is_zero(tmp_path: Path) -> None:
    selected = tmp_path / "outputs"
    step0 = selected / "step_0"
    step0.mkdir(parents=True)
    (step0 / "stderr.log").write_text("ERROR_MISSING_DATABASE: snpEff db missing\n", encoding="utf-8")
    (step0 / "stdout.log").write_text("snpEff ann Escherichia_coli_K12 input.vcf\n", encoding="utf-8")

    run = {
        "current_step_index": 0,
        "final_plan": {"plan": [{"tool_name": "snpeff_annotate", "arguments": {}}]},
        "stderr_tail": [],
        "stdout_tail": [],
    }

    ctx = build_repair_context(
        run=run,
        selected_dir=selected,
        failure_class="runtime_step_failure",
        reason="Database missing",
    )

    assert "ERROR_MISSING_DATABASE" in ctx["diagnostic_traces"]["stderr"]
    assert "Escherichia_coli_K12" in ctx["diagnostic_traces"]["stdout"]
    assert "snpeff_annotate" in ctx["diagnostic_traces"]["executed_command"]


def test_prior_repair_history_injected(tmp_path: Path) -> None:
    run = {
        "current_step_index": 0,
        "final_plan": {"plan": [{"tool_name": "star_align", "arguments": {}}]},
        "stderr_tail": [],
        "stdout_tail": [],
        "auto_repair_history": [
            {
                "ts": "2026-04-01T10:00:00",
                "run_id": "run-1",
                "failure_class": "tool_missing",
                "attempt": 1,
                "action": "substitute_tool",
                "details": {"from": "STAR", "to": "hisat2"},
            },
            {
                "ts": "2026-04-01T10:05:00",
                "run_id": "run-1",
                "failure_class": "runtime_step_failure",
                "attempt": 2,
                "action": "replan_step",
                "details": {"step": 0, "reason": "hisat2 also failed"},
            },
        ],
    }

    ctx = build_repair_context(
        run=run,
        selected_dir=tmp_path,
        failure_class="runtime_step_failure",
        reason="Alignment failed",
    )

    history = ctx["prior_repair_attempts"]
    assert history["count"] == 2
    assert history["attempts"][0]["action"] == "substitute_tool"
    assert "Do NOT repeat" in history["instruction"]


def test_no_prior_history_when_empty(tmp_path: Path) -> None:
    run = {
        "current_step_index": 0,
        "final_plan": {"plan": []},
        "stderr_tail": [],
        "stdout_tail": [],
        "auto_repair_history": [],
    }

    ctx = build_repair_context(
        run=run,
        selected_dir=tmp_path,
        failure_class="unknown_failure",
        reason="Unknown",
    )

    assert "prior_repair_attempts" not in ctx


def test_diagnostic_traces_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    step0 = tmp_path / "step_0"
    step0.mkdir(parents=True)
    (step0 / "stderr.log").write_text("ERROR: something failed\n", encoding="utf-8")
    run = {
        "current_step_index": 0,
        "final_plan": {"plan": [{"tool_name": "fastqc_run", "arguments": {}}]},
        "stderr_tail": [],
        "stdout_tail": [],
    }
    monkeypatch.setenv("BIO_HARNESS_DIAGNOSTIC_TRACES", "0")

    ctx = build_repair_context(
        run=run,
        selected_dir=tmp_path,
        failure_class="runtime_step_failure",
        reason="Failed",
    )

    assert ctx["diagnostic_traces"] == {}


def test_build_repair_context_surfaces_artifact_role_issues(tmp_path: Path) -> None:
    run = {
        "current_step_index": 0,
        "final_plan": {"plan": [{"tool_name": "freebayes_call", "arguments": {}}]},
        "stderr_tail": [],
        "stdout_tail": [],
    }

    ctx = build_repair_context(
        run=run,
        selected_dir=tmp_path,
        failure_class="contract_mismatch",
        reason="Planner output failed contract validation",
        validation={
            "missing_capabilities": ["annotation"],
            "direct_wrapper_issues": ["incomplete_direct_wrapper:freebayes_call:reference_fasta"],
            "artifact_role_issues": ["bash_run.command:input_in_selected_dir_without_producer:/tmp/ancestor_raw.vcf"],
        },
    )

    assert ctx["contract_summary"]["artifact_role_issues"] == [
        "bash_run.command:input_in_selected_dir_without_producer:/tmp/ancestor_raw.vcf"
    ]
    assert ctx["contract_summary"]["direct_wrapper_issues"] == [
        "incomplete_direct_wrapper:freebayes_call:reference_fasta"
    ]
    assert ctx["validation_summary"]["artifact_role_issues"] == [
        "bash_run.command:input_in_selected_dir_without_producer:/tmp/ancestor_raw.vcf"
    ]


def test_nonmarkovian_repair_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run = {
        "current_step_index": 0,
        "final_plan": {"plan": [{"tool_name": "star_align", "arguments": {}}]},
        "stderr_tail": [],
        "stdout_tail": [],
        "auto_repair_history": [{"action": "substitute_tool"}],
    }
    monkeypatch.setenv("BIO_HARNESS_NONMARKOVIAN_REPAIR", "0")

    ctx = build_repair_context(
        run=run,
        selected_dir=tmp_path,
        failure_class="runtime_step_failure",
        reason="Failed",
    )

    assert "prior_repair_attempts" not in ctx
