from __future__ import annotations

from bio_harness.core.benchmark_policy import BIOAGENTBENCH_PLANNING_STRICT_POLICY
from bio_harness.core.analysis_spec import (
    analysis_spec_preference_profile,
    build_analysis_brief,
    deterministic_analysis_spec,
    discover_data_files,
    infer_analysis_type,
    normalize_analysis_spec,
    should_generate_analysis_review,
)


def test_infer_analysis_type_detects_bacterial_evolution_variant_calling():
    contract = {"must_include_capabilities": ["alignment", "variant_calling", "reference_inputs"]}
    analysis_type = infer_analysis_type(
        "Identify shared variants in evolved bacterial isolates relative to an ancestor.",
        contract,
    )
    assert analysis_type == "bacterial_evolution_variant_calling"


def test_infer_analysis_type_detects_spatial_transcriptomics_from_noisy_prompt() -> None:
    analysis_type = infer_analysis_type(
        "I have some spatial gene expression data from a tissue section. Can you figure out what regions are different and which genes define them?",
        None,
    )
    assert analysis_type == "spatial_transcriptomics"


def test_infer_analysis_type_detects_differentially_expressed_genes() -> None:
    analysis_type = infer_analysis_type(
        "Identify differentially expressed genes between planktonic and biofilm conditions using tiny RNA-seq reads.",
        None,
    )
    assert analysis_type == "rna_seq_differential_expression"


def test_infer_analysis_type_prefers_spatial_over_single_cell_contract_label() -> None:
    analysis_type = infer_analysis_type(
        "Analyze this Visium spatial transcriptomics dataset and identify spatial domains.",
        {"must_include_capabilities": ["single_cell_analysis"]},
    )
    assert analysis_type == "spatial_transcriptomics"


def test_infer_analysis_type_detects_noisy_proteomics_prompt() -> None:
    analysis_type = infer_analysis_type(
        "I have some protein expression data. Can you tell me which proteins are different between my two groups?",
        None,
    )
    assert analysis_type == "proteomics"


def test_infer_analysis_type_detects_noisy_metabolomics_prompt() -> None:
    analysis_type = infer_analysis_type(
        "I ran a mass spec experiment and got this feature table. Which metabolites are changing between conditions?",
        None,
    )
    assert analysis_type == "metabolomics"


def test_infer_analysis_type_prefers_proteomics_over_generic_differential_analysis_contract() -> None:
    analysis_type = infer_analysis_type(
        "Perform differential protein abundance analysis comparing control vs treatment conditions.",
        {"must_include_capabilities": ["differential_analysis", "group_comparison"]},
    )
    assert analysis_type == "proteomics"


def test_infer_analysis_type_prefers_metabolomics_over_generic_differential_analysis_contract() -> None:
    analysis_type = infer_analysis_type(
        "Perform differential metabolite analysis on the feature intensity table comparing control vs treatment.",
        {"must_include_capabilities": ["differential_analysis", "group_comparison"]},
    )
    assert analysis_type == "metabolomics"


def test_deterministic_analysis_spec_prefers_freebayes_for_bacterial_evolution():
    contract = {"must_include_capabilities": ["alignment", "variant_calling", "reference_inputs"]}
    spec = deterministic_analysis_spec(
        "Identify shared variants in evolved bacterial isolates relative to an ancestor.",
        contract=contract,
        available_skill_names=[
            "spades_assemble",
            "bwa_mem_align",
            "freebayes_call",
            "bcftools_call",
            "bcftools_filter_run",
            "bcftools_isec_run",
            "bcftools_norm_run",
            "shared_variants_export_run",
            "snpeff_annotate",
        ],
    )
    assert spec["analysis_type"] == "bacterial_evolution_variant_calling"
    assert spec["chosen_method"] == "freebayes_call"
    assert "freebayes_call" in spec["preferred_tools"]
    assert any(
        entry.get("tool_name") == "freebayes_call" and entry.get("settings", {}).get("ploidy") == 1
        for entry in spec["parameter_profile"]
    )
    assert [entry[0] for entry in spec["plan_skeleton"]] == [
        "spades_assemble",
        "prodigal_annotate",
        "bwa_mem_align",
        "freebayes_call",
        "bwa_mem_align",
        "freebayes_call",
        "bcftools_filter_run",
        "bcftools_isec_run",
        "snpeff_annotate",
        "bcftools_norm_run",
        "shared_variants_export_run",
    ]
    assert "assembled ancestor reference is annotated" in spec["acceptance_checks"][0]
    assert "working reference" in spec["acceptance_checks"][1]
    assert "ancestor-supported variants are removed" in spec["acceptance_checks"][2]
    assert "ancestor subtraction happens on each evolved line" in spec["acceptance_checks"][3]
    assert "exact columns" in spec["acceptance_checks"][-1]
    assert "comparison-ready VCF" in spec["plan_skeleton"][-5][1]
    assert "Subtract the ancestor-supported sites" in spec["plan_skeleton"][-4][1]
    assert "Normalize each annotated evolved callset separately" in spec["plan_skeleton"][-2][1]
    assert "final shared-variant CSV" in spec["plan_skeleton"][-1][1]
    assert any(entry.get("tool_name") == "bcftools_filter_run" for entry in spec["parameter_profile"])
    assert any(
        entry.get("tool_name") == "bcftools_isec_run" and entry.get("settings", {}).get("mode") == "complement"
        for entry in spec["parameter_profile"]
    )
    assert any(
        entry.get("tool_name") == "shared_variants_export_run"
        and entry.get("settings", {}).get("header_case") == "upper"
        for entry in spec["parameter_profile"]
    )
    assert any("reuses one branch's BAM/VCF paths" in trigger for trigger in spec["rerun_triggers"])


def test_analysis_spec_preference_profile_exposes_fallback_preferences():
    profile = analysis_spec_preference_profile(
        {
            "analysis_type": "rna_seq_differential_expression",
            "chosen_method": "featurecounts_run + deseq2_run",
            "preferred_tools": ["featurecounts_run", "deseq2_run"],
            "discouraged_tools": ["bcftools_call"],
            "acceptance_checks": ["metadata rows match count columns"],
        }
    )
    assert profile["analysis_type"] == "rna_seq_differential_expression"
    assert "featurecounts_run" in profile["preferred_tools"]
    assert "bcftools_call" in profile["discouraged_tools"]
    assert "differential_expression_deseq2" in profile["preferred_pipeline_ids"]


def test_discover_data_files_finds_supported_bioinformatics_inputs(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "ex1.eff.vcf").write_text("##fileformat=VCFv4.2\n")
    (data_root / "family_description.txt").write_text("pedigree\n")
    (data_root / ".hidden.vcf").write_text("##hidden\n")

    discovered = discover_data_files(data_root)

    assert discovered == [
        {"name": "ex1.eff.vcf", "path": str((data_root / "ex1.eff.vcf").resolve(strict=False))},
        {"name": "family_description.txt", "path": str((data_root / "family_description.txt").resolve(strict=False))},
    ]


def test_should_generate_analysis_review_for_quantification_request():
    contract = {"must_include_capabilities": ["quantification"]}
    assert should_generate_analysis_review("Quantify transcripts with salmon.", contract) is True


