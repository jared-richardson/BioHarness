from __future__ import annotations

from pathlib import Path

from bio_harness.harness.plan_repair_analysis_workflows import (
    _repair_direct_wrapper_inspection_bash_run,
    _looks_like_inline_multi_model_compare_pathways_command,
    _repair_direct_wrapper_helper_bash_run,
    _repair_variant_annotation_impact_filter,
)


def test_inline_multi_model_compare_pathways_detector_flags_embedded_science_command() -> None:
    command = (
        "python - <<'PY'\n"
        "import pandas as pd\n"
        "from scipy.stats import fisher_exact, ttest_ind\n"
        "dea_ps3o1s = pd.read_csv('dea_ps3o1s.csv')\n"
        "gse161904 = pd.read_table('GSE161904_counts.txt')\n"
        "gse168137 = pd.read_table('GSE168137_counts.txt')\n"
        "pathway_comparison_csv = 'pathway_comparison.csv'\n"
        "print(ttest_ind([1], [2]))\n"
        "PY"
    )

    assert _looks_like_inline_multi_model_compare_pathways_command(command) is True


def test_repair_variant_annotation_impact_filter_rewrites_bcftools_filter_command() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "snpeff_annotate",
                "arguments": {
                    "output_vcf": "/tmp/workspace/final/annotated.vcf",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": "bcftools filter -i 'INFO/IMPACT=\"HIGH\"' -o /tmp/workspace/final/high.vcf /tmp/workspace/final/annotated.vcf",
                },
            },
        ]
    }

    repaired, meta = _repair_variant_annotation_impact_filter(raw_plan)

    assert meta["changed"] is True
    command = repaired["plan"][1]["arguments"]["command"]
    assert "SnpSift filter" in command
    assert "/tmp/workspace/final/annotated.vcf" in command
    assert "/tmp/workspace/final/high.vcf" in command


def test_repair_direct_wrapper_helper_bash_run_drops_path_scaffold_step() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {"command": "mkdir -p /tmp/run/stringtie"},
            },
            {
                "step_id": 2,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": "/tmp/run/stringtie/assembled.gtf",
                },
            },
        ]
    }

    repaired, meta = _repair_direct_wrapper_helper_bash_run(
        raw_plan,
        selected_dir=Path("/tmp/run"),
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            }
        },
    )

    assert meta["changed"] is True
    assert [step["tool_name"] for step in repaired["plan"]] == ["stringtie_quant"]


def test_repair_direct_wrapper_helper_bash_run_drops_deseq_deliverable_move() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/counts.tsv",
                    "metadata_table": "/tmp/meta.tsv",
                    "design_formula": "~ dex",
                    "contrast": "dex_trt_vs_untrt",
                    "output_dir": "/tmp/run/my_analysis/de_intermediate",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": "mv /tmp/run/my_analysis/de_intermediate/deseq2_results.tsv /tmp/run/my_analysis/final_result.csv",
                },
            },
        ]
    }

    repaired, meta = _repair_direct_wrapper_helper_bash_run(
        raw_plan,
        selected_dir=Path("/tmp/run"),
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["deseq2_run"],
            }
        },
    )

    assert meta["changed"] is True
    assert [step["tool_name"] for step in repaired["plan"]] == ["deseq2_run"]


def test_repair_direct_wrapper_helper_bash_run_drops_mkdir_and_copy_combo() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "arguments": {
                    "counts_matrix": "/tmp/counts.tsv",
                    "metadata_table": "/tmp/meta.tsv",
                    "design_formula": "~ dex",
                    "contrast": "dex_trt_vs_untrt",
                    "output_dir": "/tmp/run/deseq_results",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "mkdir -p /tmp/run/final && "
                        "cp /tmp/run/deseq_results/deseq2_results.tsv /tmp/run/final/deseq_results.csv"
                    ),
                },
            },
        ]
    }

    repaired, meta = _repair_direct_wrapper_helper_bash_run(
        raw_plan,
        selected_dir=Path("/tmp/run"),
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["deseq2_run"],
            }
        },
    )

    assert meta["changed"] is True
    assert [step["tool_name"] for step in repaired["plan"]] == ["deseq2_run"]


