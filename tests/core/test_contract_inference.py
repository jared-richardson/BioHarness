from __future__ import annotations

from pathlib import Path

from bio_harness.core import tool_env
from bio_harness.harness.config import CAPABILITY_CATALOG_PATH
from bio_harness.harness.contract_utils import (
    _infer_request_contract,
    _missing_exec_tools_for_plan,
    _repair_requested_references_and_index_bases_in_plan,
    _verify_run_outputs,
)
from bio_harness.harness.plan_repair import _repair_metagenomics_prebuilt_db_bindings
from bio_harness.core.capability_catalog import load_capability_catalog


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_direct_skill_smoke_contract_requires_only_requested_tool() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "This is a direct one-step skill smoke test. Use only the freebayes_call tool "
            "to call variants from sample.bam against scaffolds.fasta and write variants/anc_raw.vcf."
        ),
        catalog,
    )

    assert contract["must_include_capabilities"] == []
    assert "freebayes_call" in contract["explicit_tool_hints"]
    assert contract["required_tool_hints"] == ["freebayes_call"]


def test_request_contract_normalizes_bedtools_and_samtools_command_phrases() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Please use bedtools intersect on peaks_a.bed and peaks_b.bed, then use samtools flagstat "
            "on aligned/sample.bam to summarize the alignment."
        ),
        catalog,
    )

    assert "interval_operations" in contract["must_include_capabilities"]
    assert "alignment_qc" in contract["must_include_capabilities"]
    assert "bedtools_intersect" in contract["explicit_tool_hints"]
    assert "samtools_flagstat" in contract["explicit_tool_hints"]
    assert "bedtools_intersect" in contract["required_tool_hints"]
    assert "samtools_flagstat" in contract["required_tool_hints"]


def test_request_contract_ignores_generic_benchmark_references_for_non_reference_task() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Perform a comparative differential expression analysis of the 5xFAD, 3xTG-AD, and PS3O1S "
            "Alzheimer's mouse models to identify shared molecular KEGG pathways. Use the provided count "
            "matrices and differential-expression table, and produce the shared-pathway comparison CSV in "
            "the requested schema. Do not write anywhere outside the current run directory except reading "
            "the provided local benchmark inputs and references."
        ),
        catalog,
    )

    assert "reference_inputs" not in contract["must_include_capabilities"]
    assert "differential_analysis" in contract["must_include_capabilities"]


def test_request_contract_preserves_reference_inputs_for_true_reference_task() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        "Perform transcript quantification on the provided paired-end RNA-seq reads using the transcriptome reference.",
        catalog,
    )

    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "quantification" in contract["must_include_capabilities"]


def test_request_contract_does_not_require_alignment_for_explicit_stringtie_on_aligned_bam() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Run stringtie_quant on /tmp/hnrnpc/sample.bam with annotation /tmp/hnrnpc/genes.gtf. "
            "Use the provided aligned BAM and annotation directly."
        ),
        catalog,
    )

    assert "quantification" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert "stringtie_quant" in contract["explicit_tool_hints"]


def test_request_contract_maps_stringtie_tool_name_to_stringtie_quant() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Proceed. Use aligned BAM /tmp/hnrnpc/sample.bam with StringTie and annotation "
            "/tmp/hnrnpc/genes.gtf to quantify transcripts."
        ),
        catalog,
    )

    assert "quantification" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert "stringtie_quant" in contract["explicit_tool_hints"]
    assert "stringtie" not in contract["explicit_tool_hints"]


def test_request_contract_locks_stringtie_for_aligned_bam_quantification_without_tool_name() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Proceed with execution now. I have an aligned BAM already. Quantify transcripts from "
            "/tmp/hnrnpc/sample.bam using /tmp/hnrnpc/genes.gtf. Write the assembled transcript GTF "
            "and gene abundance table in the current run directory."
        ),
        catalog,
    )

    assert set(contract["must_include_capabilities"]) == {"quantification", "reference_inputs"}
    assert contract["explicit_tool_hints"] == ["stringtie_quant"]
    assert contract["required_tool_hints"] == ["stringtie_quant"]


