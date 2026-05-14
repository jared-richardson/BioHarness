"""Tests for deterministic post-run result review."""

from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.result_review import result_review_to_markdown, review_run_results


def test_review_run_results_accepts_clean_de_output(tmp_path: Path) -> None:
    """Clean DE outputs should produce an accept recommendation."""

    results = tmp_path / "deseq_results.csv"
    results.write_text(
        (
            "gene,log2FoldChange,pvalue,padj\n"
            "GENE1,2.0,0.001,0.01\n"
            "GENE2,-1.5,0.002,0.02\n"
            "GENE3,0.1,0.4,0.6\n"
        ),
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "differential_expression",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept"
    assert review.quality_reports[0].path == str(results)
    assert "differential expression" in review.interpretation.interpretation.lower()


def test_review_run_results_warns_when_de_output_has_no_significant_rows(tmp_path: Path) -> None:
    """Warning-level DE outputs should produce an accept-with-warning decision."""

    results = tmp_path / "deseq_results.csv"
    results.write_text(
        (
            "gene,log2FoldChange,pvalue,padj\n"
            "GENE1,0.2,0.4,0.6\n"
            "GENE2,-0.1,0.5,0.7\n"
        ),
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "differential_expression",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept_with_warning"
    assert "no_significant_genes" in review.decision.warning_metric_names


def test_review_run_results_escalates_when_only_unsupported_outputs_exist(tmp_path: Path) -> None:
    """Unsupported outputs alone should not be auto-accepted."""

    html_report = tmp_path / "multiqc_report.html"
    html_report.write_text("<html><body>report</body></html>\n", encoding="utf-8")

    review = review_run_results(
        tmp_path,
        "",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "escalate_to_researcher"
    assert review.quality_reports[0].overall_level.value == "skip"
    markdown = result_review_to_markdown(review, selected_dir=tmp_path)
    assert "multiqc_report.html" in markdown
    assert "`escalate_to_researcher`" in markdown


def test_result_review_json_is_json_serializable(tmp_path: Path) -> None:
    """Serialized review payloads should remain JSON-friendly."""

    results = tmp_path / "variants.vcf"
    results.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t40\tPASS\t.\n",
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "variant_calling",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    payload = json.dumps(
        {
            "analysis_type": review.analysis_type,
            "decision": review.decision.decision,
            "report_count": len(review.quality_reports),
        }
    )

    assert "variant_calling" in payload


def test_review_run_results_accepts_single_cell_marker_outputs(tmp_path: Path) -> None:
    """Single-cell marker summaries should be accepted when structurally sound."""

    (tmp_path / "clusters.csv").write_text(
        "cell_id,cluster\ncell1,0\ncell2,0\ncell3,1\ncell4,1\n",
        encoding="utf-8",
    )
    (tmp_path / "markers.csv").write_text(
        "gene,cluster,pval_adj,log2fc\nCD3D,0,0.001,3.2\nCD79A,1,0.002,2.9\n",
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "single_cell",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept"


def test_review_run_results_escalates_on_implausible_single_cell_fragmentation(tmp_path: Path) -> None:
    """Cross-artifact single-cell fragmentation should escalate review."""

    (tmp_path / "clusters.csv").write_text(
        (
            "cell_id,cluster\n"
            "cell1,0\n"
            "cell2,1\n"
            "cell3,2\n"
            "cell4,3\n"
            "cell5,4\n"
            "cell6,5\n"
            "cell7,6\n"
            "cell8,7\n"
            "cell9,8\n"
            "cell10,9\n"
            "cell11,10\n"
            "cell12,11\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "markers.csv").write_text(
        (
            "gene,cluster,pval_adj,log2fc\n"
            "G1,0,0.001,3.2\n"
            "G2,1,0.001,3.1\n"
            "G3,2,0.001,3.0\n"
            "G4,3,0.001,2.9\n"
            "G5,4,0.001,2.8\n"
            "G6,5,0.001,2.7\n"
            "G7,6,0.001,2.6\n"
            "G8,7,0.001,2.5\n"
            "G9,8,0.001,2.4\n"
            "G10,9,0.001,2.3\n"
            "G11,10,0.001,2.2\n"
            "G12,11,0.001,2.1\n"
        ),
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "single_cell",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "escalate_to_researcher"
    assert "implausible_cluster_fragmentation" in review.decision.fail_metric_names


def test_review_run_results_accepts_transcript_quant_with_gtf_and_abundance_table(tmp_path: Path) -> None:
    """Transcript quant outputs should accept when GTF and abundance table are valid."""

    (tmp_path / "assembled.gtf").write_text(
        'chr1\tStringTie\ttranscript\t1\t1000\t.\t+\t.\tgene_id "ENSG1"; transcript_id "ENST1";\n',
        encoding="utf-8",
    )
    (tmp_path / "gene_abundances.tsv").write_text(
        "Gene ID\tGene Name\tReference\tCoverage\tFPKM\tTPM\n"
        "ENSG1\tGENE1\tchr1\t10.0\t5.2\t6.1\n"
        "ENSG2\tGENE2\tchr1\t8.0\t3.7\t4.4\n",
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "transcript_quantification",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept"


def test_review_run_results_escalates_on_zero_abundance_transcript_quant(tmp_path: Path) -> None:
    """Degenerate transcript-quant outputs should escalate review."""

    (tmp_path / "assembled.gtf").write_text(
        'chr1\tStringTie\ttranscript\t1\t1000\t.\t+\t.\tgene_id "ENSG1"; transcript_id "ENST1";\n',
        encoding="utf-8",
    )
    (tmp_path / "gene_abundances.tsv").write_text(
        "Gene ID\tGene Name\tReference\tCoverage\tFPKM\tTPM\n"
        "ENSG1\tGENE1\tchr1\t0.0\t0.0\t0.0\n"
        "ENSG2\tGENE2\tchr1\t0.0\t0.0\t0.0\n",
        encoding="utf-8",
    )

    review = review_run_results(
        tmp_path,
        "transcript_quantification",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "escalate_to_researcher"
    assert "all_primary_abundance_zero" in review.decision.fail_metric_names


def test_review_run_results_ignores_transcript_quant_logs_and_state(tmp_path: Path) -> None:
    """Transcript-quant review should ignore noisy runtime artifacts."""

    (tmp_path / "assembled.gtf").write_text(
        'chr1\tStringTie\ttranscript\t1\t1000\t.\t+\t.\tgene_id "ENSG1"; transcript_id "ENST1";\n',
        encoding="utf-8",
    )
    (tmp_path / "gene_abundances.tsv").write_text(
        "Gene ID\tGene Name\tReference\tCoverage\tFPKM\tTPM\n"
        "ENSG1\tGENE1\tchr1\t10.0\t5.2\t6.1\n"
        "ENSG2\tGENE2\tchr1\t8.0\t3.7\t4.4\n",
        encoding="utf-8",
    )
    (tmp_path / "stdout.log").write_text("comma,separated,noise\n", encoding="utf-8")
    (tmp_path / "state.json").write_text('{"status":"completed"}\n', encoding="utf-8")
    (tmp_path / "processed.h5ad").write_bytes(b"h5ad-placeholder")

    review = review_run_results(
        tmp_path,
        "transcript_quantification",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept"
    assert "all_primary_abundance_zero" not in review.decision.fail_metric_names


def test_review_run_results_ignores_single_cell_runtime_artifacts(tmp_path: Path) -> None:
    """Single-cell review should use result tables, not logs or caches."""

    (tmp_path / "cluster_assignments.csv").write_text(
        "cell_id,cluster\ncell1,0\ncell2,0\ncell3,1\ncell4,1\n",
        encoding="utf-8",
    )
    (tmp_path / "marker_genes.csv").write_text(
        "gene,cluster,pval_adj,log2fc\nCD3D,0,0.001,3.2\nCD79A,1,0.002,2.9\n",
        encoding="utf-8",
    )
    (tmp_path / "single_cell_results.csv").write_text(
        "gene,cluster,pval_adj,log2fc,score,cells_detected,mean_expression\n"
        "CD3D,0,0.001,3.2,9.1,2,1.4\n"
        "CD79A,1,0.002,2.9,8.7,2,1.2\n",
        encoding="utf-8",
    )
    (tmp_path / "stdout.log").write_text("debug,noise\n", encoding="utf-8")
    (tmp_path / "stderr.log").write_text("debug,noise\n", encoding="utf-8")
    (tmp_path / "processed.h5ad").write_bytes(b"h5ad-placeholder")

    review = review_run_results(
        tmp_path,
        "single_cell",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept"
    assert "implausible_cluster_fragmentation" not in review.decision.fail_metric_names


def test_review_run_results_ignores_variant_tool_internals(tmp_path: Path) -> None:
    """Variant review should ignore internal tool databases and staged inputs."""

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "annotated.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t40\tPASS\t.\n",
        encoding="utf-8",
    )
    (output_dir / "filtered_pathogenic.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t40\tPASS\t.\n",
        encoding="utf-8",
    )
    (tmp_path / "input_variants.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t40\tPASS\t.\n",
        encoding="utf-8",
    )
    internal_dir = output_dir / "_snpeff" / "data" / "custom_bench"
    internal_dir.mkdir(parents=True)
    (internal_dir / "sequence.bin").write_bytes(b"binary")

    review = review_run_results(
        tmp_path,
        "variant_calling",
        {"plan": [], "final_deliverables": []},
        llm=None,
    )

    assert review.decision.decision.value == "accept"
    assert "parse_failure" not in review.decision.fail_metric_names
