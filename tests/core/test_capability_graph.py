"""Tests for bio_harness.core.capability_graph."""
from __future__ import annotations

from bio_harness.core.capability_graph import (
    CapabilityGraph,
    _ANALYSIS_TYPE_TO_GOAL,
)


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

class TestGraphConstruction:
    def test_default_graph_has_nodes(self):
        graph = CapabilityGraph.default()
        assert len(graph.nodes) > 10

    def test_default_graph_has_edges(self):
        graph = CapabilityGraph.default()
        assert len(graph.edges) > 5

    def test_alignment_nodes_exist(self):
        graph = CapabilityGraph.default()
        assert "short_read_alignment" in graph.nodes
        assert "spliced_alignment" in graph.nodes

    def test_variant_calling_exists(self):
        graph = CapabilityGraph.default()
        assert "variant_calling" in graph.nodes
        vc = graph.nodes["variant_calling"]
        assert "bam" in vc.input_types
        assert "vcf" in vc.output_types

    def test_edge_from_alignment_to_variant_calling(self):
        graph = CapabilityGraph.default()
        # alignment produces bam, variant_calling needs bam
        assert ("short_read_alignment", "variant_calling") in graph.edges

    def test_edge_from_alignment_to_gene_counting(self):
        graph = CapabilityGraph.default()
        # spliced alignment produces bam, gene_counting needs bam
        assert ("spliced_alignment", "gene_counting") in graph.edges


# ---------------------------------------------------------------------------
# Pipeline tracing
# ---------------------------------------------------------------------------

