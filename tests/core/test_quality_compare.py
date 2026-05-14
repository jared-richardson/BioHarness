"""Tests for run-quality comparison helpers."""

from __future__ import annotations

from pathlib import Path

from bio_harness.reporting.quality_compare import (
    _determine_better,
    _extract_run_metrics,
    compare_run_quality,
    quality_comparison_to_markdown,
)


def test_extract_vcf_metrics(tmp_path: Path) -> None:
    vcf = tmp_path / "variants.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )

    metrics = _extract_run_metrics(tmp_path, {"plan": [], "final_deliverables": []})

    assert metrics["variant_calling"]["variant_count"] == 1.0


def test_extract_de_metrics(tmp_path: Path) -> None:
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

    metrics = _extract_run_metrics(tmp_path, plan)

    assert metrics["de_results"]["significant_row_count"] == 1.0


def test_extract_empty_dir(tmp_path: Path) -> None:
    assert _extract_run_metrics(tmp_path, {"plan": [], "final_deliverables": []}) == {}


def test_extract_result_json_summary_metrics(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text(
        """
        {
          "status": "completed",
          "auto_repair_history_count": 2,
          "quality_metrics": {"mapping_rate": 0.91, "variant_count": 42},
          "elapsed_seconds": 120.0,
          "steps_completed": 6,
          "steps_total": 8,
          "outputs": ["final/a.txt", "final/b.txt"]
        }
        """.strip()
    )

    metrics = _extract_run_metrics(tmp_path, {"plan": [], "final_deliverables": []})

    assert metrics["summary"]["mapping_rate"] == 0.91
    assert metrics["summary"]["variant_count"] == 42.0
    assert metrics["summary"]["repairs"] == 2.0
    assert metrics["summary"]["completion_rate"] == 0.75
    assert metrics["summary"]["output_count"] == 2.0


def test_compare_same_metrics(tmp_path: Path) -> None:
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    vcf_text = (
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )
    (run_a / "variants.vcf").write_text(vcf_text)
    (run_b / "variants.vcf").write_text(vcf_text)

    comparison = compare_run_quality(run_a, run_b)

    assert any(item.better_run == "same" for item in comparison.metric_comparisons)


def test_compare_better_mapping_rate() -> None:
    assert _determine_better("mapping_rate", 0.90, 0.95) == "b"


def test_compare_lower_duplicate_rate() -> None:
    assert _determine_better("duplicate_rate", 0.20, 0.10) == "b"


def test_compare_tstv_closer_to_two() -> None:
    assert _determine_better("ts_tv_ratio", 1.5, 2.1) == "b"


def test_compare_missing_metric() -> None:
    assert _determine_better("mapping_rate", 0.90, None) == "unknown"


def test_compare_treats_small_deltas_as_same() -> None:
    assert _determine_better("mapping_rate", 0.94, 0.95) == "same"


def test_compare_treats_small_variant_count_delta_as_same() -> None:
    assert _determine_better("variant_count", 145, 147) == "same"
    assert _determine_better("repairs", 3, 4) == "same"


def test_compare_result_json_improvement(tmp_path: Path) -> None:
    run_a = tmp_path / "run_a"
    run_b = tmp_path / "run_b"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "result.json").write_text(
        """
        {
          "status": "completed",
          "auto_repair_history_count": 2,
          "quality_metrics": {"mapping_rate": 0.6, "variant_count": 50}
        }
        """.strip()
    )
    (run_b / "result.json").write_text(
        """
        {
          "status": "completed",
          "auto_repair_history_count": 0,
          "quality_metrics": {"mapping_rate": 0.95, "variant_count": 150}
        }
        """.strip()
    )

    comparison = compare_run_quality(run_a, run_b)

    assert comparison.overall_winner == "b"
    assert any(item.metric_name.endswith("mapping_rate") and item.better_run == "b" for item in comparison.metric_comparisons)
    assert any(item.metric_name.endswith("repairs") and item.better_run == "b" for item in comparison.metric_comparisons)


def test_compare_detects_different_pipeline(tmp_path: Path) -> None:
    run_a = tmp_path / "run_a"
    run_b = tmp_path / "run_b"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "result.json").write_text(
        """
        {
          "status": "completed",
          "quality_metrics": {"mapping_rate": 0.92, "tools_used": ["bwa_mem_align", "gatk_haplotype_caller"]}
        }
        """.strip()
    )
    (run_b / "result.json").write_text(
        """
        {
          "status": "completed",
          "quality_metrics": {"mapping_rate": 0.93, "tools_used": ["minimap2_align", "deepvariant_call"]}
        }
        """.strip()
    )

    comparison = compare_run_quality(run_a, run_b)

    assert comparison.overall_winner == "different_pipeline"


def test_compare_mixed_results(tmp_path: Path) -> None:
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "a.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
        "chr1\t2\t.\tC\tT\t10\tPASS\t.\n"
    )
    (run_b / "b.vcf").write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t1\t.\tA\tG\t10\tPASS\t.\n"
        "chr1\t2\t.\tC\tA\t10\tLowQual\t.\n"
    )
    (run_a / "results_deseq.csv").write_text("gene,log2FoldChange,padj\nA,2.0,0.001\n")
    (run_b / "results_deseq.csv").write_text("gene,log2FoldChange,padj\nA,2.0,0.001\nB,1.0,0.002\n")
    plan = {"plan": [], "final_deliverables": []}

    comparison = compare_run_quality(run_a, run_b, plan, plan)

    assert comparison.overall_winner == "mixed"


def test_markdown_table_format(tmp_path: Path) -> None:
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "variants.vcf").write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )
    (run_b / "variants.vcf").write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\nchr1\t1\t.\tA\tG\t10\tPASS\t.\n"
    )

    markdown = quality_comparison_to_markdown(compare_run_quality(run_a, run_b))

    assert "| Metric | Run A | Run B | Delta | Better |" in markdown


def test_summary_mentions_winner(tmp_path: Path) -> None:
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "results_deseq.csv").write_text("gene,log2FoldChange,padj\nA,2.0,0.100\n")
    (run_b / "results_deseq.csv").write_text("gene,log2FoldChange,padj\nA,2.0,0.001\nB,1.0,0.002\n")

    comparison = compare_run_quality(run_a, run_b)

    assert "Run B" in comparison.summary
    assert comparison.overall_winner == "b"
