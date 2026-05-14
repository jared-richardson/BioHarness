"""Tests for semantic feature extraction used by deterministic result review."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.output_semantic_features import (
    extract_single_cell_fragmentation_features,
    extract_transcript_quant_features,
    extract_vcf_header_payload_features,
)


def test_extract_transcript_quant_features_flags_all_zero_abundance() -> None:
    """Transcript-quant features should identify all-zero abundance tables."""

    features = extract_transcript_quant_features(
        ["Gene ID", "Coverage", "FPKM", "TPM"],
        [
            {"Gene ID": "ENSG1", "Coverage": "0.0", "FPKM": "0.0", "TPM": "0.0"},
            {"Gene ID": "ENSG2", "Coverage": "0.0", "FPKM": "0.0", "TPM": "0.0"},
        ],
    )

    assert features.row_count == 2
    assert features.numeric_value_count == 6
    assert features.zero_value_count == 6
    assert features.all_primary_abundance_zero is True
    assert features.abundance_dynamic_range == 0.0


def test_extract_vcf_header_payload_features_reports_missing_payload_contigs(tmp_path: Path) -> None:
    """VCF semantic features should capture header and payload disagreements."""

    path = tmp_path / "variants.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chrX,length=100>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr1\t10\t.\tA\tG\t40\tPASS\t.\n",
        encoding="utf-8",
    )

    features = extract_vcf_header_payload_features(path)

    assert features.header_contig_ids == ("chrX",)
    assert features.payload_contig_ids == ("chr1",)
    assert features.payload_contigs_missing_from_header == ("chr1",)


def test_extract_single_cell_fragmentation_features_quantifies_singletons(tmp_path: Path) -> None:
    """Single-cell features should quantify cluster fragmentation cleanly."""

    clusters = tmp_path / "clusters.csv"
    clusters.write_text(
        "cell_id,cluster\n"
        "c1,0\n"
        "c2,1\n"
        "c3,2\n"
        "c4,3\n",
        encoding="utf-8",
    )
    markers = tmp_path / "markers.csv"
    markers.write_text(
        "gene,cluster,pval_adj,log2fc\n"
        "G1,0,0.001,2.0\n"
        "G2,1,0.001,2.0\n"
        "G3,2,0.001,2.0\n"
        "G4,3,0.001,2.0\n",
        encoding="utf-8",
    )

    features = extract_single_cell_fragmentation_features(clusters, markers)

    assert features is not None
    assert features.cell_count == 4
    assert features.cluster_count == 4
    assert features.cluster_to_cell_ratio == 1.0
    assert features.singleton_cluster_fraction == 1.0
    assert features.median_cluster_size == 1.0