def test_normalize_analysis_spec_prefers_grounded_variant_caller_for_shared_evolution():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "chosen_method": "spades_assemble",
            "preferred_tools": ["spades_assemble", "freebayes_call", "snpeff_annotate"],
            "protocol_grounding": {
                "required_tools": ["spades_assemble", "freebayes_call", "snpeff_annotate"],
                "preferred_tools": ["freebayes_call", "snpeff_annotate"],
                "requires_shared_comparison": True,
                "min_variant_branches": 2,
            },
        },
        user_query="Identify shared variants in evolved bacterial isolates relative to an ancestor.",
        contract={"must_include_capabilities": ["variant_calling"]},
        available_skill_names=["spades_assemble", "freebayes_call", "snpeff_annotate"],
    )

    assert spec["chosen_method"] == "freebayes_call"


def test_normalize_analysis_spec_overrides_model_supplied_benchmark_policy():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "germline_variant_calling",
            "benchmark_policy": "scientific_harness",
        },
        user_query="Call germline variants from paired-end reads.",
        contract={"must_include_capabilities": ["variant_calling"]},
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )

    assert spec["benchmark_policy"] == BIOAGENTBENCH_PLANNING_STRICT_POLICY


def test_normalize_analysis_spec_replaces_verbose_model_skeleton_in_blind_benchmark_mode():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "bacterial_evolution_variant_calling",
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "plan_skeleton": [
                {
                    "tool": "spades_assemble",
                    "command": "spades.py -1 /very/long/path/anc_R1.fastq.gz -2 /very/long/path/anc_R2.fastq.gz",
                }
            ],
        },
        user_query="Identify shared variants in evolved bacterial isolates relative to an ancestor.",
        contract={"must_include_capabilities": ["alignment", "variant_calling", "reference_inputs"]},
        available_skill_names=["spades_assemble", "bwa_mem_align", "freebayes_call", "snpeff_annotate"],
    )

    assert spec["plan_skeleton"]
    assert isinstance(spec["plan_skeleton"][0], tuple)
    assert spec["plan_skeleton"][0][0] == "spades_assemble"
    assert any(step[0] == "prodigal_annotate" for step in spec["plan_skeleton"])


def test_normalize_analysis_spec_prefers_subread_scaffold_for_blind_rna_seq_de_gff_tasks():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "rna_seq_differential_expression",
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "plan_skeleton": [
                ("star_align", "Align reads", {}),
                ("featurecounts_run", "Count genes", {}),
                ("deseq2_run", "Run DESeq2", {}),
            ],
            "protocol_grounding": {
                "required_tools": ["subread_align", "featurecounts_run", "deseq2_run"],
                "required_plan_signals": ["subread_align", "featurecounts_run", "deseq2_run"],
            },
        },
        user_query="Identify differentially expressed genes between planktonic and biofilm conditions using DESeq2.",
        contract={"must_include_capabilities": ["differential_analysis"]},
        available_skill_names=["subread_align", "featurecounts_run", "deseq2_run", "star_align", "star_2pass_align"],
    )

    assert spec["chosen_method"] == "featurecounts_run + deseq2_run"
    assert spec["preferred_tools"][:3] == ["subread_align", "featurecounts_run", "deseq2_run"]
    assert spec["plan_skeleton"][0][0] == "subread_align"
    assert spec["plan_skeleton"][1][0] == "featurecounts_run"
    assert spec["plan_skeleton"][2][0] == "deseq2_run"
    assert "star_align" in spec["discouraged_tools"]
    assert any("Do not invent a genome_index directory" in fact for fact in spec["context_facts"])


def test_normalize_analysis_spec_prefers_subread_scaffold_for_scientific_rna_seq_de_gff_tasks():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "rna_seq_differential_expression",
            "benchmark_policy": "scientific_harness",
            "plan_skeleton": [
                ("star_align", "Align reads", {}),
                ("featurecounts_run", "Count genes", {}),
                ("deseq2_run", "Run DESeq2", {}),
            ],
            "protocol_grounding": {
                "required_tools": ["subread_align", "featurecounts_run", "deseq2_run"],
                "required_plan_signals": ["subread_align", "featurecounts_run", "deseq2_run"],
            },
        },
        user_query="Identify differentially expressed genes between planktonic and biofilm conditions.",
        contract={"must_include_capabilities": ["differential_analysis"]},
        available_skill_names=["subread_align", "featurecounts_run", "deseq2_run", "star_align"],
    )

    assert spec["plan_skeleton"][0][0] == "subread_align"
    assert spec["preferred_tools"][:3] == ["subread_align", "featurecounts_run", "deseq2_run"]
    assert "star_align" in spec["discouraged_tools"]


def test_normalize_analysis_spec_uses_discovered_gff_to_prefer_subread_scaffold():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "rna_seq_differential_expression",
            "benchmark_policy": "scientific_harness",
            "plan_skeleton": [
                ("star_align", "Align reads", {}),
                ("featurecounts_run", "Count genes", {}),
                ("deseq2_run", "Run DESeq2", {}),
            ],
            "protocol_grounding": {
                "compatible_tools": ["subread_align", "featurecounts_run", "star_align"],
            },
        },
        user_query="Identify differentially expressed genes between planktonic and biofilm conditions.",
        contract={"must_include_capabilities": ["differential_analysis"]},
        available_skill_names=["subread_align", "featurecounts_run", "deseq2_run", "star_align"],
        discovered_data_files=[
            {"name": "features.gff", "path": "/data/references/features.gff"},
        ],
    )

    assert spec["plan_skeleton"][0][0] == "subread_align"
    assert "star_align" in spec["discouraged_tools"]


