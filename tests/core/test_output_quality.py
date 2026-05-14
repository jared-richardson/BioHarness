"""Tests for deterministic output quality assessment helpers."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bio_harness.core.output_quality import (
    QualityLevel,
    _detect_file_type,
    _worst_level,
    assess_bam_quality,
    assess_fastq_quality,
    assess_output_quality,
    assess_tabular_quality,
    assess_vcf_quality,
)
from bio_harness.core.tool_env import which_with_pixi


_SAM_HEADER = textwrap.dedent(
    """\
    @HD\tVN:1.6\tSO:coordinate
    @SQ\tSN:chr1\tLN:100
    @SQ\tSN:chr2\tLN:100
    """
).rstrip("\n")

_SAM_READS = textwrap.dedent(
    """\
    read1\t0\tchr1\t1\t60\t8M\t*\t0\t0\tACGTACGT\tIIIIIIII
    read2\t0\tchr1\t10\t60\t8M\t*\t0\t0\tACGTACGT\tIIIIIIII
    read3\t0\tchr2\t1\t60\t8M\t*\t0\t0\tTTTTAAAA\tIIIIIIII
    read4\t0\tchr2\t15\t60\t8M\t*\t0\t0\tCCCCGGGG\tIIIIIIII
    read5\t4\t*\t0\t0\t*\t*\t0\t0\tNNNNNNNN\tIIIIIIII
    read6\t1024\tchr1\t1\t60\t8M\t*\t0\t0\tACGTACGT\tIIIIIIII
    """
).rstrip("\n")

_VCF_TEXT = textwrap.dedent(
    """\
    ##fileformat=VCFv4.2
    ##contig=<ID=chr1,length=100>
    ##contig=<ID=chr2,length=100>
    ##INFO=<ID=DP,Number=1,Type=Integer,Description="Read depth">
    #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
    chr1\t10\tvar1\tA\tG\t30\tPASS\tDP=15
    chr1\t25\tvar2\tC\tT\t25\tPASS\tDP=20
    chr1\t50\tvar3\tG\tT\t35\tPASS\tDP=12
    chr2\t15\tvar4\tT\tC\t40\tPASS\tDP=25
    chr2\t30\tvar5\tA\tC\t22\tLowQual\tDP=8
    """
)

_FASTQ_TEXT = textwrap.dedent(
    """\
    @read1
    ACGTACGTACGTACGTACGT
    +
    IIIIIIIIIIIIIIIIIIII
    @read2
    TTTTAAAACCCCGGGGTTTT
    +
    IIIIIIIIIIIIIIIIIIII
    @read3
    GGGGCCCCTTTTAAAACCCC
    +
    IIIIIIIIIIIIIIIIIIII
    @read4
    ACGTACGTTTTTAAAACCCC
    +
    IIIIIIIIIIIIIIIIIIII
    """
)


def _run(cmd: list[str]) -> None:
    import subprocess

    subprocess.run(cmd, capture_output=True, check=True, text=True)


@pytest.fixture
def tiny_fastq(tmp_path: Path) -> Path:
    path = tmp_path / "reads.fastq"
    path.write_text(_FASTQ_TEXT)
    return path


@pytest.fixture
def tiny_vcf(tmp_path: Path) -> Path:
    path = tmp_path / "variants.vcf"
    path.write_text(_VCF_TEXT)
    return path


@pytest.fixture
def de_results_tsv(tmp_path: Path) -> Path:
    path = tmp_path / "deseq_results.tsv"
    path.write_text(
        "gene\tlog2FoldChange\tpadj\n"
        "gene1\t2.0\t0.001\n"
        "gene2\t-1.5\t0.020\n"
        "gene3\t0.2\t0.700\n"
    )
    return path


@pytest.fixture
def generic_csv(tmp_path: Path) -> Path:
    path = tmp_path / "table.csv"
    path.write_text("sample,value\ns1,10\ns2,15\n")
    return path


@pytest.fixture
def tiny_bam(tmp_path: Path) -> Path:
    samtools = which_with_pixi("samtools")
    if not samtools:
        pytest.skip("samtools not available")

    sam = tmp_path / "input.sam"
    sam.write_text(_SAM_HEADER + "\n" + _SAM_READS + "\n")
    bam = tmp_path / "input.bam"
    sorted_bam = tmp_path / "sorted.bam"
    _run([samtools, "view", "-bS", str(sam), "-o", str(bam)])
    _run([samtools, "sort", str(bam), "-o", str(sorted_bam)])
    _run([samtools, "index", str(sorted_bam)])
    return sorted_bam


def test_detect_file_type_handles_common_formats(
    tiny_fastq: Path,
    tiny_vcf: Path,
    de_results_tsv: Path,
    tmp_path: Path,
) -> None:
    sam_path = tmp_path / "reads.sam"
    sam_path.write_text(_SAM_HEADER + "\n" + _SAM_READS + "\n")

    assert _detect_file_type(tiny_fastq) == "fastq"
    assert _detect_file_type(tiny_vcf) == "vcf"
    assert _detect_file_type(de_results_tsv) == "tsv"
    assert _detect_file_type(sam_path) == "bam"


def test_assess_output_quality_dispatches_fastq(tiny_fastq: Path) -> None:
    report = assess_output_quality(tiny_fastq)

    assert report.file_type == "fastq"
    assert report.overall_level == QualityLevel.PASS


def test_fastq_quality_good(tiny_fastq: Path) -> None:
    report = assess_fastq_quality(tiny_fastq)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["read_count"].value == 4.0
    assert metrics["mean_quality"].value == 40.0
    assert report.overall_level == QualityLevel.PASS


def test_fastq_quality_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.fastq"
    path.write_text("")

    report = assess_fastq_quality(path)

    assert report.overall_level == QualityLevel.FAIL
    assert report.metrics[0].name == "empty_file"


def test_fastq_quality_short_reads_warn(tmp_path: Path) -> None:
    path = tmp_path / "short.fastq"
    path.write_text("@r1\nACGTACGTACGTACG\n+\nIIIIIIIIIIIIIII\n")

    report = assess_fastq_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["mean_read_length"].value == 15.0
    assert metrics["short_reads"].level == QualityLevel.WARNING
    assert report.overall_level == QualityLevel.WARNING


def test_fastq_quality_low_quality_fails(tmp_path: Path) -> None:
    path = tmp_path / "low.fastq"
    path.write_text("@r1\nACGTACGTACGTACGT\n+\n################\n")

    report = assess_fastq_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["mean_quality"].value == 2.0
    assert metrics["low_base_quality"].level == QualityLevel.FAIL
    assert report.overall_level == QualityLevel.FAIL


def test_fastq_quality_truncated_fails(tmp_path: Path) -> None:
    path = tmp_path / "truncated.fastq"
    path.write_text("@r1\nACGTACGT\n+\nIIIIIIII\n@r2\nACGTACGT\n+\n")

    report = assess_fastq_quality(path)

    assert report.overall_level == QualityLevel.FAIL
    assert report.metrics[0].name == "truncated_file"


def test_tabular_quality_generic_csv(generic_csv: Path) -> None:
    report = assess_tabular_quality(generic_csv)
    metrics = {metric.name: metric for metric in report.metrics}

    assert report.file_type == "csv"
    assert metrics["row_count"].value == 2.0
    assert metrics["column_count"].value == 2.0
    assert report.overall_level == QualityLevel.PASS


def test_tabular_quality_de_results(de_results_tsv: Path) -> None:
    report = assess_tabular_quality(
        de_results_tsv,
        tool_name="deseq2_run",
        analysis_type="rna_seq_differential_expression",
    )
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["significant_row_count"].value == 2.0
    assert metrics["significant_genes"].value == 2.0
    assert "fold_change_range" in metrics
    assert report.overall_level == QualityLevel.PASS


def test_tabular_quality_detects_de_without_context(tmp_path: Path) -> None:
    path = tmp_path / "good_de.csv"
    path.write_text(
        "gene,log2FoldChange,padj\n"
        "gene1,2.0,0.001\n"
        "gene2,-1.5,0.02\n"
        "gene3,0.2,0.7\n"
    )

    report = assess_tabular_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["total_genes"].value == 3.0
    assert metrics["significant_genes"].value == 2.0


def test_tabular_quality_missing_required_de_column_fails(tmp_path: Path) -> None:
    path = tmp_path / "missing_col_de.csv"
    path.write_text("gene,log2FoldChange\nGENE1,1.2\nGENE2,-0.5\n")

    report = assess_tabular_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["missing_required_column"].level == QualityLevel.FAIL
    assert report.overall_level == QualityLevel.FAIL


def test_tabular_quality_single_cell_markers_do_not_use_de_requirements(tmp_path: Path) -> None:
    path = tmp_path / "markers.csv"
    path.write_text(
        "gene,cluster,pval_adj,log2fc\n"
        "CD3D,0,0.001,3.2\n"
        "CD79A,1,0.002,2.9\n"
    )

    report = assess_tabular_quality(path, analysis_type="single_cell")
    metrics = {metric.name: metric for metric in report.metrics}

    assert "missing_required_column" not in metrics
    assert metrics["marker_gene_count"].value == 2.0
    assert metrics["cluster_count"].value == 2.0
    assert report.overall_level == QualityLevel.PASS


def test_tabular_quality_transcript_quant_all_zero_abundance_fails(tmp_path: Path) -> None:
    """Transcript-quant outputs should fail when every primary abundance is zero."""

    path = tmp_path / "gene_abundances.tsv"
    path.write_text(
        "Gene ID\tGene Name\tCoverage\tFPKM\tTPM\n"
        "ENSG1\tGENE1\t0.0\t0.0\t0.0\n"
        "ENSG2\tGENE2\t0.0\t0.0\t0.0\n"
    )

    report = assess_tabular_quality(path, analysis_type="transcript_quantification")
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["all_primary_abundance_zero"].level == QualityLevel.FAIL
    assert report.overall_level == QualityLevel.FAIL


def test_tabular_quality_transcript_quant_low_but_nonzero_abundance_passes(tmp_path: Path) -> None:
    """Transcript-quant outputs should not fail on uniformly low non-zero abundance."""

    path = tmp_path / "gene_abundances.tsv"
    path.write_text(
        "Gene ID\tGene Name\tCoverage\tFPKM\tTPM\n"
        "ENSG1\tGENE1\t0.1\t0.05\t0.03\n"
        "ENSG2\tGENE2\t0.2\t0.06\t0.04\n"
    )

    report = assess_tabular_quality(path, analysis_type="transcript_quantification")
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["all_primary_abundance_zero"].level == QualityLevel.PASS
    assert report.overall_level == QualityLevel.PASS


def test_gtf_quality_passes_when_feature_rows_exist(tmp_path: Path) -> None:
    path = tmp_path / "assembled.gtf"
    path.write_text(
        'chr1\tStringTie\ttranscript\t1\t1000\t.\t+\t.\tgene_id "ENSG1"; transcript_id "ENST1";\n'
    )

    report = assess_output_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert report.file_type == "gtf"
    assert metrics["feature_count"].value == 1.0
    assert metrics["annotation_attributes"].value == 1.0
    assert report.overall_level == QualityLevel.PASS


def test_tabular_quality_high_na_fraction_warns(tmp_path: Path) -> None:
    path = tmp_path / "nan_heavy_de.csv"
    path.write_text(
        "gene,log2FoldChange,padj\n"
        "g1,1.0,\n"
        "g2,2.0,NA\n"
        "g3,3.0,\n"
        "g4,4.0,0.04\n"
    )

    report = assess_tabular_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["na_fraction"].value == pytest.approx(0.75)
    assert metrics["high_na_fraction"].level == QualityLevel.WARNING


def test_tabular_quality_no_significant_genes_warns(tmp_path: Path) -> None:
    path = tmp_path / "no_sig_de.csv"
    path.write_text(
        "gene,log2FoldChange,padj\n"
        "gene1,0.2,0.5\n"
        "gene2,-0.1,0.9\n"
    )

    report = assess_tabular_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["significant_genes"].value == 0.0
    assert metrics["no_significant_genes"].level == QualityLevel.WARNING


def test_tabular_quality_all_significant_warns(tmp_path: Path) -> None:
    path = tmp_path / "all_sig_de.csv"
    path.write_text(
        "gene,log2FoldChange,padj\n"
        "gene1,1.1,0.001\n"
        "gene2,1.2,0.002\n"
        "gene3,1.3,0.003\n"
    )

    report = assess_tabular_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["suspiciously_all_significant"].level == QualityLevel.WARNING


def test_bam_quality_good(tiny_bam: Path) -> None:
    report = assess_bam_quality(tiny_bam)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["total_reads"].value == 6.0
    assert metrics["mapping_rate"].value > 0.5
    assert report.overall_level in {QualityLevel.PASS, QualityLevel.WARNING}


def test_bam_quality_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.sam"
    path.write_text(_SAM_HEADER + "\n")

    report = assess_bam_quality(path)

    assert report.overall_level == QualityLevel.FAIL
    assert report.metrics[0].name == "empty_file"


def test_bam_quality_sam_metrics(tmp_path: Path) -> None:
    path = tmp_path / "input.sam"
    path.write_text(_SAM_HEADER + "\n" + _SAM_READS + "\n")

    report = assess_bam_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["mapping_rate"].value == pytest.approx(5.0 / 6.0)
    assert metrics["duplicate_rate"].value == pytest.approx(1.0 / 6.0)


def test_bam_quality_sam_does_not_require_samtools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "input.sam"
    path.write_text(_SAM_HEADER + "\n" + _SAM_READS + "\n")
    monkeypatch.setattr("bio_harness.core.output_quality._resolve_tool", lambda _name: None)

    report = assess_bam_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert report.overall_level in {QualityLevel.PASS, QualityLevel.WARNING}
    assert metrics["mapping_rate"].value == pytest.approx(5.0 / 6.0)


def test_bam_quality_skips_without_samtools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bam = tmp_path / "fake.bam"
    bam.write_bytes(b"bam")
    monkeypatch.setattr("bio_harness.core.output_quality._resolve_tool", lambda _name: None)

    report = assess_bam_quality(bam)

    assert report.overall_level == QualityLevel.SKIP


def test_vcf_quality_good(tiny_vcf: Path) -> None:
    report = assess_vcf_quality(tiny_vcf)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["variant_count"].value == 5.0
    assert metrics["pass_variant_count"].value == 4.0
    assert metrics["pass_rate"].value == pytest.approx(0.8)
    assert metrics["pass_fraction"].value == pytest.approx(0.8)
    assert report.overall_level in {QualityLevel.PASS, QualityLevel.WARNING}


def test_vcf_quality_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.vcf"
    path.write_text("")

    report = assess_vcf_quality(path)

    assert report.overall_level == QualityLevel.FAIL


def test_vcf_quality_falls_back_without_bcftools(
    monkeypatch: pytest.MonkeyPatch,
    tiny_vcf: Path,
) -> None:
    monkeypatch.setattr("bio_harness.core.output_quality._resolve_tool", lambda _name: None)

    report = assess_vcf_quality(tiny_vcf)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["variant_count"].value == 5.0
    assert metrics["pass_fraction"].value == pytest.approx(0.8)


def test_vcf_quality_no_pass_variants_fails(tmp_path: Path) -> None:
    path = tmp_path / "variants.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
        "chr1\t10\t.\tA\tG\t4\tLowQual\tDP=5\tGT:GQ\t0/1:4\n"
    )

    report = assess_vcf_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["no_pass_variants"].level == QualityLevel.FAIL
    assert report.overall_level == QualityLevel.FAIL


def test_vcf_quality_low_gq_warns(tmp_path: Path) -> None:
    path = tmp_path / "variants.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
        "chr1\t10\t.\tA\tG\t30\tPASS\tDP=5\tGT:GQ\t0/1:3\n"
        "chr1\t20\t.\tC\tT\t30\tPASS\tDP=5\tGT:GQ\t0/1:4\n"
    )

    report = assess_vcf_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["mean_gq"].value == pytest.approx(3.5)
    assert metrics["low_genotype_quality"].level == QualityLevel.WARNING


def test_vcf_quality_clustered_variants_warn(tmp_path: Path) -> None:
    path = tmp_path / "variants.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\n"
        "chr1\t10\t.\tA\tG\t30\tPASS\tDP=5\tGT:GQ\t0/1:30\n"
        "chr1\t12\t.\tC\tT\t30\tPASS\tDP=5\tGT:GQ\t0/1:30\n"
    )

    report = assess_vcf_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["variant_clustering"].level == QualityLevel.WARNING


def test_vcf_quality_header_payload_mismatch_fails(tmp_path: Path) -> None:
    """VCFs should fail when payload contigs are missing from the header."""

    path = tmp_path / "variants.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chrX,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t30\tPASS\tDP=5\n"
    )

    report = assess_vcf_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert metrics["header_payload_contig_mismatch"].level == QualityLevel.FAIL
    assert report.overall_level == QualityLevel.FAIL


def test_vcf_quality_header_superset_of_payload_passes(tmp_path: Path) -> None:
    """VCFs should pass semantic header checks when the header is a superset."""

    path = tmp_path / "variants.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=100>\n"
        "##contig=<ID=chr2,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t30\tPASS\tDP=5\n"
    )

    report = assess_vcf_quality(path)
    metrics = {metric.name: metric for metric in report.metrics}

    assert "header_payload_contig_mismatch" not in metrics
    assert report.overall_level == QualityLevel.PASS


def test_worst_level_prefers_fail() -> None:
    class _Metric:
        def __init__(self, level: QualityLevel):
            self.level = level

    assert _worst_level((_Metric(QualityLevel.PASS), _Metric(QualityLevel.FAIL))) == QualityLevel.FAIL
