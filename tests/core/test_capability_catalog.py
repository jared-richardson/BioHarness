from __future__ import annotations

from bio_harness.core.capability_catalog import (
    infer_capabilities_from_text,
    infer_tool_hints_from_text,
    load_capability_catalog,
    update_capability_tool_hints,
)


def test_load_capability_catalog_bootstraps_defaults(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    assert catalog_path.exists()
    cap_ids = {c.get("id", "") for c in catalog.get("capabilities", [])}
    assert "alignment" in cap_ids
    assert "differential_analysis" in cap_ids
    assert "group_comparison" in cap_ids


def test_infer_capabilities_and_hints_from_text(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Run differential expression with DESeq2 using treatment vs control."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "differential_analysis" in caps
    assert "group_comparison" in caps
    assert "deseq2" in hints


def test_infer_pathway_enrichment_capability_from_text(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Perform differential expression and KEGG pathway enrichment analysis across the mouse models."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)

    assert "differential_analysis" in caps
    assert "pathway_enrichment" in caps


def test_update_capability_tool_hints_adds_custom_tool(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    updated = update_capability_tool_hints(
        catalog,
        capability_ids=["differential_analysis"],
        tool_hints=["newdifftool"],
        plan_signals=["run_newdifftool.sh"],
    )

    diff_cap = None
    for cap in updated.get("capabilities", []):
        if str(cap.get("id", "")) == "differential_analysis":
            diff_cap = cap
            break
    assert diff_cap is not None
    assert "newdifftool" in diff_cap.get("tool_hints", [])
    assert "run_newdifftool.sh" in diff_cap.get("plan_signals", [])


def test_infer_curated_tool_capabilities(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = (
        "Plan uses salmon_quant for transcript quantification, then deseq2_run for differential expression, "
        "and finally macs2_atacseq_callpeak for accessibility peaks."
    )
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "quantification" in caps
    assert "differential_analysis" in caps
    assert "atacseq_analysis" in caps
    assert "salmon_quant" in hints
    assert "deseq2_run" in hints
    assert "macs2_atacseq_callpeak" in hints


def test_wrapped_scientific_tools_are_available_as_direct_capability_hints(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = (
        "Run fastqc_run on the staged FASTQs, align with bowtie2_align, call somatic variants with "
        "gatk_mutect2_call, detect splicing with rmats_run, and annotate proteins with prodigal_annotate."
    )
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "fastqc" in caps
    assert "alignment" in caps
    assert "variant_calling" in caps
    assert "splicing_analysis" in caps
    assert "annotation" in caps
    assert "fastqc_run" in hints
    assert "bowtie2_align" in hints
    assert "gatk_mutect2_call" in hints
    assert "rmats_run" in hints
    assert "prodigal_annotate" in hints


def test_common_scientific_catalog_tools_infer_capabilities_and_hints(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = (
        "Profile the metagenome with MetaPhlAn, annotate variants with ANNOVAR, quantify transcripts with RSEM, "
        "detect fusions with Arriba, and infer the phylogeny with MAFFT and RAxML-NG."
    )
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "metagenomics_profiling" in caps
    assert "annotation" in caps
    assert "quantification" in caps
    assert "fusion_detection" in caps
    assert "phylogenetics" in caps
    assert "metaphlan" in hints
    assert "annovar" in hints
    assert "rsem" in hints
    assert "arriba" in hints
    assert "mafft" in hints
    assert "raxml-ng" in hints


def test_stringtie_infers_quantification_capability_from_text(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Use StringTie to quantify transcript expression from aligned RNA-seq BAM files with a reference GTF."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "quantification" in caps
    assert "stringtie" in hints


def test_sniffles_infers_structural_variant_capability_from_text(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Use Sniffles to call structural variants from the aligned long-read BAM against the provided reference genome."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "structural_variant_calling" in caps
    assert "sniffles" in hints


def test_bedtools_and_samtools_utilities_infer_interval_and_alignment_qc_capabilities(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = (
        "Use bedtools intersect and bedtools coverage on the peak BED files, then run samtools flagstat "
        "and samtools idxstats on the aligned BAM."
    )
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "interval_operations" in caps
    assert "alignment_qc" in caps
    assert "bedtools intersect" in hints
    assert "samtools flagstat" in hints


def test_skeletal_muscle_prompt_does_not_infer_phylogenetics_or_muscle_tool(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Analyze single-cell RNA-seq data from pre- and post-exercise skeletal muscle samples."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "phylogenetics" not in caps
    assert "muscle" not in hints


def test_muscle_alignment_prompt_still_infers_phylogenetics_and_muscle_tool(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Use MUSCLE for multiple sequence alignment before building a phylogenetic tree with IQ-TREE."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "phylogenetics" in caps
    assert "muscle" in hints


def test_infer_alignment_variant_and_single_cell_tool_hints(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = (
        "Run star_2pass_align then gatk_haplotypecaller on the BAM, and use star_solo_count for single-cell counting."
    )
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "alignment" in caps
    assert "variant_calling" in caps
    assert "single_cell_analysis" in caps
    assert "star_2pass_align" in hints
    assert "gatk_haplotypecaller" in hints
    assert "star_solo_count" in hints


def test_catalog_migration_adds_end_to_end_single_cell_signals(tmp_path):
    import json

    catalog_path = tmp_path / "catalog.json"
    old_catalog = {
        "version": 1,
        "capabilities": [
            {
                "id": "alignment",
                "name": "Alignment",
                "enabled": True,
                "keywords": ["alignment"],
                "plan_signals": ["star", "star_align"],
                "tool_hints": ["star", "star_align"],
            },
            {
                "id": "single_cell_analysis",
                "name": "Single-Cell Analysis",
                "enabled": True,
                "keywords": ["single-cell", "cellranger"],
                "plan_signals": ["single-cell", "cellranger_count"],
                "tool_hints": ["cellranger_count"],
            },
        ],
    }
    catalog_path.write_text(json.dumps(old_catalog), encoding="utf-8")

    catalog = load_capability_catalog(catalog_path)

    alignment = next(cap for cap in catalog["capabilities"] if cap["id"] == "alignment")
    single_cell = next(cap for cap in catalog["capabilities"] if cap["id"] == "single_cell_analysis")

    assert "sc_count_and_cluster" in alignment["plan_signals"]
    assert "cellranger_count" in alignment["plan_signals"]
    assert "sc_count_and_cluster" in alignment["tool_hints"]
    assert "sc_count_and_cluster" in single_cell["keywords"]
    assert "sc_count_and_cluster" in single_cell["plan_signals"]
    assert "sc_count_and_cluster" in single_cell["tool_hints"]


def test_infer_uncommon_capability_domains(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = (
        "Run Bismark methylation analysis, profile metagenomics with Kraken2/Bracken, "
        "detect fusions with STAR-Fusion, and infer a phylogenetic tree with IQ-TREE."
    )
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)
    hints = infer_tool_hints_from_text(text, catalog, enabled_only=True)

    assert "methylation_analysis" in caps
    assert "metagenomics_profiling" in caps
    assert "fusion_detection" in caps
    assert "phylogenetics" in caps
    assert "bismark" in hints
    assert "kraken2" in hints


def test_protein_phylogenetics_does_not_infer_protein_analysis(tmp_path):
    """Bare 'protein' in phylogenetics context must NOT trigger protein_analysis.

    Phylogenetics prompts like 'protein sequences for phylogenetics' should NOT
    infer protein_analysis — only specific phrases like 'protein analysis' should.
    """
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Build a phylogenetic tree from the protein sequences using IQ-TREE with 1000 bootstrap replicates."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)

    assert "phylogenetics" in caps, f"Expected phylogenetics, got {caps}"
    assert "protein_analysis" not in caps, (
        f"Bare 'protein' in phylogenetics context should not trigger protein_analysis, got: {caps}"
    )


def test_explicit_protein_analysis_still_inferred(tmp_path):
    """Explicit protein analysis phrases should still trigger protein_analysis."""
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Run protein analysis using blastp to identify homologs."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)

    assert "protein_analysis" in caps, f"Expected protein_analysis for explicit phrase, got {caps}"


def test_blastp_still_triggers_protein_analysis(tmp_path):
    """Tool-specific keywords like 'blastp' should still trigger protein_analysis."""
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    text = "Search for homologs with blastp against the reference database."
    caps = infer_capabilities_from_text(text, catalog, enabled_only=True)

    assert "protein_analysis" in caps, f"Expected protein_analysis for blastp, got {caps}"


def test_annotation_plan_signals_no_bare_protein(tmp_path):
    """annotation capability's plan_signals must not include bare 'protein'."""
    catalog_path = tmp_path / "catalog.json"
    catalog = load_capability_catalog(catalog_path)

    for cap in catalog.get("capabilities", []):
        if cap.get("id") == "annotation":
            signals = cap.get("plan_signals", [])
            assert "protein" not in signals, f"Bare 'protein' still in annotation plan_signals: {signals}"
            break


def test_catalog_migration_removes_bare_protein_from_existing_file(tmp_path):
    """Loading an old catalog with bare 'protein' keyword should auto-migrate."""
    import json
    catalog_path = tmp_path / "catalog.json"
    # Simulate an old catalog file with bare "protein" keyword.
    old_catalog = {
        "version": 1,
        "capabilities": [
            {
                "id": "protein_analysis",
                "name": "Protein Analysis",
                "enabled": True,
                "keywords": ["protein", "proteomics", "blastp"],
                "plan_signals": ["protein", "blastp"],
                "tool_hints": ["blastp"],
            },
        ],
    }
    catalog_path.write_text(json.dumps(old_catalog), encoding="utf-8")

    catalog = load_capability_catalog(catalog_path)

    for cap in catalog.get("capabilities", []):
        if cap.get("id") == "protein_analysis":
            kw = cap.get("keywords", [])
            assert "protein" not in kw, f"Bare 'protein' not migrated in keywords: {kw}"
            assert "protein analysis" in kw, f"Missing 'protein analysis' after migration: {kw}"
            ps = cap.get("plan_signals", [])
            assert "protein" not in ps, f"Bare 'protein' not migrated in plan_signals: {ps}"
            assert "protein_analysis" in ps, f"Missing 'protein_analysis' after migration: {ps}"
            break
