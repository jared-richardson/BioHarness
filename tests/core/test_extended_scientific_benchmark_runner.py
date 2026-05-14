from __future__ import annotations

import json
from pathlib import Path

from scripts.run_extended_scientific_benchmark_suite import (
    DEFAULT_SUITE_PATH,
    ColumnCheck,
    SuiteCase,
    _load_suite_cases,
    _evaluate_case,
    _extract_tool_names,
    _read_header_columns,
    _render_case_text,
)


def test_render_case_text_substitutes_standard_placeholders(tmp_path: Path) -> None:
    selected_dir = tmp_path / "case"
    data_root = tmp_path / "data"
    rendered = _render_case_text(
        "Prompt uses {project_root}, {selected_dir}, and {data_root}.",
        selected_dir=selected_dir,
        data_root=data_root,
    )

    assert str(selected_dir) in rendered
    assert str(data_root) in rendered
    assert "<BIO_HARNESS_ROOT>" in rendered


def test_extract_tool_names_prefers_exec_file_patterns(tmp_path: Path) -> None:
    exec_file = tmp_path / "execution.log"
    exec_file.write_text(
        "--- Executing Step 1: scanpy_workflow ---\n"
        "--- Executing Step 2: deseq2_run ---\n"
        "--- Executing Step 3: deseq2_run ---\n",
        encoding="utf-8",
    )

    result_obj = {"exec_file": str(exec_file)}
    assert _extract_tool_names(result_obj, tmp_path / "missing.log") == ["scanpy_workflow", "deseq2_run"]


def test_read_header_columns_detects_delimiter_from_path_suffix(tmp_path: Path) -> None:
    tsv_path = tmp_path / "counts.tsv"
    tsv_path.write_text("gene_id\tlog2FoldChange\tpvalue\tpadj\n", encoding="utf-8")

    assert _read_header_columns(tsv_path, "") == ["gene_id", "log2FoldChange", "pvalue", "padj"]


def test_evaluate_case_marks_pass_when_artifacts_and_tools_match(tmp_path: Path) -> None:
    selected_dir = tmp_path / "case"
    selected_dir.mkdir()
    (selected_dir / "final").mkdir()
    (selected_dir / "final" / "deseq_results.csv").write_text(
        "gene_id,log2FoldChange,pvalue,padj\nA,1.0,0.01,0.02\n",
        encoding="utf-8",
    )

    exec_file = selected_dir / "execution.log"
    exec_file.write_text("--- Executing Step 1: deseq2_run ---\n", encoding="utf-8")
    result_json = selected_dir / "result.json"
    result_obj = {
        "status": "completed",
        "benchmark_policy": "scientific_harness",
        "exec_file": str(exec_file),
        "generic_template_fallback_used": False,
        "protocol_template_fallback_used": False,
        "run_dir": str(selected_dir / "run"),
    }
    result_json.write_text(json.dumps(result_obj), encoding="utf-8")

    case = SuiteCase(
        case_id="airway_deseq_explicit",
        lane="deseq_prompt_grounding",
        description="desc",
        prompt="prompt",
        data_root="workspace/non_bioagent_real_data/airway",
        benchmark_policy="scientific_harness",
        expected_artifacts=("final/deseq_results.csv",),
        required_tools=("deseq2_run",),
        forbidden_tools=("subread_align",),
        column_checks=(
            ColumnCheck(
                path="final/deseq_results.csv",
                columns=("gene_id", "log2FoldChange", "pvalue", "padj"),
            ),
        ),
    )

    row = _evaluate_case(
        case=case,
        result_obj=result_obj,
        selected_dir=selected_dir,
        result_json=result_json,
        log_path=selected_dir / "harness.log",
        harness_exit_code=0,
        timed_out=False,
    )

    assert row.passed is True
    assert row.actual_tools == ["deseq2_run"]
    assert row.reasons == []


def test_evaluate_case_reports_missing_artifact_and_forbidden_tool(tmp_path: Path) -> None:
    selected_dir = tmp_path / "case"
    selected_dir.mkdir()
    exec_file = selected_dir / "execution.log"
    exec_file.write_text(
        "--- Executing Step 1: deseq2_run ---\n--- Executing Step 2: subread_align ---\n",
        encoding="utf-8",
    )

    result_obj = {
        "status": "completed",
        "benchmark_policy": "scientific_harness",
        "exec_file": str(exec_file),
        "generic_template_fallback_used": False,
        "protocol_template_fallback_used": False,
        "run_dir": str(selected_dir / "run"),
    }

    case = SuiteCase(
        case_id="airway_deseq_noisy",
        lane="deseq_prompt_grounding",
        description="desc",
        prompt="prompt",
        data_root="workspace/non_bioagent_real_data/airway",
        benchmark_policy="scientific_harness",
        expected_artifacts=("final/deseq_results.csv",),
        required_tools=("deseq2_run",),
        forbidden_tools=("subread_align",),
    )

    row = _evaluate_case(
        case=case,
        result_obj=result_obj,
        selected_dir=selected_dir,
        result_json=selected_dir / "result.json",
        log_path=selected_dir / "harness.log",
        harness_exit_code=0,
        timed_out=False,
    )

    assert row.passed is False
    assert row.missing_artifacts == ["final/deseq_results.csv"]
    assert row.forbidden_tools_detected == ["subread_align"]
    assert "missing_artifacts=final/deseq_results.csv" in row.reasons
    assert "forbidden_tools=subread_align" in row.reasons


def test_default_suite_manifest_includes_expanded_lanes() -> None:
    cases = _load_suite_cases(DEFAULT_SUITE_PATH)

    case_ids = {case.case_id for case in cases}
    lanes = {case.lane for case in cases}

    assert "pbmc3k_scanpy_misleading_de" in case_ids
    assert "airway_edger_not_deseq" in case_ids
    assert "hnrnpc_stringtie_deep_path" in case_ids
    assert "scanpy_adversarial" in lanes
    assert "cross_tool_contamination" in lanes
    assert "output_path_fidelity" in lanes
    assert len(cases) >= 30