def test_normalize_analysis_spec_adds_execution_contract_for_direct_scanpy_request() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "single_cell_rna_seq",
            "chosen_method": "scanpy_workflow",
            "protocol_grounding": {"required_tools": ["scanpy_workflow"]},
        },
        user_query=(
            "This is single-cell work, but do not switch to Seurat. "
            "Keep the workflow on scanpy_workflow for the processed AnnData file "
            "at /tmp/pbmc3k_processed.h5ad and write outputs under /tmp/scanpy_output only."
        ),
        contract={
            "must_include_capabilities": ["single_cell_analysis"],
            "required_tool_hints": ["scanpy_workflow"],
            "explicit_tool_hints": ["scanpy_workflow"],
            "blocked_tool_hints": ["seurat"],
        },
        available_skill_names=["scanpy_workflow", "seurat_rscript_workflow"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["analysis_family"] == "single_cell_rna_seq"
    assert execution_contract["input_mode"] == "processed_single_cell"
    assert execution_contract["execution_mode"] == "direct_wrapper"
    assert execution_contract["compatible_tools"] == ["scanpy_workflow"]
    assert execution_contract["blocked_tools"] == ["seurat"]
    assert spec["protocol_grounding"]["input_mode"] == "processed_single_cell"
    assert spec["protocol_grounding"]["execution_mode"] == "direct_wrapper"


def test_normalize_analysis_spec_adds_execution_contract_for_direct_spatial_request() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "spatial_transcriptomics",
            "chosen_method": "spatial_transcriptomics_workflow",
            "protocol_grounding": {"required_tools": ["spatial_transcriptomics_workflow"]},
        },
        user_query=(
            "Analyze this Visium spatial transcriptomics h5ad file with "
            "spatial_transcriptomics_workflow and write outputs under /tmp/spatial_out."
        ),
        contract={
            "must_include_capabilities": ["spatial_transcriptomics", "single_cell_analysis"],
            "required_tool_hints": ["spatial_transcriptomics_workflow"],
            "explicit_tool_hints": ["spatial_transcriptomics_workflow"],
        },
        available_skill_names=["spatial_transcriptomics_workflow", "scanpy_workflow"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["analysis_family"] == "spatial_transcriptomics"
    assert execution_contract["input_mode"] == "processed_single_cell"
    assert execution_contract["execution_mode"] == "direct_wrapper"
    assert execution_contract["compatible_tools"] == ["spatial_transcriptomics_workflow"]


def test_normalize_analysis_spec_adds_execution_contract_for_direct_proteomics_request() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "proteomics",
            "chosen_method": "proteomics_diff_abundance",
            "protocol_grounding": {"required_tools": ["proteomics_diff_abundance"]},
        },
        user_query=(
            "Run proteomics_diff_abundance on the abundance matrix /tmp/abundance_matrix.csv "
            "with metadata /tmp/metadata.csv and write outputs under /tmp/proteomics_out."
        ),
        contract={
            "must_include_capabilities": ["proteomics", "differential_analysis", "group_comparison"],
            "required_tool_hints": ["proteomics_diff_abundance"],
            "explicit_tool_hints": ["proteomics_diff_abundance"],
        },
        available_skill_names=["proteomics_diff_abundance", "deseq2_run"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["analysis_family"] == "proteomics"
    assert execution_contract["input_mode"] == "count_matrix"
    assert execution_contract["execution_mode"] == "direct_wrapper"
    assert execution_contract["compatible_tools"] == ["proteomics_diff_abundance"]


def test_normalize_analysis_spec_adds_execution_contract_for_direct_deseq_counts_request() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "rna_seq_differential_expression",
            "chosen_method": "deseq2_run",
            "protocol_grounding": {"required_tools": ["deseq2_run"]},
        },
        user_query=(
            "Run deseq2_run directly on /tmp/airway_counts.tsv with "
            "/tmp/airway_metadata_dex.tsv for dex."
        ),
        contract={
            "must_include_capabilities": ["differential_analysis"],
            "required_tool_hints": ["deseq2_run"],
            "explicit_tool_hints": ["deseq2_run"],
        },
        available_skill_names=["deseq2_run", "edger_run", "limma_voom_run"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["input_mode"] == "count_matrix"
    assert execution_contract["execution_mode"] == "direct_wrapper"
    assert execution_contract["compatible_tools"] == ["deseq2_run"]
    assert spec["protocol_grounding"]["compatible_tools"] == ["deseq2_run"]


def test_deterministic_analysis_spec_expands_stringtie_output_root_to_canonical_outputs() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use stringtie_quant on /tmp/sample.bam with annotation /tmp/genes.gtf "
            "and write outputs under /tmp/stringtie."
        ),
        contract={
            "required_tool_hints": ["stringtie_quant"],
            "explicit_tool_hints": ["stringtie_quant"],
        },
        available_skill_names=["stringtie_quant"],
    )

    intent = spec["explicit_execution_intent"]
    locked = intent["locked_argument_values"]["stringtie_quant"]
    assert locked["output_gtf"] == "/tmp/stringtie/assembled.gtf"
    assert locked["gene_abundance_tsv"] == "/tmp/stringtie/gene_abundances.tsv"


def test_deterministic_analysis_spec_preserves_explicit_stringtie_file_outputs() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use only the stringtie_quant tool on /tmp/sample.bam with /tmp/genes.gtf. "
            "Write the assembled transcript GTF to /tmp/stringtie/assembled.gtf "
            "and the gene abundance table to /tmp/stringtie/gene_abundances.tsv."
        ),
        contract={
            "required_tool_hints": ["stringtie_quant"],
            "explicit_tool_hints": ["stringtie_quant"],
        },
        available_skill_names=["stringtie_quant"],
    )

    locked = spec["explicit_execution_intent"]["locked_argument_values"]["stringtie_quant"]
    assert locked["output_gtf"] == "/tmp/stringtie/assembled.gtf"
    assert locked["gene_abundance_tsv"] == "/tmp/stringtie/gene_abundances.tsv"


def test_deterministic_analysis_spec_prefers_long_read_rna_annotation_pipeline() -> None:
    spec = deterministic_analysis_spec(
        (
            "These are Oxford Nanopore direct-RNA reads. Align them to the reference genome "
            "using the provided annotation and quantify transcript isoforms."
        ),
        contract={
            "must_include_capabilities": ["alignment", "annotation", "reference_inputs", "quantification"],
            "required_tool_hints": ["minimap2_align", "stringtie_quant"],
        },
        available_skill_names=["minimap2_align", "stringtie_quant", "salmon_quant"],
    )

    assert spec["analysis_type"] == "long_read_rna"
    assert spec["chosen_method"] == "minimap2_align + stringtie_quant"
    assert spec["preferred_tools"] == ["minimap2_align", "stringtie_quant"]


def test_normalize_analysis_spec_marks_annotation_backed_long_read_rna_as_compiled_pipeline() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "long_read_rna",
            "chosen_method": "minimap2_align + stringtie_quant",
        },
        user_query=(
            "These are Oxford Nanopore direct-RNA reads with the provided annotation GTF. "
            "Align them to the reference genome and quantify transcript isoforms."
        ),
        contract={
            "must_include_capabilities": ["alignment", "annotation", "reference_inputs", "quantification"],
            "required_tool_hints": ["minimap2_align", "stringtie_quant"],
        },
        available_skill_names=["minimap2_align", "stringtie_quant"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["analysis_family"] == "long_read_rna"
    assert execution_contract["input_mode"] == "raw_fastq"
    assert execution_contract["execution_mode"] == "compiled_pipeline"


def test_normalize_analysis_spec_marks_raw_fastq_de_as_compiled_pipeline() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "rna_seq_differential_expression",
            "chosen_method": "featurecounts_run + deseq2_run",
        },
        user_query=(
            "Identify differentially expressed genes from paired-end reads "
            "/tmp/sample_R1.fastq.gz and /tmp/sample_R2.fastq.gz."
        ),
        contract={"must_include_capabilities": ["differential_analysis", "alignment"]},
        available_skill_names=["subread_align", "featurecounts_run", "deseq2_run"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["input_mode"] == "raw_fastq"
    assert execution_contract["execution_mode"] == "compiled_pipeline"


def test_build_analysis_brief_includes_execution_contract_and_protocol_compatibility() -> None:
    brief = build_analysis_brief(
        {
            "analysis_type": "single_cell_rna_seq",
            "chosen_method": "scanpy_workflow",
            "execution_contract": {
                "input_mode": "processed_single_cell",
                "execution_mode": "direct_wrapper",
            },
            "protocol_grounding": {
                "task_name": "single_cell_rna_seq",
                "input_mode": "processed_single_cell",
                "execution_mode": "direct_wrapper",
                "compatible_tools": ["scanpy_workflow", "seurat_rscript_workflow"],
            },
        }
    )

    assert "input_mode=processed_single_cell" in brief
    assert "execution_mode=direct_wrapper" in brief
    assert "protocol_input_mode=processed_single_cell" in brief
    assert "protocol_execution_mode=direct_wrapper" in brief
    assert "protocol_compatible_tools=scanpy_workflow, seurat_rscript_workflow" in brief


def test_build_analysis_brief_includes_visible_literature_planning_support() -> None:
    brief = build_analysis_brief(
        {
            "analysis_type": "long_read_rna",
            "literature_planning_support": {
                "visible_to_planner": True,
                "query_class": "parameter_recommendation",
                "trigger_reason": "parameter_question:preset",
                "sources_consulted": 4,
                "primary_literature_count": 3,
                "trusted_web_count": 1,
                "backend_diversity_count": 2,
                "recommendations": ["Use splice-aware minimap2 presets."],
                "parameter_suggestions": [["minimap2", "preset", "splice"]],
            },
        }
    )

    assert "literature_assistance_query_class=parameter_recommendation" in brief
    assert "literature_assistance_reason=parameter_question:preset" in brief
    assert "literature_parameter_suggestions=minimap2.preset=splice" in brief


def test_infer_analysis_type_prioritizes_single_cell_contract_over_generic_de() -> None:
    analysis_type = infer_analysis_type(
        "Analyze single-cell RNA-seq data from pre- and post-exercise skeletal muscle samples.",
        {
            "must_include_capabilities": [
                "alignment",
                "annotation",
                "differential_analysis",
                "reference_inputs",
                "single_cell_analysis",
            ]
        },
    )

    assert analysis_type == "single_cell_rna_seq"


def test_normalize_analysis_spec_blind_germline_omits_truth_benchmark_step() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "germline_variant_calling",
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
        },
        user_query=(
            "BioAgentBench official-mode task: GIAB Germline Variant Calling. "
            "Use the provided NA12878 sequencing data and reference genome to perform germline variant calling. "
            "Do not read benchmark truth files, benchmark results files, or benchmark recipe files."
        ),
        contract={"must_include_capabilities": ["reference_inputs", "variant_calling"]},
        available_skill_names=["bwa_mem_align", "gatk_haplotypecaller", "bash_run"],
        benchmark_policy=BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    )

    assert spec["analysis_type"] == "germline_variant_calling"
    assert spec["preferred_tools"] == ["bwa_mem_align", "gatk_haplotypecaller"]
    assert all(step[0] != "bash_run" for step in spec["plan_skeleton"])
    assert all("hap.py" not in check.lower() for check in spec["acceptance_checks"])
    assert any("forbids using truth-set benchmarking" in fact for fact in spec["context_facts"])


