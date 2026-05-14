from __future__ import annotations

from bio_harness.core.execution_mode import (
    build_execution_contract,
    compatible_tools_for_execution_mode,
    infer_execution_mode,
    infer_input_mode,
)


def test_infer_input_mode_for_processed_single_cell_scanpy_request() -> None:
    mode = infer_input_mode(
        user_query="Use scanpy_workflow on the processed h5ad file at /tmp/pbmc3k_processed.h5ad.",
        analysis_type="single_cell_rna_seq",
        explicit_tools=["scanpy_workflow"],
    )

    assert mode == "processed_single_cell"


def test_infer_input_mode_for_count_matrix_deseq_request() -> None:
    mode = infer_input_mode(
        user_query=(
            "Run deseq2_run on the count matrix /tmp/airway_counts.tsv with "
            "metadata /tmp/airway_metadata_dex.tsv."
        ),
        analysis_type="rna_seq_differential_expression",
        explicit_tools=["deseq2_run"],
    )

    assert mode == "count_matrix"


def test_infer_input_mode_for_proteomics_request() -> None:
    mode = infer_input_mode(
        user_query=(
            "Run proteomics_diff_abundance on the abundance matrix /tmp/abundance_matrix.csv "
            "with metadata /tmp/metadata.csv."
        ),
        analysis_type="proteomics",
        explicit_tools=["proteomics_diff_abundance"],
    )

    assert mode == "count_matrix"


def test_infer_input_mode_for_metabolomics_request() -> None:
    mode = infer_input_mode(
        user_query=(
            "Run metabolomics_diff_abundance on the feature table /tmp/feature_table.csv "
            "with metadata /tmp/metadata.csv."
        ),
        analysis_type="metabolomics",
        explicit_tools=["metabolomics_diff_abundance"],
    )

    assert mode == "count_matrix"


def test_infer_input_mode_for_deseq_request_prefers_raw_reads_over_wrapper_hint() -> None:
    mode = infer_input_mode(
        user_query=(
            "Run deseq2_run on the raw reads sample_R1.fastq.gz and sample_R2.fastq.gz "
            "with metadata /tmp/airway_metadata_dex.tsv."
        ),
        analysis_type="rna_seq_differential_expression",
        explicit_tools=["deseq2_run"],
    )

    assert mode == "raw_fastq"


def test_infer_input_mode_for_rna_seq_de_uses_pipeline_tool_context() -> None:
    mode = infer_input_mode(
        user_query="Identify differentially expressed genes between two conditions using DESeq2.",
        analysis_type="rna_seq_differential_expression",
        explicit_tools=["featurecounts_run", "deseq2_run"],
    )

    assert mode == "raw_fastq"


def test_infer_input_mode_for_deseq_wrapper_alone_does_not_force_count_matrix() -> None:
    mode = infer_input_mode(
        user_query="Identify differentially expressed genes between two conditions using DESeq2.",
        analysis_type="rna_seq_differential_expression",
        explicit_tools=["deseq2_run"],
    )

    assert mode == ""