def test_request_contract_does_not_treat_minimap2_asm5_alignment_as_genome_assembly() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Align the orangutan mitochondrial genome at /tmp/longread/MT-orang.fa to the human "
            "mitochondrial reference at /tmp/longread/MT-human.fa using minimap2_align with preset "
            "asm5 (assembly-to-assembly). Write the aligned output to /tmp/longread/aligned/orang_vs_human.sam."
        ),
        catalog,
    )

    assert "minimap2_align" in contract["explicit_tool_hints"]
    assert "minimap2_align" in contract["required_tool_hints"]
    assert "alignment" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "genome_assembly" not in contract["must_include_capabilities"]


def test_request_contract_does_not_treat_svs_token_as_group_comparison() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Detect structural variants from the long reads at /tmp/sv/sample_reads.fastq using the "
            "reference at /tmp/sv/ref_small.fa. Align with minimap2_align using map-ont preset, then "
            "call SVs with sniffles_sv_call and write /tmp/sv/structural_variants.vcf."
        ),
        catalog,
    )

    assert "minimap2_align" in contract["explicit_tool_hints"]
    assert "sniffles_sv_call" in contract["explicit_tool_hints"]
    assert "alignment" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "structural_variant_calling" in contract["must_include_capabilities"]
    assert "group_comparison" not in contract["must_include_capabilities"]


def test_request_contract_detects_noisy_long_read_structural_change_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "I have some long sequencing reads and a reference genome. Can you figure out if there are any "
            "big structural changes in my sample compared to the reference?"
        ),
        catalog,
    )

    assert "structural_variant_calling" in contract["must_include_capabilities"]
    assert "alignment" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]


def test_request_contract_detects_spatial_transcriptomics_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Analyze this Visium spatial transcriptomics dataset in h5ad format. "
            "Identify spatial domains and marker genes for each domain."
        ),
        catalog,
    )

    assert "spatial_transcriptomics" in contract["must_include_capabilities"]
    assert "spatial_transcriptomics_workflow" in contract["explicit_tool_hints"]
    assert "spatial_transcriptomics_workflow" in contract["required_tool_hints"]


def test_request_contract_detects_proteomics_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Perform differential protein abundance analysis comparing control "
            "vs treatment conditions using the provided abundance matrix and metadata."
        ),
        catalog,
    )

    assert "proteomics" in contract["must_include_capabilities"]
    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "group_comparison" in contract["must_include_capabilities"]
    assert "protein_analysis" not in contract["must_include_capabilities"]
    assert "proteomics_diff_abundance" in contract["explicit_tool_hints"]
    assert "proteomics_diff_abundance" in contract["required_tool_hints"]


def test_request_contract_detects_metabolomics_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Perform differential metabolite analysis comparing control vs treatment "
            "using the provided feature table and metadata."
        ),
        catalog,
    )

    assert "metabolomics" in contract["must_include_capabilities"]
    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "group_comparison" in contract["must_include_capabilities"]
    assert "metabolomics_diff_abundance" in contract["explicit_tool_hints"]
    assert "metabolomics_diff_abundance" in contract["required_tool_hints"]


def test_request_contract_long_read_rna_without_annotation_drops_unsupported_caps() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference genome and quantify "
            "transcript isoforms. No annotation file is provided."
        ),
        catalog,
    )

    assert "alignment" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "annotation" not in contract["must_include_capabilities"]
    assert "quantification" not in contract["must_include_capabilities"]
    assert "annotation_limited_long_read_rna" in contract["downstream_capability_hints"]


def test_request_contract_long_read_rna_with_annotation_requires_stringtie_quant() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference genome "
            "using the provided annotation and quantify transcript isoforms."
        ),
        catalog,
    )

    assert "minimap2_align" in contract["explicit_tool_hints"]
    assert "stringtie_quant" in contract["explicit_tool_hints"]
    assert "minimap2_align" in contract["required_tool_hints"]
    assert "stringtie_quant" in contract["required_tool_hints"]


def test_request_contract_does_not_treat_quality_control_as_group_comparison() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Run quality control on /tmp/qc/reads_R1.fastq.gz using fastqc_run. "
            "Write the report to /tmp/qc_out/."
        ),
        catalog,
    )

    assert "fastqc_run" in contract["required_tool_hints"]
    assert "fastqc" in contract["must_include_capabilities"]
    assert "group_comparison" not in contract["must_include_capabilities"]


