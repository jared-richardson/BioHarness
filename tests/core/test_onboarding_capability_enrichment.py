from __future__ import annotations

from bio_harness.core.capability_catalog import load_capability_catalog
from bio_harness.core.onboarding_capability_enrichment import enrich_onboarding_metadata


def test_enrich_onboarding_metadata_infers_missing_capabilities_and_categories(tmp_path) -> None:
    catalog = load_capability_catalog(tmp_path / "catalog.json")

    enriched = enrich_onboarding_metadata(
        {
            "skill_name": "demo_dge",
            "description": "Run DESeq2 differential expression between treatment and control from a count matrix and metadata table.",
            "risk_level": "medium",
            "tools_required": ["deseq2", "rscript"],
            "capabilities": ["analysis"],
            "parameters": {
                "counts_matrix": {"type": "path", "description": "Gene counts TSV.", "required": True},
                "metadata_table": {"type": "path", "description": "Sample metadata TSV.", "required": True},
                "output_dir": {"type": "path", "description": "Result directory.", "required": True},
            },
            "command_template": "Rscript run_deseq2.R --counts {counts_matrix} --metadata {metadata_table} --outdir {output_dir}",
        },
        capability_catalog=catalog,
    )

    assert "differential_analysis" in enriched["capabilities"]
    assert "group_comparison" in enriched["capabilities"]
    assert enriched["analysis_categories"] == ["rna_seq_differential_expression"]
    assert "tsv" in enriched["input_types"]
    assert "directory" in enriched["output_types"]


def test_enrich_onboarding_metadata_keeps_strong_explicit_capabilities(tmp_path) -> None:
    catalog = load_capability_catalog(tmp_path / "catalog.json")

    enriched = enrich_onboarding_metadata(
        {
            "skill_name": "demo_align",
            "description": "Align short reads to a reference genome.",
            "tools_required": ["star"],
            "capabilities": ["alignment"],
            "parameters": {
                "reads_1": {"type": "path", "description": "FASTQ read 1.", "required": True},
                "output_bam": {"type": "path", "description": "Aligned BAM.", "required": True},
            },
        },
        capability_catalog=catalog,
    )

    assert enriched["capabilities"] == ["alignment"]
    assert enriched["analysis_categories"] == ["alignment"]
    assert enriched["input_types"] == ["fastq"]
    assert enriched["output_types"] == ["bam"]