def test_deterministic_analysis_spec_prefers_helper_backed_path_for_viral_metagenomics():
    contract = {"must_include_capabilities": ["alignment", "classification", "reference_inputs"]}
    spec = deterministic_analysis_spec(
        "Identify viruses in paired-end reads by mapping against a staged viral reference panel.",
        contract=contract,
        available_skill_names=[
            "minimap2_align",
            "fastp_run",
            "bash_run",
            "fastqc_run",
        ],
    )

    assert spec["analysis_type"] == "viral_metagenomics"
    assert spec["chosen_method"] == "fastp_run + bash_run"
    assert spec["preferred_tools"][:3] == ["fastp_run", "bash_run", "fastqc_run"]
    assert any(
        entry.get("tool_name") == "bash_run"
        and "classify_viral_reads_kmer.py" in entry.get("settings", {}).get("helper_script", "")
        for entry in spec["parameter_profile"]
    )
    assert any(
        entry.get("tool_name") == "fastp_run" and entry.get("settings", {}).get("length_required") == 30
        for entry in spec["parameter_profile"]
    )
    assert spec["plan_skeleton"][0][0] == "fastp_run"
    assert spec["plan_skeleton"][1][0] == "bash_run"
    assert "classify_viral_reads_kmer.py" in spec["plan_skeleton"][1][2]["helper_script"]


def test_deterministic_analysis_spec_viral_metagenomics_falls_back_to_bash_fastp_when_wrapper_missing():
    spec = deterministic_analysis_spec(
        "Identify viruses in paired-end reads by mapping against a staged viral reference panel.",
        contract={"must_include_capabilities": ["alignment", "classification", "reference_inputs"]},
        available_skill_names=[
            "minimap2_align",
            "bash_run",
            "fastqc_run",
        ],
    )

    assert spec["chosen_method"] == "bash_run + bash_run"
    assert spec["plan_skeleton"][0][0] == "bash_run"
    assert spec["plan_skeleton"][1][0] == "bash_run"
    assert spec["parameter_profile"][0]["tool_name"] == "bash_run"
    assert "classify_viral_reads_kmer.py" in spec["parameter_profile"][1]["settings"]["helper_script"]


def test_deterministic_analysis_spec_prefers_sniffles_for_structural_variant_calling():
    spec = deterministic_analysis_spec(
        "Call structural variants from the aligned long-read BAM using Sniffles.",
        contract={"must_include_capabilities": ["structural_variant_calling", "reference_inputs"]},
        available_skill_names=["sniffles_sv_call", "minimap2_align", "bcftools_call"],
    )

    assert spec["analysis_type"] == "structural_variant_calling"
    assert spec["chosen_method"] == "sniffles_sv_call"
    assert spec["preferred_tools"][:2] == ["sniffles_sv_call", "minimap2_align"]
    assert "bcftools_call" in spec["discouraged_tools"]
    assert spec["plan_skeleton"][0][0] == "sniffles_sv_call"
    assert any(
        entry.get("tool_name") == "sniffles_sv_call"
        and entry.get("settings", {}).get("min_support") == 3
        and entry.get("settings", {}).get("min_sv_length") == 50
        for entry in spec["parameter_profile"]
    )


def test_infer_analysis_type_detects_long_read_assembly_prompt() -> None:
    result = infer_analysis_type(
        "Assemble these Oxford Nanopore reads into a de novo genome assembly and write contigs in FASTA format.",
        contract={},
        available_skill_names=["flye_assemble", "bash_run"],
    )

    assert result == "long_read_assembly"


def test_deterministic_analysis_spec_prefers_flye_for_long_read_assembly() -> None:
    spec = deterministic_analysis_spec(
        "Assemble these Oxford Nanopore reads into a de novo genome assembly and write contigs in FASTA format.",
        contract={},
        available_skill_names=["flye_assemble", "bash_run"],
    )

    assert spec["analysis_type"] == "long_read_assembly"
    assert spec["chosen_method"] == "flye_assemble"
    assert spec["preferred_tools"][:1] == ["flye_assemble"]
    assert spec["plan_skeleton"][0][0] == "flye_assemble"


def test_infer_analysis_type_detects_long_read_rna_prompt() -> None:
    result = infer_analysis_type(
        "These are Oxford Nanopore direct-RNA reads. Align them to the genome and quantify transcript isoforms.",
        contract={},
        available_skill_names=["minimap2_align", "bash_run"],
    )

    assert result == "long_read_rna"


def test_infer_analysis_type_prefers_long_read_rna_over_generic_quantification_caps() -> None:
    result = infer_analysis_type(
        "These are Oxford Nanopore direct-RNA reads. Align them to the genome and quantify transcript isoforms.",
        contract={"must_include_capabilities": ["quantification", "alignment", "annotation"]},
        available_skill_names=["minimap2_align", "salmon_quant", "bash_run"],
    )

    assert result == "long_read_rna"