def test_request_contract_does_not_require_fastqc_for_fastp_qc_report_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Trim adapters and low-quality bases from /tmp/reads_R1.fastq.gz and "
            "/tmp/reads_R2.fastq.gz using fastp_run. Write trimmed reads and QC "
            "report to /tmp/trimmed/."
        ),
        catalog,
    )

    assert "fastp_run" in contract["required_tool_hints"]
    assert "fastqc" not in contract["must_include_capabilities"]


def test_request_contract_treats_completed_fastqc_report_bundle_as_run_reporting() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Proceed with execution now. Build a MultiQC report bundle from the completed FastQC outputs "
            "in /tmp/run_001 and keep all generated files in the current run directory."
        ),
        catalog,
    )

    assert contract["must_include_capabilities"] == ["run_reporting"]
    assert contract["explicit_tool_hints"] == ["multiqc_report"]
    assert contract["required_tool_hints"] == ["multiqc_report"]
    assert "fastqc" not in contract["must_include_capabilities"]


def test_request_contract_locks_deseq2_for_group_comparison_count_matrix_prompt_with_negated_featurecounts() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Proceed with execution now. I already have a counts matrix and sample metadata. "
            "Compare dex-treated versus untreated airway samples using /tmp/airway_counts.tsv "
            "and /tmp/airway_metadata_dex.tsv. Do not rerun alignment or featureCounts; "
            "use the provided matrix and write the final DE results in the current run directory."
        ),
        catalog,
    )

    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "group_comparison" in contract["must_include_capabilities"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert contract["explicit_tool_hints"] == ["deseq2_run"]
    assert contract["required_tool_hints"] == ["deseq2_run"]
    assert contract["blocked_tool_hints"] == ["featurecounts"]


def test_request_contract_does_not_require_annotation_for_sc_count_with_gtf_input() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Perform single-cell analysis from raw 10X FASTQs at /tmp/sc/sample_R1.fastq.gz "
            "and /tmp/sc/sample_R2.fastq.gz using the genome at /tmp/sc/genome.fa and "
            "annotation /tmp/sc/genes.gtf. Use sc_count_and_cluster and write results "
            "to /tmp/sc_raw/."
        ),
        catalog,
    )

    assert "sc_count_and_cluster" in contract["required_tool_hints"]
    assert "annotation" not in contract["must_include_capabilities"]


def test_request_contract_blocks_negated_salmon_and_kallisto_for_stringtie_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "This is transcript quantification, but do not pseudoalign with Salmon or Kallisto. "
            "Use stringtie_quant on the existing BAM /tmp/hnrnpc/sample.bam with GTF /tmp/hnrnpc/genes.gtf "
            "and write outputs under /tmp/stringtie."
        ),
        catalog,
    )

    assert contract["must_include_capabilities"] == ["quantification", "reference_inputs"]
    assert contract["explicit_tool_hints"] == ["stringtie_quant"]
    assert contract["required_tool_hints"] == ["stringtie_quant"]
    assert "salmon" in contract["blocked_tool_hints"]
    assert "kallisto" in contract["blocked_tool_hints"]


def test_request_contract_treats_downstream_de_context_as_advisory_for_stringtie_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Run stringtie_quant on /tmp/hnrnpc/sample.bam with annotation /tmp/hnrnpc/genes.gtf "
            "to quantify transcripts before downstream differential expression later."
        ),
        catalog,
    )

    assert "quantification" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "differential_analysis" not in contract["must_include_capabilities"]
    assert "group_comparison" not in contract["must_include_capabilities"]
    assert contract["downstream_capability_hints"] == [
        "differential_analysis",
        "group_comparison",
    ]
    assert contract["required_tool_hints"] == ["stringtie_quant"]


def test_request_contract_prefers_deseq2_for_count_matrix_metadata_de_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Proceed. Use counts table /tmp/airway/airway_counts.tsv and metadata "
            "/tmp/airway/airway_metadata.tsv to run differential expression for dex."
        ),
        catalog,
    )

    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "deseq2_run" in contract["explicit_tool_hints"]
    assert "deseq2_run" in contract["required_tool_hints"]
    assert "dexseq_run" not in contract["explicit_tool_hints"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert "reference_inputs" not in contract["must_include_capabilities"]


def test_request_contract_strips_negated_alignment_for_direct_deseq_wrapper_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Use only the deseq2_run tool on /tmp/airway/counts.tsv with metadata /tmp/airway/meta.tsv. "
            "Do not align reads, build counts, or use bash_run."
        ),
        catalog,
    )

    assert "deseq2_run" in contract["required_tool_hints"]
    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "alignment" not in contract["must_include_capabilities"]


