"""Tests for post-run result interpretation."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.result_interpreter import (
    _build_interpretation_prompt,
    _collect_run_metrics,
    _template_based_summary,
    interpret_run_results,
)


def test_collect_metrics_de_run(tmp_path: Path) -> None:
    final_dir = tmp_path / "final"
    final_dir.mkdir()
    results = final_dir / "deseq_results.csv"
    results.write_text("gene,log2FoldChange,padj\nA,2.0,0.001\nB,-1.0,0.200\n")
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "expected_files": ["final/deseq_results.csv"],
                "deliverables": ["deseq_results.csv"],
            }
        ],
        "final_deliverables": ["deseq_results.csv"],
    }

    metrics = _collect_run_metrics(
        tmp_path,
        plan,
        analysis_type="rna_seq_differential_expression",
    )

    assert metrics["total_steps"] == 1
    assert metrics["key_outputs"]["de_results"]["metrics"]["significant_row_count"] == 1.0


def test_collect_metrics_vc_run(tmp_path: Path) -> None:
    variants = tmp_path / "variants.vcf"
    variants.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "bcftools_call",
                "expected_files": ["variants.vcf"],
                "deliverables": ["variants.vcf"],
            }
        ],
        "final_deliverables": ["variants.vcf"],
    }

    metrics = _collect_run_metrics(
        tmp_path,
        plan,
        analysis_type="variant_calling",
    )

    assert metrics["key_outputs"]["variant_calling"]["metrics"]["variant_count"] == 1.0


def test_collect_metrics_empty_dir(tmp_path: Path) -> None:
    metrics = _collect_run_metrics(
        tmp_path,
        {"plan": [], "final_deliverables": []},
        analysis_type="alignment",
    )

    assert metrics["total_steps"] == 0
    assert metrics["key_outputs"] == {}


def test_collect_metrics_handles_missing_files(tmp_path: Path) -> None:
    plan = {
        "plan": [
            {
                "step_id": 1,
                "tool_name": "deseq2_run",
                "expected_files": ["final/missing.csv"],
                "deliverables": ["missing.csv"],
            }
        ],
        "final_deliverables": ["missing.csv"],
    }

    metrics = _collect_run_metrics(
        tmp_path,
        plan,
        analysis_type="rna_seq_differential_expression",
    )

    assert metrics["key_outputs"] == {}


def test_template_summary_de() -> None:
    metrics = {
        "completed_steps": 3,
        "key_outputs": {
            "de_results": {
                "metrics": {"row_count": 100.0, "significant_row_count": 12.0},
            }
        },
        "concerns": (),
    }

    summary = _template_based_summary("rna_seq_differential_expression", metrics)

    assert "differentially expressed genes" in summary


def test_template_summary_vc() -> None:
    metrics = {
        "completed_steps": 2,
        "key_outputs": {
            "variant_calling": {
                "metrics": {"variant_count": 42.0, "pass_fraction": 0.9},
            }
        },
        "concerns": (),
    }

    summary = _template_based_summary("variant_calling", metrics)

    assert "variants" in summary


def test_template_summary_alignment() -> None:
    metrics = {
        "completed_steps": 2,
        "key_outputs": {
            "alignment": {
                "metrics": {"mapping_rate": 0.94, "total_reads": 1000.0},
            }
        },
        "concerns": (),
    }

    summary = _template_based_summary("alignment", metrics)

    assert "mapping" in summary.lower()


def test_template_summary_includes_concerns() -> None:
    metrics = {
        "completed_steps": 1,
        "key_outputs": {},
        "concerns": ("Mapping rate is 4.0%.",),
    }

    summary = _template_based_summary("alignment", metrics)

    assert "Concerns:" in summary


def test_build_prompt_includes_analysis_type() -> None:
    prompt = _build_interpretation_prompt("rna_seq_differential_expression", {"key_outputs": {}})

    assert "rna_seq_differential_expression" in prompt


def test_build_prompt_includes_metrics() -> None:
    prompt = _build_interpretation_prompt(
        "variant_calling",
        {"key_outputs": {"variant_calling": {"metrics": {"variant_count": 12.0}}}},
    )

    assert "variant_calling" in prompt


def test_build_prompt_reasonable_length() -> None:
    prompt = _build_interpretation_prompt("analysis", {"key_outputs": {"a": {}, "b": {}, "c": {}}})

    assert len(prompt) < 2000


class _MockLLM:
    model_name = "mock-fast-model"

    def summarize_text(self, text: str, instruction: str) -> str:
        return f"Mock summary based on {len(text)} chars."


def test_interpret_with_mock_llm(tmp_path: Path) -> None:
    results = tmp_path / "variants.vcf"
    results.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )
    plan = {
        "plan": [{"step_id": 1, "tool_name": "bcftools_call", "expected_files": ["variants.vcf"], "deliverables": []}],
        "final_deliverables": [],
    }

    result = interpret_run_results(tmp_path, "variant_calling", plan, llm=_MockLLM())

    assert result.model_used == "mock-fast-model"
    assert result.interpretation.startswith("Mock summary")


def test_interpret_without_llm(tmp_path: Path) -> None:
    results = tmp_path / "variants.vcf"
    results.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )
    plan = {
        "plan": [{"step_id": 1, "tool_name": "bcftools_call", "expected_files": ["variants.vcf"], "deliverables": []}],
        "final_deliverables": [],
    }

    result = interpret_run_results(tmp_path, "variant_calling", plan, llm=None)

    assert result.model_used == "template"
    assert "variants" in result.interpretation


def test_interpret_de_artifact_summary_includes_top_gene(tmp_path: Path) -> None:
    results = tmp_path / "deseq_results.csv"
    results.write_text(
        "gene,baseMean,log2FoldChange,pvalue,padj\n"
        "BRCA1,1000,4.2,1e-10,1e-8\n"
        "TP53,900,-3.1,1e-9,1e-7\n"
        "GENE3,500,0.5,0.4,0.6\n"
    )

    result = interpret_run_results(tmp_path, "rna_seq_differential_expression", {"plan": [], "final_deliverables": []}, llm=None)

    assert "BRCA1" in result.interpretation
    assert "2 significant genes" in result.interpretation
    assert "upregulated" in result.interpretation


def test_interpret_single_cell_artifact_summary_uses_markers(tmp_path: Path) -> None:
    (tmp_path / "clusters.csv").write_text(
        "cell,cluster\n"
        "cell1,0\n"
        "cell2,0\n"
        "cell3,1\n"
        "cell4,1\n"
        "cell5,2\n"
        "cell6,2\n"
    )
    (tmp_path / "markers.csv").write_text(
        "gene,cluster,pval_adj,log2fc\n"
        "CD3D,0,0.001,3.2\n"
        "CD19,1,0.001,4.1\n"
        "CD14,2,0.001,3.8\n"
    )

    result = interpret_run_results(tmp_path, "single_cell", {"plan": [], "final_deliverables": []}, llm=None)

    assert "6 cells" in result.interpretation
    assert "3 clusters" in result.interpretation
    assert "CD3D" in result.interpretation


def test_interpret_flagstat_content_without_flagstat_filename(tmp_path: Path) -> None:
    (tmp_path / "alignment_stats.txt").write_text(
        "50000 + 0 in total (QC-passed reads + QC-failed reads)\n"
        "1500 + 0 duplicates\n"
        "47500 + 0 mapped (95.00% : N/A)\n"
        "48000 + 0 paired in sequencing\n"
    )

    result = interpret_run_results(tmp_path, "alignment", {"plan": [], "final_deliverables": []}, llm=None)

    assert "50,000" in result.interpretation
    assert "95.00%" in result.interpretation
    assert "duplicates" in result.interpretation