def test_repair_direct_wrapper_helper_bash_run_drops_selected_dir_mkdir_with_date_typo() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "mkdir -p "
                        "/tmp/extended_suite_ablation/overnight_full_gemma26_20250406_224101/"
                        "gemma26_full/hnrnpc_stringtie_not_salmon/stringtie"
                    ),
                },
            },
            {
                "step_id": 2,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": (
                        "/tmp/extended_suite_ablation/overnight_full_gemma26_20260406_224101/"
                        "gemma26_full/hnrnpc_stringtie_not_salmon/stringtie/assembled.gtf"
                    ),
                },
            },
        ]
    }

    repaired, meta = _repair_direct_wrapper_helper_bash_run(
        raw_plan,
        selected_dir=Path(
            "/tmp/extended_suite_ablation/overnight_full_gemma26_20260406_224101/"
            "gemma26_full/hnrnpc_stringtie_not_salmon"
        ),
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            }
        },
    )

    assert meta["changed"] is True
    assert [step["tool_name"] for step in repaired["plan"]] == ["stringtie_quant"]


def test_repair_direct_wrapper_inspection_bash_run_rebinds_stringtie_preview() -> None:
    selected_dir = Path("/tmp/run")
    legacy_output_dir = Path("/tmp/workspace/output")
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": str(selected_dir / "assembled.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "gene_abundances.tsv"),
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
    }

    repaired, meta = _repair_direct_wrapper_inspection_bash_run(
        raw_plan,
        selected_dir=selected_dir,
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            }
        },
        request_text="Inspect the gene abundance table and explain what it contains.",
    )

    assert meta["changed"] is True
    command = repaired["plan"][1]["arguments"]["command"]
    assert command == f"head -n 20 {selected_dir.resolve(strict=False) / 'gene_abundances.tsv'}"


def test_repair_direct_wrapper_inspection_bash_run_ignores_non_preview_commands() -> None:
    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": "/tmp/genes.gtf",
                    "output_gtf": "/tmp/run/assembled.gtf",
                    "gene_abundance_tsv": "/tmp/run/gene_abundances.tsv",
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": "python summarize.py /tmp/run/ERR127302_chr14_gene_abundance.tsv",
                },
            },
        ]
    }

    repaired, meta = _repair_direct_wrapper_inspection_bash_run(
        raw_plan,
        selected_dir=Path("/tmp/run"),
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            }
        },
        request_text="Inspect the gene abundance table and explain what it contains.",
    )

    assert meta["changed"] is False
    assert repaired == raw_plan


def test_repair_direct_wrapper_inspection_bash_run_preserves_existing_external_file(
    tmp_path: Path,
) -> None:
    selected_dir = tmp_path / "run"
    annotation_path = tmp_path / "references" / "existing_annotation.gtf"
    selected_dir.mkdir(parents=True, exist_ok=True)
    annotation_path.parent.mkdir(parents=True, exist_ok=True)
    annotation_path.write_text("existing\n", encoding="utf-8")

    raw_plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "stringtie_quant",
                "arguments": {
                    "input_bam": "/tmp/sample.bam",
                    "annotation_gtf": str(annotation_path),
                    "output_gtf": str(selected_dir / "assembled.gtf"),
                    "gene_abundance_tsv": str(selected_dir / "gene_abundances.tsv"),
                },
            },
            {
                "step_id": 2,
                "tool_name": "bash_run",
                "arguments": {
                    "command": f"head -n 5 {annotation_path}",
                },
            },
        ]
    }

    repaired, meta = _repair_direct_wrapper_inspection_bash_run(
        raw_plan,
        selected_dir=selected_dir,
        analysis_spec={
            "execution_contract": {
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["stringtie_quant"],
            }
        },
        request_text="Preview the annotation gtf file after planning.",
    )

    assert meta["changed"] is False
    assert repaired == raw_plan