def test_request_contract_strips_upstream_caps_for_noisy_direct_deseq_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Do not align FASTQs or build counts from reads. I want DESeq2 directly on "
            "/tmp/airway/airway_counts.tsv with metadata /tmp/airway/airway_metadata_dex.tsv "
            "using the design formula ~ dex. Keep the workflow on deseq2_run and write the final "
            "CSV to /tmp/airway/final/deseq_results.csv."
        ),
        catalog,
    )

    assert "deseq2_run" in contract["explicit_tool_hints"]
    assert "deseq2_run" in contract["required_tool_hints"]
    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert "quantification" not in contract["must_include_capabilities"]
    assert contract["required_output_paths"] == [
        "/tmp/airway/final/deseq_results.csv",
    ]


def test_request_contract_collects_intermediate_and_final_output_paths() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Use only the deseq2_run tool on /tmp/airway/counts.tsv with metadata "
            "/tmp/airway/meta.tsv. Write intermediate outputs under /tmp/airway/deseq_out "
            "and write the final CSV to /tmp/airway/final/deseq_results.csv."
        ),
        catalog,
    )

    assert contract["required_output_paths"] == [
        "/tmp/airway/deseq_out",
        "/tmp/airway/final/deseq_results.csv",
    ]


def test_request_contract_collects_multiple_stringtie_output_files() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Keep this on stringtie_quant and write outputs to "
            "/tmp/stringtie/assembled.gtf and /tmp/stringtie/gene_abundances.tsv."
        ),
        catalog,
    )

    assert contract["required_output_paths"] == [
        "/tmp/stringtie/assembled.gtf",
        "/tmp/stringtie/gene_abundances.tsv",
    ]


def test_request_contract_collects_labeled_stringtie_output_files() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Use only the stringtie_quant tool on /tmp/sample.bam with /tmp/genes.gtf. "
            "Write the assembled transcript GTF to /tmp/stringtie/assembled.gtf "
            "and the gene abundance table to /tmp/stringtie/gene_abundances.tsv."
        ),
        catalog,
    )

    assert contract["required_output_paths"] == [
        "/tmp/stringtie/assembled.gtf",
        "/tmp/stringtie/gene_abundances.tsv",
    ]


def test_request_contract_collects_put_outputs_in_directory() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Use stringtie_quant on /tmp/sample.bam with /tmp/genes.gtf. "
            "Put outputs in /tmp/custom_stringtie/output_set."
        ),
        catalog,
    )

    assert contract["required_output_paths"] == ["/tmp/custom_stringtie/output_set"]


def test_request_contract_collects_minimal_scanpy_output_directory() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        "scanpy_workflow on /tmp/pbmc3k_processed.h5ad output /tmp/scanpy_output only",
        catalog,
    )

    assert contract["required_output_paths"] == ["/tmp/scanpy_output"]


def test_request_contract_keeps_precounted_scanpy_prompt_focused_on_single_cell() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Proceed with execution now. Please do not look for FASTQ files or build a count matrix. "
            "Use the processed h5ad file at /tmp/pbmc3k_processed.h5ad and run the normal Scanpy workflow on it."
        ),
        catalog,
    )

    assert "single_cell_analysis" in contract["must_include_capabilities"]
    assert "differential_analysis" not in contract["must_include_capabilities"]
    assert "variant_calling" not in contract["must_include_capabilities"]
    assert "group_comparison" not in contract["must_include_capabilities"]
    assert "scanpy_workflow" in contract["explicit_tool_hints"]


