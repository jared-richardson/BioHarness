from __future__ import annotations

from pathlib import Path

from bio_harness.ui.execution_plan_normalization import (
    normalize_ui_run_plan_for_execution,
)


def test_normalize_ui_run_plan_for_execution_rebinds_stringtie_inspection_command(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "data"
    legacy_output_dir = tmp_path / "workspace" / "output"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    normalized, meta, _fc_meta = normalize_ui_run_plan_for_execution(
        plan={
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "stringtie_quant",
                    "arguments": {
                        "input_bam": "/tmp/sample.bam",
                        "annotation_gtf": "/tmp/refs/genes.gtf",
                        "output_gtf": str(legacy_output_dir / "assembled.gtf"),
                        "gene_abundance_tsv": str(legacy_output_dir / "gene_abundances.tsv"),
                    },
                },
                {
                    "step_id": 2,
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": f"head -n 20 {legacy_output_dir / 'ERR127302_chr14_gene_abundance.tsv'}",
                    },
                },
            ]
        },
        analysis_spec={
            "execution_contract": {
                "analysis_family": "transcript_quantification",
                "input_mode": "aligned_bam",
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            },
            "explicit_execution_intent": {
                "locked_tools": ["stringtie_quant"],
            },
        },
        plan_contract={},
        user_request=(
            "Proceed with execution now. Run stringtie_quant on /tmp/sample.bam "
            "with annotation /tmp/refs/genes.gtf, then inspect the gene "
            "abundance table and explain what it contains."
        ),
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        benchmark_policy="scientific_harness",
    )

    step1_args = normalized["plan"][0]["arguments"]
    step2_command = normalized["plan"][1]["arguments"]["command"]
    assert step1_args["output_gtf"] == str(selected_dir / "assembled.gtf")
    assert step1_args["gene_abundance_tsv"] == str(selected_dir / "gene_abundances.tsv")
    assert step2_command == f"head -n 20 {selected_dir / 'gene_abundances.tsv'}"
    assert "direct_wrapper_inspection_bash_run_repairs" in meta


def test_normalize_ui_run_plan_for_execution_preserves_report_bundle_run_input(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "data"
    existing_run_dir = tmp_path / "external" / "completed_fastqc_run"
    existing_run_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    normalized, _meta, _fc_meta = normalize_ui_run_plan_for_execution(
        plan={
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "multiqc_report",
                    "arguments": {
                        "run_input": str(existing_run_dir),
                        "output_dir": str(existing_run_dir),
                    },
                }
            ]
        },
        analysis_spec={
            "analysis_type": "general",
            "chosen_method": "multiqc_report",
            "execution_contract": {
                "analysis_family": "general",
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["multiqc_report"],
            },
            "explicit_execution_intent": {
                "locked_tools": ["multiqc_report"],
            },
        },
        plan_contract={
            "explicit_tool_hints": ["multiqc", "fastqc"],
            "must_include_capabilities": ["run_reporting"],
        },
        user_request=(
            "Proceed with execution now. Build a MultiQC report bundle from the completed FastQC outputs "
            f"in {existing_run_dir} and keep all generated files in the current run directory."
        ),
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        benchmark_policy="scientific_harness",
    )

    step_args = normalized["plan"][0]["arguments"]
    assert step_args["run_input"] == str(existing_run_dir)
    assert step_args["output_dir"] == str(selected_dir / existing_run_dir.name)


def test_normalize_ui_run_plan_for_execution_preserves_report_bundle_run_input_for_context_expanded_request(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "run"
    data_root = tmp_path / "completed_fastqc_run"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    normalized, meta, _fc_meta = normalize_ui_run_plan_for_execution(
        plan={
            "plan": [
                {
                    "step_id": 1,
                    "tool_name": "multiqc_report",
                    "arguments": {
                        "run_input": str(data_root),
                        "output_dir": str(data_root),
                    },
                }
            ]
        },
        analysis_spec={
            "analysis_type": "viral_metagenomics",
            "chosen_method": "prokka_annotate",
            "selected_dir": str(tmp_path),
            "execution_contract": {
                "analysis_family": "viral_metagenomics",
                "execution_mode": "compiled_pipeline",
                "compatible_tools": [],
            },
        },
        plan_contract={
            "explicit_tool_hints": ["fastqc", "multiqc"],
            "must_include_capabilities": ["fastqc", "run_reporting"],
        },
        user_request=(
            "Recent conversation context:\n"
            "user: Proceed with execution now. Build a MultiQC report bundle from the completed FastQC outputs "
            f"in {data_root} and keep all generated files in the current run directory.\n"
            "assistant: Execution requested. Starting now.\n"
            f"- Data root: `{data_root}`\n"
            "- FASTQ detected: `0` (preferred_latest_user_message_path)\n\n"
            "Latest user instruction:\n"
            "Proceed with execution now. Build a MultiQC report bundle from the completed FastQC outputs "
            f"in {data_root} and keep all generated files in the current run directory."
        ),
        selected_dir=str(selected_dir),
        data_root=str(data_root),
        benchmark_policy="scientific_harness",
    )

    step_args = normalized["plan"][0]["arguments"]
    assert step_args["run_input"] == str(data_root)
    assert step_args["output_dir"] == str(selected_dir / data_root.name)
    assert meta["artifact_role_repairs_after_output_redirect"]["restored"] == ["multiqc_report.run_input"]