def test_infer_analysis_type_detects_noisy_long_read_structural_change_prompt() -> None:
    result = infer_analysis_type(
        (
            "I have some long sequencing reads and a reference genome. "
            "Can you figure out if there are any big structural changes in my sample compared to the reference?"
        ),
        contract={},
        available_skill_names=["sniffles_sv_call", "minimap2_align", "bash_run"],
    )

    assert result == "structural_variant_calling"


def test_deterministic_analysis_spec_uses_metagenome_defaults_for_long_read_assembly() -> None:
    spec = deterministic_analysis_spec(
        "Assemble these Oxford Nanopore reads into a metagenome assembly. There may be multiple organisms present.",
        contract={},
        available_skill_names=["flye_assemble", "bash_run"],
    )

    assert spec["analysis_type"] == "long_read_assembly"
    assert any(
        entry.get("tool_name") == "flye_assemble"
        and entry.get("settings", {}).get("meta_mode") is True
        and entry.get("settings", {}).get("threads") == 2
        and entry.get("settings", {}).get("genome_size") == "100k"
        for entry in spec["parameter_profile"]
    )


def test_normalize_analysis_spec_adds_execution_contract_for_long_read_assembly() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "long_read_assembly",
            "chosen_method": "flye_assemble",
            "preferred_tools": ["flye_assemble"],
        },
        user_query=(
            "Assemble these Oxford Nanopore reads into a de novo genome assembly "
            "and write contigs in FASTA format."
        ),
        contract={},
        available_skill_names=["flye_assemble", "bash_run"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["analysis_family"] == "long_read_assembly"
    assert execution_contract["input_mode"] == "raw_fastq"
    assert execution_contract["execution_mode"] == "direct_wrapper"
    assert execution_contract["compatible_tools"] == ["flye_assemble"]


def test_normalize_analysis_spec_infers_unique_input_mode_from_chosen_wrapper() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "transcript_quantification",
            "chosen_method": "stringtie_quant",
            "preferred_tools": ["stringtie_quant"],
        },
        user_query="Quantify transcripts and write a GTF plus expression estimates.",
        contract={},
        available_skill_names=["stringtie_quant", "salmon_quant"],
    )

    execution_contract = spec["execution_contract"]
    assert execution_contract["analysis_family"] == "transcript_quantification"
    assert execution_contract["input_mode"] == "aligned_bam"
    assert execution_contract["compatible_tools"] == ["stringtie_quant"]


def test_deterministic_analysis_spec_seeds_helper_for_metagenomics_classification() -> None:
    spec = deterministic_analysis_spec(
        "Classify the paired-end metagenomics reads and write a Kraken-style report.",
        contract={"must_include_capabilities": ["genome_assembly", "metagenomics_profiling"]},
        available_skill_names=["spades_assemble", "bash_run", "fastqc_run"],
    )

    assert spec["analysis_type"] == "metagenomics_classification"
    assert spec["chosen_method"] == "spades_assemble + bash_run"
    assert spec["plan_skeleton"][0][0] == "spades_assemble"
    assert spec["plan_skeleton"][0][2]["meta_mode"] is True
    assert spec["plan_skeleton"][1][0] == "bash_run"
    assert "classify_metagenomics_kmer.py" in spec["plan_skeleton"][1][2]["helper_script"]


def test_deterministic_analysis_spec_seeds_phylogenetics_helper() -> None:
    spec = deterministic_analysis_spec(
        "Infer a phylogenetic tree from the provided homologous sequences and write a Newick tree.",
        contract={"must_include_capabilities": ["phylogenetics"]},
        available_skill_names=["bash_run"],
    )

    assert spec["analysis_type"] == "phylogenetics"
    assert spec["chosen_method"] == "bash_run"
    assert spec["preferred_tools"] == ["bash_run"]
    assert spec["plan_skeleton"][0][0] == "bash_run"
    assert "infer_phylogeny_biopython.py" in spec["plan_skeleton"][0][2]["helper_script"]
    assert any("Do not fabricate placeholder Newick trees" in fact for fact in spec["context_facts"])


def test_deterministic_analysis_spec_seeds_helper_for_viral_metagenomics_without_minimap2() -> None:
    spec = deterministic_analysis_spec(
        "Identify viruses from paired-end reads and write the coverage and detection outputs.",
        contract={"must_include_capabilities": ["metagenomics_profiling", "alignment"]},
        available_skill_names=["bash_run", "fastqc_run"],
    )

    assert spec["analysis_type"] == "viral_metagenomics"
    assert spec["chosen_method"] == "bash_run"
    assert spec["plan_skeleton"][0][0] == "bash_run"
    assert "classify_viral_reads_kmer.py" in spec["plan_skeleton"][0][2]["helper_script"]


# ── Canonicalization alias tests ──────────────────────────────────────

def test_canonicalize_variant_calling_with_annotation_heuristic():
    """LLM returns 'variant_calling' but prompt mentions SnpEff → variant_annotation."""
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("variant_calling", "variant_annotation")
    assert result == "variant_annotation", f"Expected variant_annotation, got {result}"


def test_canonicalize_variant_calling_without_heuristic():
    """LLM returns 'variant_calling' with no useful heuristic → germline_variant_calling."""
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("variant_calling", "variant_calling")
    assert result == "germline_variant_calling", f"Expected germline_variant_calling, got {result}"


def test_canonicalize_exact_canonical_passthrough():
    """Exact canonical type should pass through unchanged."""
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("variant_annotation", "")
    assert result == "variant_annotation"


def test_canonicalize_long_read_rna_overrides_generic_transcript_quantification() -> None:
    """Long-read RNA heuristic should beat generic transcript quantification."""
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("transcript_quantification", "long_read_rna")
    assert result == "long_read_rna"


def test_canonicalize_long_read_assembly_overrides_generic_comparative_label() -> None:
    """Long-read assembly heuristic should beat generic comparative-genomics output."""
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("comparative_genomics", "long_read_assembly")
    assert result == "long_read_assembly"


def test_canonicalize_proteomics_overrides_generic_rna_de_label() -> None:
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("rna_seq_differential_expression", "proteomics")
    assert result == "proteomics"


def test_canonicalize_metabolomics_overrides_generic_rna_de_label() -> None:
    from bio_harness.core.analysis_spec import _canonicalize_analysis_type

    result = _canonicalize_analysis_type("rna_seq_differential_expression", "metabolomics")
    assert result == "metabolomics"


# ── Fix 9: Annotation-context gating of variant_calling ──────────────

def test_infer_analysis_type_annotation_prompt_with_variant_calling_contract():
    """Contract has variant_calling but prompt is about annotation → variant_annotation."""
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs"]}
    result = infer_analysis_type(
        "Annotate variants with functional impact predictions using SnpEff.",
        contract,
    )
    assert result == "variant_annotation", f"Expected variant_annotation, got {result}"


def test_infer_analysis_type_germline_still_works_with_variant_calling_contract():
    """Contract has variant_calling and prompt is about germline → germline_variant_calling."""
    contract = {"must_include_capabilities": ["variant_calling"]}
    result = infer_analysis_type(
        "Call germline variants from paired-end FASTQ files using GATK HaplotypeCaller.",
        contract,
    )
    assert result == "germline_variant_calling", f"Expected germline_variant_calling, got {result}"


def test_infer_analysis_type_generic_variant_calling_contract():
    """Contract has variant_calling and prompt mentions tumor → somatic_variant_calling."""
    contract = {"must_include_capabilities": ["variant_calling"]}
    result = infer_analysis_type(
        "Detect variants in the tumor sample.",
        contract,
    )
    assert result == "somatic_variant_calling", f"Expected somatic_variant_calling, got {result}"