def test_request_contract_strips_alignment_and_reference_for_explicit_scanpy_wrapper_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Use only the scanpy_workflow tool on the processed AnnData file at "
            "/tmp/pbmc3k_processed.h5ad. Do not add FASTQ processing or count matrix generation."
        ),
        catalog,
    )

    assert "scanpy_workflow" in contract["required_tool_hints"]
    assert "single_cell_analysis" in contract["must_include_capabilities"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert "quantification" not in contract["must_include_capabilities"]
    assert "reference_inputs" not in contract["must_include_capabilities"]


def test_request_contract_blocks_negated_tool_mentions_for_scanpy_adversarial_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "I need differential expression analysis. Use scanpy_workflow on the processed h5ad at "
            "/tmp/pbmc3k_processed.h5ad. Write outputs under /tmp/scanpy_output. "
            "Do not use DESeq2, edgeR, or bash_run."
        ),
        catalog,
    )

    assert contract["required_tool_hints"] == ["scanpy_workflow"]
    assert contract["explicit_tool_hints"] == ["scanpy_workflow"]
    assert "deseq2_run" in contract["blocked_tool_hints"]
    assert "edger_run" in contract["blocked_tool_hints"]


def test_request_contract_blocks_negated_switch_target_for_scanpy_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "This is single-cell work, but do not switch to Seurat. "
            "Keep the workflow on scanpy_workflow for the processed AnnData file "
            "at /tmp/pbmc3k_processed.h5ad and write outputs under /tmp/scanpy_output only."
        ),
        catalog,
    )

    assert contract["required_tool_hints"] == ["scanpy_workflow"]
    assert contract["explicit_tool_hints"] == ["scanpy_workflow"]
    assert "seurat" in contract["blocked_tool_hints"]


def test_request_contract_excludes_negated_featurecounts_for_noisy_deseq_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "I already have the count matrix and metadata for dex, so do not align reads, "
            "do not run featureCounts, and do not invent sample groups from filenames. "
            "Use DESeq2 on /tmp/airway_counts.tsv with /tmp/airway_metadata_dex.tsv."
        ),
        catalog,
    )

    assert contract["required_tool_hints"] == ["deseq2_run"]
    assert contract["explicit_tool_hints"] == ["deseq2_run"]
    assert "featurecounts" in contract["blocked_tool_hints"]


def test_request_contract_does_not_infer_single_cell_for_star_fusion_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        "Detect fusions using STAR-Fusion.",
        catalog,
    )

    assert "fusion_detection" in contract["must_include_capabilities"]
    assert "single_cell_analysis" not in contract["must_include_capabilities"]
    assert "star-fusion" in contract["required_tool_hints"]


def test_request_contract_requires_explicit_edger_wrapper_and_output_root() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Run edgeR, not DESeq2, differential expression on /tmp/airway_counts.tsv "
            "with metadata /tmp/airway_metadata_dex.tsv using design ~ dex and contrast "
            "dex,trt,untrt. Write results under /tmp/edger_results and do not use bash_run."
        ),
        catalog,
    )

    assert contract["explicit_tool_hints"] == ["edger_run"]
    assert contract["required_tool_hints"] == ["edger_run"]
    assert contract["required_output_paths"] == ["/tmp/edger_results"]
    assert "deseq2_run" in contract["blocked_tool_hints"]
    assert "alignment" not in contract["must_include_capabilities"]
    assert "reference_inputs" not in contract["must_include_capabilities"]


def test_request_contract_requires_explicit_limma_wrapper_and_output_root() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Use limma_voom_run on /tmp/airway_counts.tsv with metadata /tmp/airway_metadata_dex.tsv, "
            "design ~ dex, contrast dex,trt,untrt, and output to /tmp/limma_results/."
        ),
        catalog,
    )

    assert contract["explicit_tool_hints"] == ["limma_voom_run"]
    assert contract["required_tool_hints"] == ["limma_voom_run"]
    assert contract["required_output_paths"] == ["/tmp/limma_results/"]


def test_request_contract_requires_explicit_run_verb_for_direct_deseq_wrapper() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Run deseq2_run directly on /tmp/airway_counts.tsv with /tmp/airway_metadata_dex.tsv "
            "for dex, keep intermediate outputs under /tmp/deseq_work, and write the final CSV "
            "to /tmp/final_result.csv."
        ),
        catalog,
    )

    assert contract["explicit_tool_hints"] == ["deseq2_run"]
    assert contract["required_tool_hints"] == ["deseq2_run"]
    assert contract["required_output_paths"] == [
        "/tmp/deseq_work",
        "/tmp/final_result.csv",
    ]