class TestPipelineTracing:
    def test_variant_calling_from_fastq(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "variant_calling",
            ["fastq", "fasta_reference"],
        )
        assert len(pipeline) >= 2
        # First step should be alignment
        assert pipeline[0].id in ("short_read_alignment", "spliced_alignment")
        # Last step should be variant calling
        assert pipeline[-1].id == "variant_calling"

    def test_variant_calling_from_bam(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "variant_calling",
            ["bam", "fasta_reference"],
        )
        assert len(pipeline) == 1
        assert pipeline[0].id == "variant_calling"

    def test_variant_annotation_pipeline(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "variant_annotation",
            ["vcf", "fasta_reference", "gff"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "variant_annotation"

    def test_differential_expression_from_counts(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "differential_expression",
            ["tsv"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "differential_expression"

    def test_phylogenetics_from_alignment(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "phylogenetic_inference",
            ["fasta_alignment"],
        )
        assert len(pipeline) == 1
        assert pipeline[0].id == "phylogenetic_inference"

    def test_phylogenetics_from_fasta(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "phylogenetic_inference",
            ["fasta"],
        )
        assert len(pipeline) >= 2
        assert pipeline[-1].id == "phylogenetic_inference"

    def test_structural_variant_calling_from_bam(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline(
            "structural_variant_calling",
            ["bam", "fasta_reference"],
        )

        assert len(pipeline) == 1
        assert pipeline[0].id == "structural_variant_calling"

    def test_unknown_goal_returns_empty(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline("nonexistent_goal", ["fastq"])
        assert pipeline == []

    def test_analysis_type_mapping(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "germline_variant_calling",
            ["fastq", "fasta_reference"],
        )
        assert len(pipeline) >= 2
        assert pipeline[-1].id == "variant_calling"

    def test_analysis_type_mapping_rna_seq(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "rna_seq_differential_expression",
            ["tsv"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "differential_expression"

    def test_analysis_type_mapping_structural_variant_calling(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "structural_variant_calling",
            ["bam", "fasta_reference"],
        )

        assert len(pipeline) == 1
        assert pipeline[-1].id == "structural_variant_calling"

    def test_analysis_type_mapping_long_read_assembly(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "long_read_assembly",
            ["fastq"],
        )

        assert len(pipeline) == 1
        assert pipeline[-1].id == "long_read_assembly"

    def test_analysis_type_mapping_long_read_rna(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "long_read_rna",
            ["fastq", "fasta_reference"],
        )

        assert len(pipeline) == 1
        assert pipeline[-1].id == "long_read_rna"

    def test_single_cell_analysis(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "single_cell_rna_seq",
            ["h5ad"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "single_cell_analysis"

    def test_spatial_transcriptomics_analysis(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "spatial_transcriptomics",
            ["h5ad"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "spatial_transcriptomics"

    def test_proteomics_analysis(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "proteomics",
            ["csv"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "proteomics"

    def test_metabolomics_analysis(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline_for_analysis(
            "metabolomics",
            ["csv"],
        )
        assert len(pipeline) >= 1
        assert pipeline[-1].id == "metabolomics"

    def test_analysis_type_without_goal_capability_returns_empty_without_warning(self, caplog):
        graph = CapabilityGraph.default()
        with caplog.at_level("WARNING"):
            pipeline = graph.trace_pipeline_for_analysis(
                "run_reporting",
                ["json"],
            )
        assert pipeline == []
        assert "Unknown goal capability" not in caplog.text

    def test_direct_skill_smoke_returns_empty_without_warning(self, caplog):
        graph = CapabilityGraph.default()
        with caplog.at_level("WARNING"):
            pipeline = graph.trace_pipeline_for_analysis(
                "direct_skill_smoke",
                ["bam", "fasta_reference"],
            )
        assert pipeline == []
        assert "Unknown goal capability" not in caplog.text


# ---------------------------------------------------------------------------
# Tool options
# ---------------------------------------------------------------------------

class TestToolOptions:
    def test_read_trimming_exposes_cutadapt_and_fastp(self):
        graph = CapabilityGraph.default()
        trimming = graph.nodes["read_trimming"]
        assert trimming.tool_options == ["cutadapt_run", "fastp_run"]

    def test_sequence_alignment_msa_exposes_mafft_align(self):
        graph = CapabilityGraph.default()
        msa = graph.nodes["sequence_alignment_msa"]
        assert msa.tool_options == ["mafft_align"]

    def test_alignment_qc_exposes_samtools_utilities(self):
        graph = CapabilityGraph.default()
        qc = graph.nodes["alignment_qc"]
        assert qc.tool_options == ["samtools_flagstat", "samtools_idxstats", "samtools_stats"]

    def test_interval_operations_exposes_bedtools_utilities(self):
        graph = CapabilityGraph.default()
        interval_ops = graph.nodes["interval_operations"]
        assert interval_ops.tool_options == ["bedtools_intersect", "bedtools_coverage"]

    def test_coverage_profiling_exposes_bedtools_genomecov(self):
        graph = CapabilityGraph.default()
        coverage = graph.nodes["coverage_profiling"]
        assert coverage.tool_options == ["bedtools_genomecov"]

    def test_structural_variant_calling_exposes_sniffles(self):
        graph = CapabilityGraph.default()
        structural_variant = graph.nodes["structural_variant_calling"]
        assert structural_variant.tool_options == ["sniffles_sv_call"]

    def test_long_read_assembly_exposes_flye(self):
        graph = CapabilityGraph.default()
        assembly = graph.nodes["long_read_assembly"]
        assert assembly.tool_options == ["flye_assemble"]

    def test_long_read_rna_exposes_minimap2(self):
        graph = CapabilityGraph.default()
        long_read_rna = graph.nodes["long_read_rna"]
        assert long_read_rna.tool_options == ["minimap2_align"]

    def test_proteomics_exposes_differential_abundance_wrapper(self):
        graph = CapabilityGraph.default()
        proteomics = graph.nodes["proteomics"]
        assert proteomics.tool_options == ["proteomics_diff_abundance"]

    def test_metabolomics_exposes_differential_abundance_wrapper(self):
        graph = CapabilityGraph.default()
        metabolomics = graph.nodes["metabolomics"]
        assert metabolomics.tool_options == ["metabolomics_diff_abundance"]

    def test_tool_options_for_pipeline(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline("variant_calling", ["fastq", "fasta_reference"])
        options = graph.tool_options_for_pipeline(pipeline)
        assert len(options) >= 2
        # alignment node should have alignment tools
        alignment_node = pipeline[0]
        assert len(options[alignment_node.id]) >= 2

    def test_preferred_tools_ranked_first(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline("variant_calling", ["fastq", "fasta_reference"])
        options = graph.tool_options_for_pipeline(
            pipeline, preferred_tools=["bowtie2_align"]
        )
        alignment_node = pipeline[0]
        if "bowtie2_align" in options[alignment_node.id]:
            assert options[alignment_node.id][0] == "bowtie2_align"


# ---------------------------------------------------------------------------
# Plan skeleton
# ---------------------------------------------------------------------------

class TestPlanSkeleton:
    def test_suggest_skeleton(self):
        graph = CapabilityGraph.default()
        skeleton = graph.suggest_plan_skeleton(
            "variant_calling",
            ["fastq", "fasta_reference"],
        )
        assert len(skeleton) >= 2
        assert skeleton[0]["step_number"] == 1
        assert "tool_name" in skeleton[0]
        assert "purpose" in skeleton[0]
        assert "capability" in skeleton[0]

    def test_empty_skeleton_for_unknown_goal(self):
        graph = CapabilityGraph.default()
        skeleton = graph.suggest_plan_skeleton("nonexistent", ["fastq"])
        assert skeleton == []


# ---------------------------------------------------------------------------
# Tool equivalence
# ---------------------------------------------------------------------------

class TestToolEquivalence:
    def test_alternatives_for_bwa(self):
        graph = CapabilityGraph.default()
        alts = graph.alternatives_for_tool("bwa_mem_align")
        assert "bowtie2_align" in alts

    def test_alternatives_for_freebayes(self):
        graph = CapabilityGraph.default()
        alts = graph.alternatives_for_tool("freebayes_call")
        assert "gatk_haplotypecaller" in alts or "bcftools_call" in alts

    def test_alternatives_for_unknown(self):
        graph = CapabilityGraph.default()
        alts = graph.alternatives_for_tool("nonexistent_tool")
        assert alts == []

    def test_build_equivalence_map(self):
        graph = CapabilityGraph.default()
        eq_map = graph.build_equivalence_map()
        assert "bwa_mem_align" in eq_map
        assert "bowtie2_align" in eq_map["bwa_mem_align"]
        assert "salmon_quant" in eq_map
        assert "kallisto_quant" in eq_map["salmon_quant"]
        assert "stringtie_quant" in eq_map["salmon_quant"]


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------

class TestPromptFormatting:
    def test_format_pipeline(self):
        graph = CapabilityGraph.default()
        pipeline = graph.trace_pipeline("variant_calling", ["fastq", "fasta_reference"])
        text = graph.format_pipeline_for_prompt(pipeline)
        assert "suggested_workflow:" in text
        assert "short_read_alignment" in text
        assert "variant_calling" in text

    def test_summary(self):
        graph = CapabilityGraph.default()
        summary = graph.summary()
        assert "CapabilityGraph:" in summary
        assert "nodes" in summary


# ---------------------------------------------------------------------------
# Analysis type coverage
# ---------------------------------------------------------------------------

class TestAnalysisTypeCoverage:
    def test_all_analysis_types_mapped(self):
        from bio_harness.core.analysis_spec import CANONICAL_ANALYSIS_TYPES
        from bio_harness.core.capability_graph import _ANALYSIS_TYPES_WITHOUT_GOAL_CAPABILITY
        for at in CANONICAL_ANALYSIS_TYPES:
            assert at in _ANALYSIS_TYPE_TO_GOAL or at in _ANALYSIS_TYPES_WITHOUT_GOAL_CAPABILITY, (
                f"Missing mapping for: {at}"
            )