def test_infer_analysis_type_truly_generic_variant_calling_contract():
    """Contract has variant_calling and prompt is generic → variant_calling."""
    contract = {"must_include_capabilities": ["variant_calling"]}
    result = infer_analysis_type(
        "Detect variants in this DNA sample.",
        contract,
    )
    assert result == "variant_calling", f"Expected variant_calling, got {result}"


def test_infer_analysis_type_annotation_prompt_beats_somatic_calling_contract():
    """Tumor-focused annotation prompts should remain annotation tasks."""
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs"]}
    result = infer_analysis_type(
        "Annotate tumor variants with ClinVar and SnpEff for functional impact review.",
        contract,
    )
    assert result == "variant_annotation", f"Expected variant_annotation, got {result}"


def test_infer_analysis_type_annotation_prompt_beats_somatic_keywords_without_contract():
    """Annotation cues should override somatic context even without a contract."""
    result = infer_analysis_type(
        "Annotate somatic tumor variants with SnpEff and ClinVar.",
        None,
    )
    assert result == "variant_annotation", f"Expected variant_annotation, got {result}"


def test_infer_analysis_type_structural_variant_prompt_with_variant_contract():
    contract = {"must_include_capabilities": ["variant_calling", "reference_inputs"]}
    result = infer_analysis_type(
        "Call structural variants from the aligned Nanopore BAM using Sniffles.",
        contract,
    )

    assert result == "structural_variant_calling"


# ---------------------------------------------------------------------------
# multi_model_dge_pathway routing
# ---------------------------------------------------------------------------


def test_infer_analysis_type_de_plus_pathway_contract():
    """differential_analysis contract + pathway keywords → multi_model_dge_pathway."""
    contract = {"must_include_capabilities": ["differential_analysis"]}
    result = infer_analysis_type(
        "Perform differential gene expression analysis and pathway enrichment on the count matrix.",
        contract,
    )
    assert result == "multi_model_dge_pathway", f"Expected multi_model_dge_pathway, got {result}"


def test_infer_analysis_type_de_contract_no_pathway():
    """differential_analysis contract WITHOUT pathway keywords → rna_seq_differential_expression."""
    contract = {"must_include_capabilities": ["differential_analysis"]}
    result = infer_analysis_type(
        "Perform differential gene expression analysis on the count matrix.",
        contract,
    )
    assert result == "rna_seq_differential_expression", f"Expected rna_seq_differential_expression, got {result}"


def test_infer_analysis_type_de_keyword_plus_pathway():
    """Keyword-driven: 'differential expression' + 'pathway' → multi_model_dge_pathway."""
    result = infer_analysis_type(
        "Run differential expression and pathway enrichment analysis on RNA-seq data.",
        None,
    )
    assert result == "multi_model_dge_pathway", f"Expected multi_model_dge_pathway, got {result}"


def test_infer_analysis_type_de_keyword_no_pathway():
    """Keyword-driven: 'differential expression' alone → rna_seq_differential_expression."""
    result = infer_analysis_type(
        "Run differential expression analysis using DESeq2.",
        None,
    )
    assert result == "rna_seq_differential_expression", f"Expected rna_seq_differential_expression, got {result}"


def test_normalize_analysis_spec_locks_multi_model_dge_to_bash_run():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "multi_model_dge_pathway",
            "chosen_method": "dexseq_run",
            "preferred_tools": ["dexseq_run", "bash_run"],
            "candidate_methods": ["dexseq_run"],
            "discouraged_tools": [],
            "context_facts": [],
        },
        user_query="Compare shared KEGG pathways across Alzheimer's mouse models.",
        contract={"must_include_capabilities": ["differential_analysis", "pathway_enrichment"]},
        available_skill_names=["bash_run", "dexseq_run", "deseq2_run"],
    )

    assert spec["chosen_method"] == "bash_run"
    assert spec["preferred_tools"] == ["bash_run"]
    assert spec["candidate_methods"] == ["bash_run"]
    assert "dexseq_run" in spec["discouraged_tools"]
    assert any("compare_pathways.py" in fact for fact in spec["context_facts"])
    assert any("Do not fabricate placeholder pathway" in fact for fact in spec["context_facts"])
    assert spec["parameter_profile"][0]["settings"]["helper_script"].endswith("compare_pathways.py")
    assert spec["plan_skeleton"] == [
        (
            "bash_run",
            "Invoke compare_pathways.py to filter counts, run DGE, and compute shared KEGG enrichment",
            {"tool": "python3", "helper_script": spec["parameter_profile"][0]["settings"]["helper_script"]},
        ),
    ]


def test_deterministic_analysis_spec_uses_local_reference_for_variant_annotation_benchmark():
    spec = deterministic_analysis_spec(
        (
            "BioAgentBench official-mode task: Variant Annotation (Local Structured Eval). "
            "Input files include genes.gff, input_variants.vcf, and reference.fa. "
            "Annotate the provided VCF with the provided reference FASTA and GFF annotation, "
            "then write annotated.vcf and filtered_pathogenic.vcf."
        ),
        contract={"must_include_capabilities": ["variant_calling", "reference_inputs"]},
        available_skill_names=["snpeff_annotate", "bash_run"],
    )

    assert spec["analysis_type"] == "variant_annotation"
    assert spec["chosen_method"] == "snpeff_annotate"
    assert spec["source_provenance"][-1] == "BioAgentBench variant-annotation task"
    assert spec["parameter_profile"][0]["settings"]["genome_db"] == "custom_ref"
    assert spec["plan_skeleton"] == [
        (
            "snpeff_annotate",
            "Annotate the provided input VCF with SnpEff using the supplied local FASTA and GFF annotation",
            {"genome_db": "custom_ref"},
        ),
        (
            "bash_run",
            "Filter the annotated VCF to keep only HIGH and MODERATE impact variants and write the requested filtered VCF",
            {"tool": "SnpSift"},
        ),
    ]


def test_normalize_analysis_spec_replaces_clinical_variant_annotation_skeleton_for_local_benchmark():
    spec = normalize_analysis_spec(
        {
            "analysis_type": "variant_annotation",
            "benchmark_policy": BIOAGENTBENCH_PLANNING_STRICT_POLICY,
            "plan_skeleton": [
                ("snpeff_annotate", "Annotate variants with SnpEff if the input VCF is not already annotated", {"genome_db": "GRCh37.75"}),
                ("bash_run", "Filter for recessive segregation across affected siblings and parents", {"tool": "SnpSift"}),
            ],
        },
        user_query=(
            "BioAgentBench official-mode task: Variant Annotation (Local Structured Eval). "
            "Input files include genes.gff, input_variants.vcf, and reference.fa. "
            "Annotate the provided VCF with the provided reference FASTA and GFF annotation, "
            "then filter for HIGH and MODERATE impact variants."
        ),
        contract={"must_include_capabilities": ["variant_calling", "reference_inputs"]},
        available_skill_names=["snpeff_annotate", "bash_run"],
    )

    assert spec["parameter_profile"][0]["settings"]["genome_db"] == "custom_ref"
    assert len(spec["plan_skeleton"]) == 2
    assert spec["plan_skeleton"][0][2]["genome_db"] == "custom_ref"
    assert "GRCh37.75" not in str(spec["plan_skeleton"])


