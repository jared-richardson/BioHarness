"""Tests for output-catalog review scoping."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.output_catalog import build_output_catalog


def test_build_output_catalog_limits_reviewable_entries_to_primary_outputs(tmp_path: Path) -> None:
    """Reviewable entries should exclude logs, state, and tool internals."""

    (tmp_path / "gene_abundances.tsv").write_text(
        "Gene ID\tCoverage\tFPKM\tTPM\nENSG1\t1.0\t0.5\t0.4\n",
        encoding="utf-8",
    )
    (tmp_path / "assembled.gtf").write_text(
        'chr1\tStringTie\ttranscript\t1\t100\t.\t+\t.\tgene_id "ENSG1"; transcript_id "ENST1";\n',
        encoding="utf-8",
    )
    (tmp_path / "stdout.log").write_text("comma,separated,noise\n", encoding="utf-8")
    (tmp_path / "state.json").write_text('{"status":"completed"}\n', encoding="utf-8")
    internal_dir = tmp_path / "_snpeff" / "data"
    internal_dir.mkdir(parents=True)
    (internal_dir / "sequence.bin").write_bytes(b"binary")

    catalog = build_output_catalog(
        tmp_path,
        {"plan": [], "final_deliverables": []},
        analysis_type="transcript_quantification",
    )

    reviewable = {Path(entry.path).name for entry in catalog.reviewable_entries}
    provenance = {Path(entry.path).name for entry in catalog.provenance_files}
    internal = {Path(entry.path).name for entry in catalog.internal_files}

    assert reviewable == {"gene_abundances.tsv", "assembled.gtf"}
    assert "stdout.log" in provenance
    assert "state.json" in provenance
    assert "sequence.bin" in internal


def test_build_output_catalog_marks_spatial_outputs_reviewable(tmp_path: Path) -> None:
    """Spatial domain and marker artifacts should be reviewable."""

    (tmp_path / "spatial_domain_assignments.csv").write_text(
        "spot_id,domain,x,y\nspot_1,Domain1,0,0\n",
        encoding="utf-8",
    )
    (tmp_path / "spatial_marker_genes.csv").write_text(
        "domain,gene,score\nDomain1,Gene_1,1.0\n",
        encoding="utf-8",
    )
    (tmp_path / "spatial_results.h5ad").write_text("placeholder\n", encoding="utf-8")

    catalog = build_output_catalog(
        tmp_path,
        {"plan": [], "final_deliverables": []},
        analysis_type="spatial_transcriptomics",
    )

    reviewable = {Path(entry.path).name for entry in catalog.reviewable_entries}
    assert reviewable == {
        "spatial_domain_assignments.csv",
        "spatial_marker_genes.csv",
        "spatial_results.h5ad",
    }


def test_build_output_catalog_marks_proteomics_outputs_reviewable(tmp_path: Path) -> None:
    """Proteomics result artifacts should be reviewable."""

    (tmp_path / "proteomics_differential_abundance.csv").write_text(
        "protein_id,log2FoldChange,pvalue,padj\nPROT_0001,2.1,0.001,0.01\n",
        encoding="utf-8",
    )
    (tmp_path / "proteomics_qc_summary.json").write_text('{"proteins_retained": 50}\n', encoding="utf-8")
    (tmp_path / "normalized_abundance_matrix.tsv").write_text("protein_id\tsample_0\nPROT_0001\t1.0\n", encoding="utf-8")
    (tmp_path / "volcano_plot_data.tsv").write_text("protein_id\tlog2FoldChange\tpvalue\tpadj\n", encoding="utf-8")
    (tmp_path / "proteomics_summary.md").write_text("# Proteomics\n", encoding="utf-8")

    catalog = build_output_catalog(
        tmp_path,
        {"plan": [], "final_deliverables": []},
        analysis_type="proteomics",
    )

    reviewable = {Path(entry.path).name for entry in catalog.reviewable_entries}
    assert reviewable == {
        "proteomics_differential_abundance.csv",
        "proteomics_qc_summary.json",
        "normalized_abundance_matrix.tsv",
        "volcano_plot_data.tsv",
        "proteomics_summary.md",
    }


def test_build_output_catalog_marks_metabolomics_outputs_reviewable(tmp_path: Path) -> None:
    """Metabolomics result artifacts should be reviewable."""

    (tmp_path / "metabolomics_differential_abundance.csv").write_text(
        "feature_id,log2FoldChange,pvalue,padj\nmz100_rt1,2.1,0.001,0.01\n",
        encoding="utf-8",
    )
    (tmp_path / "metabolomics_qc_summary.json").write_text('{"features_retained": 50}\n', encoding="utf-8")
    (tmp_path / "normalized_feature_matrix.tsv").write_text("feature_id\tsample_0\nmz100_rt1\t1.0\n", encoding="utf-8")
    (tmp_path / "volcano_plot_data.tsv").write_text("feature_id\tlog2FoldChange\tpvalue\tpadj\n", encoding="utf-8")
    (tmp_path / "metabolomics_summary.md").write_text("# Metabolomics\n", encoding="utf-8")

    catalog = build_output_catalog(
        tmp_path,
        {"plan": [], "final_deliverables": []},
        analysis_type="metabolomics",
    )

    reviewable = {Path(entry.path).name for entry in catalog.reviewable_entries}
    assert reviewable == {
        "metabolomics_differential_abundance.csv",
        "metabolomics_qc_summary.json",
        "normalized_feature_matrix.tsv",
        "volcano_plot_data.tsv",
        "metabolomics_summary.md",
    }