def test_request_contract_only_adds_group_comparison_for_tumor_normal_pairing() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        "Run the normal Scanpy workflow on a processed h5ad file.",
        catalog,
    )

    assert "variant_calling" not in contract["must_include_capabilities"]
    assert "group_comparison" not in contract["must_include_capabilities"]


def test_request_contract_keeps_group_comparison_for_tumor_vs_normal_variant_request() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        "Call somatic variants in tumor vs normal samples using Mutect2.",
        catalog,
    )

    assert "variant_calling" in contract["must_include_capabilities"]
    assert "group_comparison" in contract["must_include_capabilities"]


def test_request_contract_still_requires_alignment_for_alignment_request() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        "Align paired-end reads to the reference genome and produce a sorted BAM.",
        catalog,
    )

    assert "alignment" in contract["must_include_capabilities"]


def test_request_contract_keeps_alignment_and_reference_for_raw_rnaseq_de_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Run RNA-seq differential expression between treatment and control using the paired-end reads "
            "sample_R1.fastq.gz and sample_R2.fastq.gz against the provided reference genome and annotation."
        ),
        catalog,
    )

    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "alignment" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "annotation" not in contract["must_include_capabilities"]


def test_request_contract_detects_differentially_expressed_raw_rnaseq_prompt() -> None:
    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Identify differentially expressed genes between planktonic and biofilm "
            "conditions using tiny RNA-seq reads, reference genome, annotation, "
            "and sample metadata."
        ),
        catalog,
    )

    assert "differential_analysis" in contract["must_include_capabilities"]
    assert "alignment" in contract["must_include_capabilities"]
    assert "reference_inputs" in contract["must_include_capabilities"]
    assert "annotation" not in contract["must_include_capabilities"]


def test_direct_skill_smoke_preserves_explicit_reference_paths(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    requested_reference = tmp_path / "source_aliases" / "scaffolds.fasta"
    requested_reference.parent.mkdir(parents=True, exist_ok=True)
    requested_reference.write_text(">chr1\nACGT\n", encoding="utf-8")
    requested_bam = tmp_path / "source_aliases" / "anc_aligned.bam"
    requested_bam.write_text("bam", encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "freebayes_call",
                "arguments": {
                    "input_bam": str(requested_bam),
                    "reference_fasta": str(requested_reference),
                    "output_vcf": str(selected_dir / "variants" / "anc_raw.vcf"),
                },
            }
        ]
    }
    repaired, meta = _repair_requested_references_and_index_bases_in_plan(
        plan,
        selected_dir,
        data_root,
        (
            "This is a direct one-step skill smoke test. Use only the freebayes_call tool "
            f"on {requested_bam} against {requested_reference}."
        ),
    )

    assert repaired == plan
    assert meta["changed"] is False
    assert meta["why"] == "direct_skill_smoke_preserves_explicit_requested_paths"