def test_infer_analysis_type_detects_run_reporting_prompt() -> None:
    result = infer_analysis_type(
        "Use the quarto_report tool on the completed run at /tmp/run and write the report bundle to /tmp/out.",
        None,
    )

    assert result == "run_reporting"


def test_infer_analysis_type_detects_artifact_schema_prompt() -> None:
    result = infer_analysis_type(
        "Use artifact_schema_profile to profile the schema of results.csv and write a schema JSON file.",
        None,
    )

    assert result == "artifact_schema_profiling"


def test_deterministic_analysis_spec_prefers_report_skill_for_reporting_prompt() -> None:
    spec = deterministic_analysis_spec(
        "Use the multiqc_report tool on the completed run at /tmp/run and write the report bundle to /tmp/out.",
        available_skill_names=["bash_run", "multiqc_report", "quarto_report"],
    )

    assert spec["analysis_type"] == "run_reporting"
    assert spec["chosen_method"] == "multiqc_report"
    assert spec["preferred_tools"] == ["multiqc_report", "quarto_report"]


def test_deterministic_analysis_spec_prefers_schema_profiler_for_schema_prompt() -> None:
    spec = deterministic_analysis_spec(
        "Use artifact_schema_profile to profile the schema of results.csv and write a schema JSON file.",
        available_skill_names=["bash_run", "artifact_schema_profile"],
    )

    assert spec["analysis_type"] == "artifact_schema_profiling"
    assert spec["chosen_method"] == "artifact_schema_profile"
    assert spec["preferred_tools"] == ["artifact_schema_profile"]


def test_infer_analysis_type_detects_direct_skill_smoke_prompt() -> None:
    result = infer_analysis_type(
        (
            "This is a direct one-step skill smoke test. Use only the freebayes_call tool "
            "to call variants from sample.bam against reference.fa and write out.vcf."
        ),
        None,
    )

    assert result == "direct_skill_smoke"


def test_deterministic_analysis_spec_prefers_explicit_skill_for_direct_smoke_prompt() -> None:
    spec = deterministic_analysis_spec(
        (
            "This is a direct one-step skill smoke test. Use only the freebayes_call tool "
            "to call variants from sample.bam against reference.fa and write out.vcf."
        ),
        available_skill_names=["bash_run", "freebayes_call", "bcftools_call"],
    )

    assert spec["analysis_type"] == "direct_skill_smoke"
    assert spec["chosen_method"] == "freebayes_call"
    assert spec["preferred_tools"] == ["freebayes_call"]
    assert spec["plan_skeleton"][0][0] == "freebayes_call"


def test_should_not_generate_analysis_review_for_direct_skill_smoke() -> None:
    assert (
        should_generate_analysis_review(
            "This is a direct one-step skill smoke test. Use only the star_align tool on the provided reads.",
            {"must_include_capabilities": ["alignment"]},
        )
        is False
    )


def test_infer_analysis_type_uses_explicit_scanpy_workflow_request() -> None:
    result = infer_analysis_type(
        "Run scanpy_workflow on /tmp/pbmc3k.h5ad and write outputs under /tmp/scanpy_out.",
        None,
        ["scanpy_workflow", "sc_count_and_cluster"],
    )

    assert result == "single_cell_rna_seq"


def test_deterministic_analysis_spec_records_explicit_scanpy_execution_intent() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use only the scanpy_workflow tool on /tmp/pbmc3k.h5ad and write outputs under "
            "/tmp/scanpy_out using min_genes 3, min_cells 1, max_mito_pct 100, "
            "n_hvgs 48, and leiden_resolution 0.3."
        ),
        available_skill_names=["scanpy_workflow", "sc_count_and_cluster", "bash_run"],
    )

    intent = spec["explicit_execution_intent"]

    assert intent["locked_tools"] == ["scanpy_workflow"]
    assert intent["preserve_existing_values_for_tools"] == ["scanpy_workflow"]
    assert intent["locked_argument_values"]["scanpy_workflow"]["output_dir"] == "/tmp/scanpy_out"
    assert intent["locked_argument_values"]["scanpy_workflow"]["min_genes"] == 3
    assert intent["locked_argument_values"]["scanpy_workflow"]["min_cells"] == 1
    assert intent["locked_argument_values"]["scanpy_workflow"]["max_mito_pct"] == 100
    assert intent["locked_argument_values"]["scanpy_workflow"]["n_hvgs"] == 48
    assert intent["locked_argument_values"]["scanpy_workflow"]["leiden_resolution"] == 0.3


def test_deterministic_analysis_spec_records_explicit_deseq_execution_intent() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use only the deseq2_run tool on /tmp/airway/counts.tsv with metadata "
            "/tmp/airway/meta.tsv. Write intermediate outputs under /tmp/deseq_out "
            "and write the final CSV to /tmp/final/deseq_results.csv."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
    )

    intent = spec["explicit_execution_intent"]

    assert intent["locked_tools"] == ["deseq2_run"]
    assert intent["locked_argument_values"]["deseq2_run"]["output_dir"] == "/tmp/deseq_out"
    assert spec["required_deliverables"] == ["/tmp/final/deseq_results.csv"]


def test_deterministic_analysis_spec_uses_pipeline_for_fastq_backed_deseq_prompt() -> None:
    spec = deterministic_analysis_spec(
        (
            "Identify differentially expressed genes between planktonic and biofilm "
            "conditions of Candida parapsilosis using DESeq2."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align", "edger_run"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert spec["analysis_type"] == "rna_seq_differential_expression"
    assert spec["chosen_method"] == "featurecounts_run + deseq2_run"
    assert spec["preferred_tools"][:2] == ["featurecounts_run", "deseq2_run"]
    assert spec["explicit_execution_intent"] == {}
    assert spec["execution_contract"]["input_mode"] == "raw_fastq"
    assert spec["execution_contract"]["execution_mode"] == "compiled_pipeline"


def test_deterministic_analysis_spec_drops_exact_deseq_lock_for_fastq_backed_prompt() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use only deseq2_run to identify differentially expressed genes between "
            "planktonic and biofilm conditions."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert spec["explicit_execution_intent"] == {}
    assert spec["execution_contract"]["input_mode"] == "raw_fastq"
    assert spec["execution_contract"]["execution_mode"] == "compiled_pipeline"


def test_deterministic_analysis_spec_uses_required_tool_hint_for_stringtie_contamination_prompt() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use stringtie_quant, not salmon_quant or kallisto_quant, on the aligned BAM "
            "/tmp/hnrnpc/sample.bam with GTF /tmp/hnrnpc/genes.gtf and write outputs under "
            "/tmp/stringtie."
        ),
        contract={
            "must_include_capabilities": ["quantification", "reference_inputs"],
            "required_tool_hints": ["stringtie_quant"],
            "explicit_tool_hints": ["kallisto_quant", "stringtie_quant"],
            "required_output_paths": ["/tmp/stringtie"],
        },
        available_skill_names=["stringtie_quant", "salmon_quant", "kallisto_quant"],
    )

    intent = spec["explicit_execution_intent"]

    assert intent["locked_tools"] == ["stringtie_quant"]
    assert intent["preserve_output_paths"] is True
    assert spec["chosen_method"] == "stringtie_quant"