def test_infer_input_mode_for_rna_seq_de_prefers_discovered_fastq_inputs() -> None:
    mode = infer_input_mode(
        user_query="Identify differentially expressed genes between planktonic and biofilm conditions using DESeq2.",
        analysis_type="rna_seq_differential_expression",
        explicit_tools=["deseq2_run"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert mode == "raw_fastq"


def test_infer_input_mode_for_stringtie_bam_request() -> None:
    mode = infer_input_mode(
        user_query=(
            "Use stringtie_quant with the aligned BAM /tmp/sample.bam and "
            "annotation /tmp/genes.gtf."
        ),
        analysis_type="transcript_quantification",
        explicit_tools=["stringtie_quant"],
    )

    assert mode == "aligned_bam"


def test_compatible_tools_for_processed_single_cell_filters_available_wrappers() -> None:
    tools = compatible_tools_for_execution_mode(
        analysis_type="single_cell_rna_seq",
        input_mode="processed_single_cell",
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster"],
    )

    assert tools == ["scanpy_workflow"]


def test_infer_execution_mode_prefers_compiled_pipeline_for_multistep_method() -> None:
    mode = infer_execution_mode(
        chosen_method="featurecounts_run + deseq2_run",
        input_mode="raw_fastq",
        explicit_execution_intent={},
    )

    assert mode == "compiled_pipeline"


def test_infer_execution_mode_requires_evidence_for_salmon_quant_without_mode() -> None:
    mode = infer_execution_mode(
        chosen_method="salmon_quant",
        input_mode="",
        explicit_execution_intent={},
    )

    assert mode == ""


def test_infer_execution_mode_rejects_incompatible_locked_deseq_tool() -> None:
    mode = infer_execution_mode(
        chosen_method="",
        input_mode="raw_fastq",
        explicit_execution_intent={"locked_tools": ["deseq2_run"]},
    )

    assert mode == ""


def test_compatible_tools_for_raw_fastq_rna_seq_de_omits_direct_deseq_wrapper() -> None:
    tools = compatible_tools_for_execution_mode(
        analysis_type="rna_seq_differential_expression",
        input_mode="raw_fastq",
        available_skill_names=[
            "featurecounts_run",
            "deseq2_run",
            "subread_align",
            "star_align",
            "star_2pass_align",
            "hisat2_align",
        ],
    )

    assert "deseq2_run" not in tools
    assert tools == [
        "subread_align",
        "featurecounts_run",
        "star_align",
        "star_2pass_align",
        "hisat2_align",
    ]


def test_build_execution_contract_rejects_exact_deseq_direct_wrapper_for_fastq_inputs() -> None:
    contract = build_execution_contract(
        analysis_type="rna_seq_differential_expression",
        user_query=(
            "Use only deseq2_run to identify differentially expressed genes between "
            "planktonic and biofilm conditions."
        ),
        chosen_method="deseq2_run",
        contract={},
        explicit_execution_intent={},
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert contract["input_mode"] == "raw_fastq"
    assert contract["execution_mode"] == "compiled_pipeline"
    assert contract["locked_tools"] == []


def test_build_execution_contract_filters_incompatible_locked_tools_for_direct_callers() -> None:
    contract = build_execution_contract(
        analysis_type="rna_seq_differential_expression",
        user_query=(
            "Use only deseq2_run to identify differentially expressed genes between "
            "planktonic and biofilm conditions."
        ),
        chosen_method="",
        contract={},
        explicit_execution_intent={"locked_tools": ["deseq2_run"]},
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert contract["input_mode"] == "raw_fastq"
    assert contract["execution_mode"] == "compiled_pipeline"
    assert contract["locked_tools"] == []
    assert "deseq2_run" not in contract["compatible_tools"]


def test_build_execution_contract_carries_locked_and_blocked_tool_state() -> None:
    contract = build_execution_contract(
        analysis_type="single_cell_rna_seq",
        user_query=(
            "Do not switch to Seurat. Keep the workflow on scanpy_workflow "
            "for /tmp/pbmc3k_processed.h5ad."
        ),
        chosen_method="scanpy_workflow",
        contract={
            "required_tool_hints": ["scanpy_workflow"],
            "blocked_tool_hints": ["seurat"],
        },
        explicit_execution_intent={"locked_tools": ["scanpy_workflow"]},
        available_skill_names=["scanpy_workflow", "seurat_rscript_workflow"],
    )

    assert contract["analysis_family"] == "single_cell_rna_seq"
    assert contract["input_mode"] == "processed_single_cell"
    assert contract["execution_mode"] == "direct_wrapper"
    assert contract["compatible_tools"] == ["scanpy_workflow"]
    assert contract["locked_tools"] == ["scanpy_workflow"]
    assert contract["blocked_tools"] == ["seurat"]


def test_build_execution_contract_narrows_compatible_tools_for_required_direct_wrapper() -> None:
    contract = build_execution_contract(
        analysis_type="rna_seq_differential_expression",
        user_query=(
            "Run edgeR and not DESeq2 on the counts matrix /tmp/airway_counts.tsv "
            "with metadata /tmp/airway_metadata.tsv."
        ),
        chosen_method="",
        contract={
            "required_tool_hints": ["edger_run"],
            "blocked_tool_hints": ["deseq2_run"],
        },
        explicit_execution_intent={},
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run"],
    )

    assert contract["input_mode"] == "count_matrix"
    assert contract["execution_mode"] == "direct_wrapper"
    assert contract["compatible_tools"] == ["edger_run"]
    assert contract["required_tools"] == ["edger_run"]
    assert contract["blocked_tools"] == ["deseq2_run"]


def test_build_execution_contract_for_proteomics_direct_wrapper() -> None:
    contract = build_execution_contract(
        analysis_type="proteomics",
        user_query=(
            "Use only proteomics_diff_abundance on /tmp/abundance_matrix.csv with "
            "/tmp/metadata.csv."
        ),
        chosen_method="proteomics_diff_abundance",
        contract={
            "must_include_capabilities": ["proteomics", "differential_analysis", "group_comparison"],
            "required_tool_hints": ["proteomics_diff_abundance"],
        },
        explicit_execution_intent={"locked_tools": ["proteomics_diff_abundance"]},
        available_skill_names=["proteomics_diff_abundance", "deseq2_run"],
    )

    assert contract["analysis_family"] == "proteomics"
    assert contract["input_mode"] == "count_matrix"
    assert contract["execution_mode"] == "direct_wrapper"
    assert contract["compatible_tools"] == ["proteomics_diff_abundance"]


def test_build_execution_contract_for_metabolomics_direct_wrapper() -> None:
    contract = build_execution_contract(
        analysis_type="metabolomics",
        user_query=(
            "Use only metabolomics_diff_abundance on /tmp/feature_table.csv with "
            "/tmp/metadata.csv."
        ),
        chosen_method="metabolomics_diff_abundance",
        contract={
            "must_include_capabilities": ["metabolomics", "differential_analysis", "group_comparison"],
            "required_tool_hints": ["metabolomics_diff_abundance"],
        },
        explicit_execution_intent={"locked_tools": ["metabolomics_diff_abundance"]},
        available_skill_names=["metabolomics_diff_abundance", "deseq2_run"],
    )

    assert contract["analysis_family"] == "metabolomics"
    assert contract["input_mode"] == "count_matrix"
    assert contract["execution_mode"] == "direct_wrapper"
    assert contract["compatible_tools"] == ["metabolomics_diff_abundance"]


def test_build_execution_contract_for_gatk_direct_wrapper() -> None:
    contract = build_execution_contract(
        analysis_type="germline_variant_calling",
        user_query=(
            "Use only gatk_haplotypecaller on the aligned BAM /tmp/sample.bam "
            "with reference /tmp/ref.fa."
        ),
        chosen_method="gatk_haplotypecaller",
        contract={},
        explicit_execution_intent={"locked_tools": ["gatk_haplotypecaller"]},
        available_skill_names=["gatk_haplotypecaller", "bwa_mem_align"],
    )

    assert contract["input_mode"] == "aligned_bam"
    assert contract["execution_mode"] == "direct_wrapper"
    assert contract["compatible_tools"] == ["gatk_haplotypecaller"]