def test_direct_skill_smoke_preserves_explicit_metagenomics_database_paths(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    data_root = tmp_path / "inputs_readonly"
    selected_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    explicit_database = selected_dir / "kraken_db"
    reference_fasta = tmp_path / "source_aliases" / "reference.fa"
    taxonomy_names = tmp_path / "source_aliases" / "names.dmp"
    taxonomy_nodes = tmp_path / "source_aliases" / "nodes.dmp"
    reads_1 = tmp_path / "source_aliases" / "reads_R1.fastq"
    reads_2 = tmp_path / "source_aliases" / "reads_R2.fastq"
    for path, content in (
        (reference_fasta, ">ecoli|kraken:taxid|562\nACGT\n"),
        (taxonomy_names, "1\t|\troot\t|\t\t|\tscientific name\t|\n"),
        (taxonomy_nodes, "1\t|\t1\t|\tno rank\t|\n"),
        (reads_1, "@r1/1\nACGT\n+\nIIII\n"),
        (reads_2, "@r1/2\nACGT\n+\nIIII\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    plan = {
        "plan": [
            {
                "tool_name": "metagenomics_kraken2_bracken_style",
                "arguments": {
                    "database": str(explicit_database),
                    "reference_fasta": str(reference_fasta),
                    "taxonomy_names": str(taxonomy_names),
                    "taxonomy_nodes": str(taxonomy_nodes),
                    "reads_1": str(reads_1),
                    "reads_2": str(reads_2),
                    "output_dir": str(selected_dir / "metagenomics"),
                    "output_report": str(selected_dir / "metagenomics" / "bracken.tsv"),
                },
            }
        ]
    }

    repaired, meta = _repair_metagenomics_prebuilt_db_bindings(
        plan,
        selected_dir=selected_dir,
        data_root=data_root,
        analysis_spec={"analysis_type": "direct_skill_smoke"},
        request_text=(
            "This is a direct one-step skill smoke test. Use only the "
            "metagenomics_kraken2_bracken_style tool."
        ),
    )

    assert repaired == plan
    assert meta["changed"] is False
    assert meta["why"] == "direct_skill_smoke_preserves_explicit_database_paths"


def test_missing_exec_tools_for_plan_uses_shared_requirement_resolution(monkeypatch) -> None:
    seen: list[str] = []

    def _fake_requirement(name: str) -> bool:
        seen.append(name)
        return name == "featureCounts"

    monkeypatch.setattr("bio_harness.harness.contract_utils.requirement_available", _fake_requirement)

    missing = _missing_exec_tools_for_plan(
        {
            "plan": [
                {
                    "tool_name": "featurecounts_run",
                    "arguments": {"input_bams": ["a.bam"], "annotation_gtf": "genes.gtf", "output_counts": "counts.tsv"},
                }
            ]
        }
    )

    assert missing == []
    assert seen == ["featureCounts"]


def test_missing_exec_tools_for_plan_reads_fake_alignment_extra_sidecar(monkeypatch, tmp_path: Path) -> None:
    _make_executable(tmp_path / ".pixi" / "envs" / "alignment-extra" / "bin" / "bowtie2")

    monkeypatch.setattr(tool_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tool_env.shutil, "which", lambda _name: None)

    missing = _missing_exec_tools_for_plan(
        {
            "plan": [
                {
                    "tool_name": "bowtie2_align",
                    "arguments": {
                        "index_base": "index/genome",
                        "reads_1": "reads_R1.fastq.gz",
                        "reads_2": "reads_R2.fastq.gz",
                        "output_sam": "aligned.sam",
                    },
                }
            ]
        }
    )

    assert missing == []


def test_verify_run_outputs_honors_explicit_rmats_output_dir(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    rmats_out = selected_dir / "rmats_out"
    rmats_out.mkdir(parents=True, exist_ok=True)
    (rmats_out / "SE.MATS.JC.txt").write_text("header\n", encoding="utf-8")

    ok, message = _verify_run_outputs(
        selected_dir,
        {
            "plan": [
                {
                    "tool_name": "rmats_run",
                    "arguments": {
                        "output_dir": str(rmats_out),
                    },
                }
            ]
        },
    )

    assert ok is True
    assert message == ""


def test_verify_run_outputs_rejects_missing_bash_run_output_flag_target(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    selected_dir.mkdir(parents=True, exist_ok=True)

    ok, message = _verify_run_outputs(
        selected_dir,
        {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            "python3 pipeline_scripts/compare_pathways.py "
                            "--input-a inputs/a.tsv "
                            "--output-csv final/pathway_comparison.csv"
                        )
                    },
                }
            ]
        },
    )

    assert ok is False
    assert "Planned outputs were not produced" in message
    assert "pathway_comparison.csv" in message


def test_verify_run_outputs_accepts_present_bash_run_output_flag_target(tmp_path: Path) -> None:
    selected_dir = tmp_path / "selected"
    output_path = selected_dir / "final" / "pathway_comparison.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("pathway,count\nimmune,3\n", encoding="utf-8")

    ok, message = _verify_run_outputs(
        selected_dir,
        {
            "plan": [
                {
                    "tool_name": "bash_run",
                    "arguments": {
                        "command": (
                            "python3 pipeline_scripts/compare_pathways.py "
                            "--input-a inputs/a.tsv "
                            "--output-csv final/pathway_comparison.csv"
                        )
                    },
                }
            ]
        },
    )

    assert ok is True
    assert message == ""