def test_deterministic_analysis_spec_preserves_explicit_deseq2_run_request() -> None:
    spec = deterministic_analysis_spec(
        (
            "Use deseq2_run on /tmp/airway/gene_counts.txt and "
            "/tmp/airway/sample_metadata.tsv for differential expression."
        ),
        available_skill_names=["featurecounts_run", "deseq2_run", "salmon_quant"],
    )

    assert spec["analysis_type"] == "rna_seq_differential_expression"
    assert spec["chosen_method"] == "deseq2_run"
    assert spec["preferred_tools"][0] == "deseq2_run"
    assert spec["plan_skeleton"][0][0] == "deseq2_run"
    assert all(step[0] != "featurecounts_run" for step in spec["plan_skeleton"])
    assert spec["parameter_profile"][0]["settings"] == {}
    assert spec["plan_skeleton"][0][2] == {}


def test_deterministic_analysis_spec_general_deseq_seed_does_not_encode_pseudo_arguments() -> None:
    spec = deterministic_analysis_spec(
        "Run differential expression on this counts matrix and metadata table with DESeq2.",
        available_skill_names=["featurecounts_run", "deseq2_run", "salmon_quant"],
    )

    deseq_entries = [entry for entry in spec["parameter_profile"] if entry.get("tool_name") == "deseq2_run"]
    assert deseq_entries
    assert deseq_entries[0]["settings"] == {}


def test_deterministic_analysis_spec_preserves_explicit_stringtie_quant_request() -> None:
    spec = deterministic_analysis_spec(
        (
            "Run stringtie_quant on /tmp/hnrnpc/sample.bam with annotation "
            "/tmp/hnrnpc/chr14.gtf and write outputs under /tmp/stringtie_out."
        ),
        available_skill_names=["stringtie_quant", "salmon_quant", "kallisto_quant"],
    )

    assert spec["analysis_type"] == "transcript_quantification"
    assert spec["chosen_method"] == "stringtie_quant"
    assert spec["preferred_tools"] == ["stringtie_quant"]
    assert spec["plan_skeleton"][0][0] == "stringtie_quant"


def test_normalize_analysis_spec_revalidates_incompatible_preseeded_deseq_lock() -> None:
    spec = normalize_analysis_spec(
        {
            "analysis_type": "rna_seq_differential_expression",
            "chosen_method": "deseq2_run",
            "explicit_execution_intent": {
                "requested_tools": ["deseq2_run"],
                "locked_tools": ["deseq2_run"],
                "preserve_existing_values_for_tools": ["deseq2_run"],
                "locked_argument_values": {
                    "deseq2_run": {"output_dir": "/tmp/deseq_out"},
                },
                "preserve_input_paths": True,
                "preserve_output_paths": True,
            },
        },
        user_query="Use deseq2_run to identify differentially expressed genes between planktonic and biofilm conditions.",
        available_skill_names=["featurecounts_run", "deseq2_run", "subread_align"],
        discovered_data_files=[
            {"name": "SRR1278968_1.fastq", "path": "/tmp/deseq/SRR1278968_1.fastq"},
            {"name": "SRR1278968_2.fastq", "path": "/tmp/deseq/SRR1278968_2.fastq"},
            {"name": "sample_metadata.tsv", "path": "/tmp/deseq/sample_metadata.tsv"},
        ],
    )

    assert spec["explicit_execution_intent"] == {}
    assert spec["execution_contract"]["input_mode"] == "raw_fastq"
    assert spec["execution_contract"]["execution_mode"] == "compiled_pipeline"


def test_deterministic_analysis_spec_maps_stringtie_tool_name_to_wrapper() -> None:
    spec = deterministic_analysis_spec(
        (
            "Proceed. Use aligned BAM /tmp/hnrnpc/sample.bam and annotation "
            "/tmp/hnrnpc/chr14.gtf to quantify transcripts with StringTie."
        ),
        available_skill_names=["stringtie_quant", "salmon_quant", "kallisto_quant"],
    )

    assert spec["analysis_type"] == "transcript_quantification"
    assert spec["chosen_method"] == "stringtie_quant"
    assert spec["preferred_tools"] == ["stringtie_quant"]
    assert spec["plan_skeleton"][0][0] == "stringtie_quant"


def test_deterministic_analysis_spec_prefers_stringtie_for_aligned_bam_quantification_request() -> None:
    spec = deterministic_analysis_spec(
        (
            "I have an aligned BAM already. Quantify transcripts from /tmp/hnrnpc/sample.bam "
            "using /tmp/hnrnpc/chr14.gtf. Write the assembled transcript GTF and gene abundance "
            "table in the run directory."
        ),
        available_skill_names=["stringtie_quant", "salmon_quant", "kallisto_quant", "featurecounts_run"],
    )

    assert spec["analysis_type"] == "transcript_quantification"
    assert spec["chosen_method"] == "stringtie_quant"
    assert spec["preferred_tools"] == ["stringtie_quant"]
    assert spec["discouraged_tools"] == ["salmon_quant", "kallisto_quant", "featurecounts_run"]
    assert spec["plan_skeleton"][0][0] == "stringtie_quant"


def test_deterministic_analysis_spec_prefers_deseq2_for_count_matrix_metadata_prompt() -> None:
    spec = deterministic_analysis_spec(
        (
            "Proceed. Use counts table /tmp/airway/airway_counts.tsv and metadata "
            "/tmp/airway/airway_metadata.tsv to run differential expression for dex."
        ),
        available_skill_names=[
            "featurecounts_run",
            "deseq2_run",
            "dexseq_run",
            "edger_run",
            "limma_voom_run",
        ],
    )

    assert spec["analysis_type"] == "rna_seq_differential_expression"
    assert spec["chosen_method"] == "deseq2_run"
    assert spec["preferred_tools"][0] == "deseq2_run"
    assert spec["plan_skeleton"][0][0] == "deseq2_run"
    assert "dexseq_run" in spec["discouraged_tools"]


def test_profile_builders_cover_all_canonical_analysis_types():
    """Every canonical analysis type should have a registered profile builder."""
    from bio_harness.core.analysis_spec_seed import _PROFILE_BUILDERS
    from bio_harness.core.analysis_spec_support import CANONICAL_ANALYSIS_TYPES

    missing = CANONICAL_ANALYSIS_TYPES - set(_PROFILE_BUILDERS.keys())
    # alternative_splicing may not be in canonical set; filter to only truly missing
    assert not missing, f"Missing profile builders for: {missing}"


def test_profile_builders_return_required_keys():
    """Every registered builder should return a dict with the expected keys."""
    from bio_harness.core.analysis_spec_seed import _PROFILE_BUILDERS

    required_keys = {
        "biological_objective",
        "candidate_methods",
        "chosen_method",
        "preferred_tools",
        "discouraged_tools",
        "parameter_profile",
        "acceptance_checks",
    }
    dummy_skills = {"bash_run", "bwa_mem_align", "freebayes_call", "deseq2_run",
                    "salmon_quant", "spades_assemble", "snpeff_annotate",
                    "sc_count_and_cluster", "gatk_haplotypecaller", "fastp_run"}

    for analysis_type, builder in _PROFILE_BUILDERS.items():
        seed = builder("test query", dummy_skills, sorted(dummy_skills))
        missing = required_keys - set(seed.keys())
        assert not missing, f"Builder for {analysis_type!r} missing keys: {missing}"