def test_evolution_shared_variant_prompt_requires_full_workflow_capabilities_fix_19() -> None:
    """Fix #19: the evolution benchmark prompt must pull in the full
    assembly -> alignment -> variant_calling -> annotation -> shared_variant_export
    capability chain. Previously only 'reference_inputs' and 'annotation' were
    inferred, which let the stepwise loop declare done after a single
    snpeff_annotate step without producing variants_shared.csv.
    """

    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Identify and annotate genome variants in two evolved lines relative to an ancestor "
            "line of E. coli; report only variants shared by both evolved lines with moderate or "
            "higher predicted severity."
        ),
        catalog,
    )
    caps = contract["must_include_capabilities"]

    for required in (
        "genome_assembly",
        "alignment",
        "variant_calling",
        "annotation",
        "reference_inputs",
        "shared_variant_export",
    ):
        assert required in caps, (required, caps)


def test_non_evolution_variant_prompt_does_not_trigger_shared_variant_export_fix_19() -> None:
    """Fix #19 must remain narrow: a plain variant-annotation prompt without
    shared-evolution context should not add shared_variant_export."""

    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)

    contract = _infer_request_contract(
        (
            "Annotate the provided VCF with SnpEff and report variants with high or moderate impact."
        ),
        catalog,
    )

    assert "shared_variant_export" not in contract["must_include_capabilities"]


def test_evolution_shared_variant_contract_blocks_plan_missing_final_export_fix_19() -> None:
    """Fix #19: a plan that only reaches snpeff_annotate (the shape of the
    exp34 run that falsely returned passed=true) must now fail the contract
    check with missing_capabilities=['shared_variant_export']."""

    from bio_harness.core.contracts import assess_plan_contract

    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)
    prompt = (
        "Identify and annotate genome variants in two evolved lines relative to an ancestor "
        "line of E. coli; report only variants shared by both evolved lines with moderate or "
        "higher predicted severity."
    )
    contract = _infer_request_contract(prompt, catalog)

    plan_without_export = {
        "plan": [
            {"tool_name": "spades_assemble", "arguments": {"output_dir": "assembly"}},
            {"tool_name": "prodigal_annotate", "arguments": {"output_gff": "ref.gff"}},
            {"tool_name": "bwa_mem_align", "arguments": {"reference_fasta": "ref.fa"}},
            {"tool_name": "freebayes_call", "arguments": {"output_vcf": "evol1_raw.vcf"}},
            {"tool_name": "bwa_mem_align", "arguments": {"reference_fasta": "ref.fa"}},
            {"tool_name": "freebayes_call", "arguments": {"output_vcf": "evol2_raw.vcf"}},
            {"tool_name": "bcftools_filter_run", "arguments": {}},
            {"tool_name": "snpeff_annotate", "arguments": {"output_vcf": "evol1.annotated.vcf"}},
        ]
    }

    result = assess_plan_contract(plan_without_export, contract)
    assert result["passed"] is False
    assert "shared_variant_export" in result["missing_capabilities"]


def test_evolution_plan_with_shared_variants_export_run_passes_contract_fix_19() -> None:
    """Fix #19: the same plan plus a final shared_variants_export_run step
    (producing variants_shared.csv) must satisfy the contract."""

    from bio_harness.core.contracts import assess_plan_contract

    catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)
    prompt = (
        "Identify and annotate genome variants in two evolved lines relative to an ancestor "
        "line of E. coli; report only variants shared by both evolved lines with moderate or "
        "higher predicted severity."
    )
    contract = _infer_request_contract(prompt, catalog)

    plan_with_export = {
        "plan": [
            {"tool_name": "spades_assemble", "arguments": {"output_dir": "assembly"}},
            {"tool_name": "bwa_mem_align", "arguments": {"reference_fasta": "ref.fa"}},
            {"tool_name": "freebayes_call", "arguments": {"output_vcf": "evol1_raw.vcf"}},
            {"tool_name": "snpeff_annotate", "arguments": {"output_vcf": "evol1.annotated.vcf"}},
            {
                "tool_name": "shared_variants_export_run",
                "arguments": {
                    "input_vcf_a": "evol1.annotated.vcf",
                    "input_vcf_b": "evol2.annotated.vcf",
                    "output_csv": "final/variants_shared.csv",
                },
            },
        ]
    }

    result = assess_plan_contract(plan_with_export, contract)
    assert result["passed"] is True, result
    assert result["missing_capabilities"] == []
