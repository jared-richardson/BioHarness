"""CapabilityGraph — directed graph of bioinformatics capabilities with
typed input/output contracts.

Given a goal (e.g. "variant_calling") and available input data types
(e.g. ["fastq", "fasta_reference"]), the graph can backward-trace to
find the required pipeline of capability nodes.

The graph is built automatically from the capability catalog and
enriched skill definitions (input_types, output_types, analysis_categories).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------

@dataclass
class CapabilityNode:
    """A capability node in the analysis graph.

    Each node represents a functional capability (alignment, quantification,
    variant calling, etc.) with typed input/output contracts and the set of
    tools that can fill that capability.
    """

    id: str  # e.g. "short_read_alignment"
    name: str  # e.g. "Short Read Alignment"
    description: str
    category: str  # e.g. "alignment", "quantification"
    input_types: List[str]  # e.g. ["fastq", "fasta_reference"]
    output_types: List[str]  # e.g. ["bam"]
    tool_options: List[str]  # e.g. ["bwa_mem_align", "bowtie2_align"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "input_types": self.input_types,
            "output_types": self.output_types,
            "tool_options": self.tool_options,
        }


# ---------------------------------------------------------------------------
# Pre-defined capability nodes
# ---------------------------------------------------------------------------
# These define the core bioinformatics capability vocabulary.
# The graph connects them based on input/output type compatibility.

_DEFAULT_CAPABILITY_NODES: List[Dict[str, Any]] = [
    {
        "id": "quality_control",
        "name": "Quality Control",
        "description": "Assess sequencing data quality",
        "category": "qc",
        "input_types": ["fastq"],
        "output_types": ["html", "zip"],
        "tool_options": ["fastqc_run"],
    },
    {
        "id": "alignment_qc",
        "name": "Alignment QC",
        "description": "Summarize BAM or CRAM alignment quality and per-reference mapping metrics",
        "category": "qc",
        "input_types": ["bam"],
        "output_types": ["txt", "tsv"],
        "tool_options": ["samtools_flagstat", "samtools_idxstats", "samtools_stats"],
    },
    {
        "id": "artifact_schema_profiling",
        "name": "Artifact Schema Profiling",
        "description": "Inspect completed artifacts and emit compact schema summaries",
        "category": "reporting",
        "input_types": ["csv", "tsv", "vcf", "gtf", "gff", "jsonl"],
        "output_types": ["json"],
        "tool_options": ["artifact_schema_profile"],
    },
    {
        "id": "run_reporting",
        "name": "Run Reporting",
        "description": "Build post-run report bundles and rendered researcher-facing summaries",
        "category": "reporting",
        "input_types": ["directory", "json"],
        "output_types": ["directory", "html", "markdown", "pdf", "docx"],
        "tool_options": ["multiqc_report", "quarto_report"],
    },
    {
        "id": "read_trimming",
        "name": "Read Trimming & Filtering",
        "description": "Trim adapters and filter low-quality reads",
        "category": "preprocessing",
        "input_types": ["fastq"],
        "output_types": ["fastq"],
        "tool_options": ["cutadapt_run", "fastp_run"],
    },
    {
        "id": "short_read_alignment",
        "name": "Short Read Alignment",
        "description": "Align short DNA reads to a reference genome",
        "category": "alignment",
        "input_types": ["fastq", "fasta_reference"],
        "output_types": ["bam"],
        "tool_options": ["bwa_mem_align", "bowtie2_align", "minimap2_align", "subread_align"],
    },
    {
        "id": "spliced_alignment",
        "name": "Splice-Aware RNA-seq Alignment",
        "description": "Align RNA-seq reads with splice-junction awareness",
        "category": "alignment",
        "input_types": ["fastq", "fasta_reference"],
        "output_types": ["bam"],
        "tool_options": ["star_align", "star_2pass_align", "hisat2_align"],
    },
    {
        "id": "long_read_alignment",
        "name": "Long Read Alignment",
        "description": "Align long reads (PacBio/Nanopore) to a reference",
        "category": "alignment",
        "input_types": ["fastq", "fasta_reference"],
        "output_types": ["bam"],
        "tool_options": ["minimap2_align"],
    },
    {
        "id": "long_read_assembly",
        "name": "Long Read Assembly",
        "description": "Assemble long-read sequencing data into contigs with a long-read assembler",
        "category": "assembly",
        "input_types": ["fastq"],
        "output_types": ["fasta"],
        "tool_options": ["flye_assemble"],
    },
    {
        "id": "long_read_rna",
        "name": "Long Read RNA Alignment",
        "description": "Align long-read RNA sequencing data with a splice-aware long-read aligner",
        "category": "alignment",
        "input_types": ["fastq", "fasta_reference"],
        "output_types": ["bam"],
        "tool_options": ["minimap2_align"],
    },
    {
        "id": "transcript_quantification",
        "name": "Transcript Quantification",
        "description": "Quantify transcript abundance from transcriptomes or aligned RNA-seq reads",
        "category": "quantification",
        "input_types": ["fastq", "fasta_transcriptome", "bam", "gtf"],
        "output_types": ["tsv", "gtf"],
        "tool_options": ["salmon_quant", "kallisto_quant", "stringtie_quant"],
    },
    {
        "id": "gene_counting",
        "name": "Gene-Level Read Counting",
        "description": "Count reads per gene from aligned BAM using GTF annotation",
        "category": "quantification",
        "input_types": ["bam", "gtf"],
        "output_types": ["tsv"],
        "tool_options": ["featurecounts_run"],
    },
    {
        "id": "differential_expression",
        "name": "Differential Gene Expression",
        "description": "Identify differentially expressed genes between conditions",
        "category": "differential_analysis",
        "input_types": ["tsv", "csv"],
        "output_types": ["tsv", "csv"],
        "tool_options": ["deseq2_run", "edger_run", "limma_voom_run"],
    },
    {
        "id": "variant_calling",
        "name": "Variant Calling",
        "description": "Call SNPs/indels from aligned reads",
        "category": "variant_calling",
        "input_types": ["bam", "fasta_reference"],
        "output_types": ["vcf"],
        "tool_options": ["gatk_haplotypecaller", "freebayes_call", "bcftools_call", "varscan_call"],
    },
    {
        "id": "somatic_variant_calling",
        "name": "Somatic Variant Calling",
        "description": "Call somatic mutations from tumor/normal pairs",
        "category": "variant_calling",
        "input_types": ["bam", "fasta_reference"],
        "output_types": ["vcf"],
        "tool_options": ["gatk_mutect2_call"],
    },
    {
        "id": "structural_variant_calling",
        "name": "Structural Variant Calling",
        "description": "Call structural variants from aligned long-read sequencing data",
        "category": "variant_calling",
        "input_types": ["bam", "fasta_reference"],
        "output_types": ["vcf"],
        "tool_options": ["sniffles_sv_call"],
    },
    {
        "id": "variant_annotation",
        "name": "Variant Annotation",
        "description": "Annotate variants with predicted functional impact",
        "category": "annotation",
        "input_types": ["vcf", "fasta_reference", "gff"],
        "output_types": ["vcf"],
        "tool_options": ["snpeff_annotate", "vep_annotate"],
    },
    {
        "id": "genome_annotation",
        "name": "Genome Annotation",
        "description": "Predict and annotate genes in assembled genomes",
        "category": "annotation",
        "input_types": ["fasta"],
        "output_types": ["gff", "fasta_protein"],
        "tool_options": ["prokka_annotate", "prodigal_annotate"],
    },
    {
        "id": "genome_assembly",
        "name": "Genome Assembly",
        "description": "De novo assembly of sequencing reads into contigs",
        "category": "assembly",
        "input_types": ["fastq"],
        "output_types": ["fasta"],
        "tool_options": ["spades_assemble", "flye_assemble"],
    },
    {
        "id": "transcriptome_assembly",
        "name": "Transcriptome Assembly",
        "description": "De novo transcriptome assembly from RNA-seq",
        "category": "assembly",
        "input_types": ["fastq"],
        "output_types": ["fasta"],
        "tool_options": ["trinity_assemble"],
    },
    {
        "id": "sequence_alignment_msa",
        "name": "Multiple Sequence Alignment",
        "description": "Align multiple sequences for comparative analysis",
        "category": "comparative",
        "input_types": ["fasta"],
        "output_types": ["fasta_alignment"],
        "tool_options": ["mafft_align"],
    },
    {
        "id": "phylogenetic_inference",
        "name": "Phylogenetic Tree Inference",
        "description": "Infer evolutionary relationships from aligned sequences",
        "category": "phylogenetics",
        "input_types": ["fasta_alignment"],
        "output_types": ["newick"],
        "tool_options": ["phylogenetics_iqtree_style"],
    },
    {
        "id": "single_cell_counting",
        "name": "Single-Cell Counting",
        "description": "Generate gene expression count matrix from scRNA-seq",
        "category": "single_cell",
        "input_types": ["fastq", "fasta_reference"],
        "output_types": ["h5ad", "tsv"],
        "tool_options": ["cellranger_count", "star_solo_count", "sc_count_and_cluster"],
    },
    {
        "id": "single_cell_analysis",
        "name": "Single-Cell Analysis",
        "description": "Preprocessing, clustering, and analysis of scRNA-seq data",
        "category": "single_cell",
        "input_types": ["h5ad", "csv", "mtx"],
        "output_types": ["h5ad", "csv", "png"],
        "tool_options": ["scanpy_workflow", "seurat_rscript_workflow"],
    },
    {
        "id": "spatial_transcriptomics",
        "name": "Spatial Transcriptomics",
        "description": "Processed-input spatial domain identification and marker analysis",
        "category": "spatial",
        "input_types": ["h5ad"],
        "output_types": ["h5ad", "csv", "json", "md"],
        "tool_options": ["spatial_transcriptomics_workflow"],
    },
    {
        "id": "metabolomics",
        "name": "Metabolomics Differential Abundance",
        "description": "Processed-input differential metabolite-feature abundance analysis from feature tables and metadata",
        "category": "metabolomics",
        "input_types": ["csv"],
        "output_types": ["csv", "tsv", "json", "md"],
        "tool_options": ["metabolomics_diff_abundance"],
    },
    {
        "id": "proteomics",
        "name": "Proteomics Differential Abundance",
        "description": "Processed-input differential protein abundance analysis from abundance tables and metadata",
        "category": "proteomics",
        "input_types": ["csv"],
        "output_types": ["csv", "tsv", "json", "md"],
        "tool_options": ["proteomics_diff_abundance"],
    },
    {
        "id": "metagenomic_classification",
        "name": "Metagenomic Classification",
        "description": "Classify metagenomic reads by taxonomy",
        "category": "metagenomics",
        "input_types": ["fastq"],
        "output_types": ["tsv"],
        "tool_options": ["metagenomics_kraken2_bracken_style"],
    },
    {
        "id": "peak_calling",
        "name": "ChIP/ATAC-seq Peak Calling",
        "description": "Identify enriched genomic regions from ChIP or ATAC-seq",
        "category": "epigenomics",
        "input_types": ["bam"],
        "output_types": ["bed", "tsv"],
        "tool_options": ["macs2_chipseq_callpeak", "macs2_atacseq_callpeak"],
    },
    {
        "id": "interval_operations",
        "name": "Genomic Interval Operations",
        "description": "Intersect, count, and summarize genomic interval overlaps",
        "category": "intervals",
        "input_types": ["bed"],
        "output_types": ["bed", "tsv"],
        "tool_options": ["bedtools_intersect", "bedtools_coverage"],
    },
    {
        "id": "coverage_profiling",
        "name": "Coverage Profiling",
        "description": "Generate genome-wide coverage tracks or depth summaries from aligned reads",
        "category": "intervals",
        "input_types": ["bam"],
        "output_types": ["bedgraph"],
        "tool_options": ["bedtools_genomecov"],
    },
    {
        "id": "protein_search",
        "name": "Protein Sequence Search",
        "description": "Search protein sequences against databases",
        "category": "comparative",
        "input_types": ["fasta_protein"],
        "output_types": ["tsv", "xml"],
        "tool_options": [
            "blastp_search",
            "blastn_search",
            "blastx_search",
            "tblastn_search",
            "tblastx_search",
            "psiblast_search",
            "deltablast_search",
            "rpsblast_search",
            "rpstblastn_search",
            "hmmscan_search",
        ],
    },
    {
        "id": "splicing_analysis",
        "name": "Alternative Splicing Analysis",
        "description": "Detect differential splicing events between conditions",
        "category": "splicing",
        "input_types": ["bam", "gtf"],
        "output_types": ["tsv"],
        "tool_options": ["rmats_run", "dexseq_run", "majiq_run"],
    },
]


# ---------------------------------------------------------------------------
# Analysis type → goal capability mapping
# ---------------------------------------------------------------------------

_ANALYSIS_TYPE_TO_GOAL: Dict[str, str] = {
    "bacterial_evolution_variant_calling": "variant_calling",
    "rna_seq_differential_expression": "differential_expression",
    "transcript_quantification": "transcript_quantification",
    "metagenomics_classification": "metagenomic_classification",
    "single_cell_rna_seq": "single_cell_analysis",
    "spatial_transcriptomics": "spatial_transcriptomics",
    "metabolomics": "metabolomics",
    "proteomics": "proteomics",
    "germline_variant_calling": "variant_calling",
    "somatic_variant_calling": "variant_calling",
    "variant_annotation": "variant_annotation",
    "comparative_genomics": "protein_search",
    "viral_metagenomics": "metagenomic_classification",
    "multi_model_dge_pathway": "differential_expression",
    "phylogenetics": "phylogenetic_inference",
    "structural_variant_calling": "structural_variant_calling",
    "long_read_assembly": "long_read_assembly",
    "long_read_rna": "long_read_rna",
}

_ANALYSIS_TYPES_WITHOUT_GOAL_CAPABILITY: Set[str] = {
    "artifact_schema_profiling",
    "direct_skill_smoke",
    "run_reporting",
}


# ---------------------------------------------------------------------------
# Input type grouping for alternative-vs-required logic
# ---------------------------------------------------------------------------
# Types in the same group are alternatives (OR); different groups are
# co-requirements (AND).

_TYPE_GROUPS: Dict[str, str] = {
    # Tabular / matrix data (interchangeable for count data)
    "tsv": "tabular_or_matrix",
    "csv": "tabular_or_matrix",
    "txt": "tabular_or_matrix",
    "h5ad": "tabular_or_matrix",
    "mtx": "tabular_or_matrix",
    "h5": "tabular_or_matrix",
    # Sequencing reads group
    "fastq": "reads",
    # Reference genome group
    "fasta_reference": "reference",
    "fasta": "sequence",
    "fasta_transcriptome": "transcriptome",
    "fasta_alignment": "alignment",
    # Annotation groups
    "gff": "annotation",
    "gtf": "annotation",
    # Aligned reads
    "bam": "aligned_reads",
    # Variants
    "vcf": "variants",
    # Other
    "newick": "tree",
    "fasta_protein": "protein",
    "html": "report",
    "zip": "report",
    "bed": "intervals",
    "xml": "structured",
    "png": "image",
}


def _group_input_types(input_types: List[str]) -> List[List[str]]:
    """Group input types into co-requirement groups.

    Types in the same group are alternatives (any one suffices);
    types in different groups are ALL required.

    Example:
        ["tsv", "csv"] → [["tsv", "csv"]]  # one group, alternatives
        ["fastq", "fasta_reference"] → [["fastq"], ["fasta_reference"]]  # two groups, both needed
        ["bam", "fasta_reference"] → [["bam"], ["fasta_reference"]]
        ["h5ad", "csv", "mtx"] → [["h5ad", "mtx"], ["csv"]]  # sc_matrix group + tabular
    """
    group_buckets: Dict[str, List[str]] = {}
    for t in input_types:
        group_name = _TYPE_GROUPS.get(t, t)
        group_buckets.setdefault(group_name, []).append(t)
    return list(group_buckets.values())


# ---------------------------------------------------------------------------
# CapabilityGraph
# ---------------------------------------------------------------------------

class CapabilityGraph:
    """Directed graph of bioinformatics capabilities with type contracts.

    Nodes represent capabilities (alignment, variant calling, etc.).
    Edges are inferred from output_types → input_types compatibility:
    if capability A produces ``bam`` and capability B requires ``bam``,
    we add an edge A → B.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, CapabilityNode] = {}
        # edges: (producer_id, consumer_id)
        self.edges: List[Tuple[str, str]] = []
        # Lookup: output_type → list of producer node IDs
        self._producers_by_output: Dict[str, List[str]] = defaultdict(list)
        # Lookup: consumer_id → list of required input types
        self._consumer_inputs: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> "CapabilityGraph":
        """Build a graph from the built-in capability node definitions."""
        graph = cls()
        for node_dict in _DEFAULT_CAPABILITY_NODES:
            graph.add_node(CapabilityNode(**node_dict))
        graph.infer_edges()
        return graph

    @classmethod
    def from_catalog_and_skills(
        cls,
        catalog: Dict[str, Any],
        skills: Sequence[Dict[str, Any]],
    ) -> "CapabilityGraph":
        """Build graph from capability catalog + enriched skill definitions.

        Skills contribute tool_options; catalog contributes descriptions.
        Falls back to default nodes for any unmatched capabilities.
        """
        graph = cls.default()
        # Augment tool_options from enriched skills
        for skill in skills:
            skill_name = str(skill.get("name", "")).strip()
            input_types = skill.get("input_types", []) or []
            output_types = skill.get("output_types", []) or []
            if not skill_name:
                continue
            # Match skill to existing nodes by checking if the skill's
            # input/output types overlap significantly with a node
            for node in graph.nodes.values():
                node_inputs = set(node.input_types)
                node_outputs = set(node.output_types)
                skill_inputs = set(input_types)
                skill_outputs = set(output_types)
                if (
                    skill_inputs & node_inputs
                    and skill_outputs & node_outputs
                    and skill_name not in node.tool_options
                ):
                    node.tool_options.append(skill_name)
        graph.infer_edges()
        return graph

    def add_node(self, node: CapabilityNode) -> None:
        """Add a capability node to the graph."""
        self.nodes[node.id] = node
        for otype in node.output_types:
            if node.id not in self._producers_by_output[otype]:
                self._producers_by_output[otype].append(node.id)
        self._consumer_inputs[node.id] = set(node.input_types)

    def infer_edges(self) -> None:
        """Infer edges from output_type → input_type compatibility.

        An edge producer → consumer exists when any of producer's
        output_types appears in consumer's input_types.
        """
        self.edges = []
        edge_set: Set[Tuple[str, str]] = set()
        for consumer_id, consumer_node in self.nodes.items():
            for needed_type in consumer_node.input_types:
                for producer_id in self._producers_by_output.get(needed_type, []):
                    if producer_id == consumer_id:
                        continue
                    pair = (producer_id, consumer_id)
                    if pair not in edge_set:
                        edge_set.add(pair)
                        self.edges.append(pair)

    # ------------------------------------------------------------------
    # Pipeline tracing
    # ------------------------------------------------------------------

    def trace_pipeline(
        self,
        goal: str,
        available_data: List[str],
        *,
        max_depth: int = 10,
    ) -> List[CapabilityNode]:
        """Backward-trace from *goal* to *available_data*.

        Returns an ordered list of capability nodes forming a valid
        pipeline from the available data to the goal capability.

        Args:
            goal: Capability ID or analysis type string.
            available_data: List of file type strings available as inputs
                (e.g. ["fastq", "fasta_reference"]).
            max_depth: Maximum trace depth to prevent infinite loops.

        Example::

            trace_pipeline("variant_calling", ["fastq", "fasta_reference"])
            # → [short_read_alignment, variant_calling]
        """
        # Resolve analysis_type to goal capability
        if goal in _ANALYSIS_TYPE_TO_GOAL:
            goal = _ANALYSIS_TYPE_TO_GOAL[goal]

        if goal not in self.nodes:
            logger.warning("Unknown goal capability: %s", goal)
            return []

        available = set(available_data)
        pipeline: List[CapabilityNode] = []
        visited: Set[str] = set()

        def _inputs_satisfied(node: CapabilityNode) -> bool:
            """Check if a node's inputs are satisfied by available data.

            Input types within the same type group (e.g. tsv/csv) are
            treated as alternatives (OR), while types from different
            groups (e.g. fastq + fasta_reference) are co-requirements
            (AND).
            """
            type_groups = _group_input_types(node.input_types)
            for group in type_groups:
                if not (set(group) & available):
                    return False
            return True

        def _trace(node_id: str, depth: int) -> bool:
            if depth > max_depth or node_id in visited:
                return False
            visited.add(node_id)
            node = self.nodes[node_id]
            if _inputs_satisfied(node):
                pipeline.append(node)
                available.update(node.output_types)
                return True

            # Need to find producers for missing input type groups
            type_groups = _group_input_types(node.input_types)
            for group in type_groups:
                if set(group) & available:
                    continue  # this group is satisfied
                # Try to find a producer for any type in this group
                group_satisfied = False
                for missing_type in group:
                    producers = self._producers_by_output.get(missing_type, [])
                    for producer_id in producers:
                        if producer_id in visited:
                            continue
                        if _trace(producer_id, depth + 1):
                            group_satisfied = True
                            break
                    if group_satisfied:
                        break
                if not group_satisfied:
                    return False

            # Re-check after tracing producers
            if _inputs_satisfied(node):
                pipeline.append(node)
                available.update(node.output_types)
                return True
            return False

        _trace(goal, 0)
        return pipeline

    def trace_pipeline_for_analysis(
        self,
        analysis_type: str,
        available_data: List[str],
    ) -> List[CapabilityNode]:
        """Convenience wrapper that maps analysis_type to goal."""
        if (
            analysis_type in _ANALYSIS_TYPES_WITHOUT_GOAL_CAPABILITY
            and analysis_type not in self.nodes
        ):
            logger.debug(
                "No capability-graph goal is defined for analysis type: %s",
                analysis_type,
            )
            return []
        goal = _ANALYSIS_TYPE_TO_GOAL.get(analysis_type, analysis_type)
        return self.trace_pipeline(goal, available_data)

    # ------------------------------------------------------------------
    # Tool options
    # ------------------------------------------------------------------

    def tool_options_for_pipeline(
        self,
        pipeline: List[CapabilityNode],
        *,
        preferred_tools: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        """For each node in the pipeline, return ranked tool options.

        Preferred tools are ranked first.
        """
        preferred = set(preferred_tools or [])
        result: Dict[str, List[str]] = {}
        for node in pipeline:
            options = list(node.tool_options)
            if preferred:
                pref_first = [t for t in options if t in preferred]
                rest = [t for t in options if t not in preferred]
                options = pref_first + rest
            result[node.id] = options
        return result

    # ------------------------------------------------------------------
    # Plan skeleton generation
    # ------------------------------------------------------------------

    def suggest_plan_skeleton(
        self,
        goal: str,
        available_data: List[str],
        preferred_tools: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate a plan skeleton from the traced pipeline.

        Returns a list of step dicts suitable for inclusion in an LLM
        analysis brief as a ``plan_skeleton``.
        """
        pipeline = self.trace_pipeline(goal, available_data)
        if not pipeline:
            return []

        tool_opts = self.tool_options_for_pipeline(
            pipeline, preferred_tools=preferred_tools
        )
        steps: List[Dict[str, Any]] = []
        for i, node in enumerate(pipeline, 1):
            options = tool_opts.get(node.id, [])
            tool_name = options[0] if options else "bash_run"
            step = {
                "step_number": i,
                "tool_name": tool_name,
                "purpose": node.description,
                "capability": node.id,
                "input_types": node.input_types,
                "output_types": node.output_types,
                "alternative_tools": options[1:] if len(options) > 1 else [],
            }
            steps.append(step)
        return steps

    # ------------------------------------------------------------------
    # LLM prompt formatting
    # ------------------------------------------------------------------

    def format_pipeline_for_prompt(
        self,
        pipeline: List[CapabilityNode],
        preferred_tools: Optional[List[str]] = None,
    ) -> str:
        """Format a traced pipeline as a text block for the LLM prompt."""
        if not pipeline:
            return ""
        tool_opts = self.tool_options_for_pipeline(
            pipeline, preferred_tools=preferred_tools
        )
        lines = ["suggested_workflow:"]
        for i, node in enumerate(pipeline, 1):
            in_str = ", ".join(node.input_types)
            out_str = ", ".join(node.output_types)
            options = tool_opts.get(node.id, [])
            tools_str = ", ".join(options[:4]) if options else "bash_run"
            lines.append(
                f"  {i}. {node.id} ({in_str} -> {out_str}) "
                f"-- tools: {tools_str}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool equivalence (replaces TOOL_EQUIVALENCE_MAP)
    # ------------------------------------------------------------------

    def alternatives_for_tool(self, tool_name: str) -> List[str]:
        """Find alternative tools for *tool_name* based on graph structure.

        Returns tools from the same capability node that can serve as
        substitutes.
        """
        alternatives: List[str] = []
        for node in self.nodes.values():
            if tool_name in node.tool_options:
                for alt in node.tool_options:
                    if alt != tool_name and alt not in alternatives:
                        alternatives.append(alt)
        return alternatives

    def build_equivalence_map(self) -> Dict[str, List[str]]:
        """Build a tool equivalence map from the graph structure.

        Returns a dict mapping each tool to its alternatives,
        equivalent to ``TOOL_EQUIVALENCE_MAP`` but derived dynamically.
        """
        eq_map: Dict[str, List[str]] = {}
        for node in self.nodes.values():
            for tool in node.tool_options:
                alts = [t for t in node.tool_options if t != tool]
                if alts:
                    eq_map[tool] = alts
        return eq_map

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable summary of the graph."""
        lines = [f"CapabilityGraph: {len(self.nodes)} nodes, {len(self.edges)} edges"]
        for node in self.nodes.values():
            tools_str = ", ".join(node.tool_options[:3]) or "(no tools)"
            lines.append(f"  {node.id}: {', '.join(node.input_types)} -> {', '.join(node.output_types)} [{tools_str}]")
        return "\n".join(lines)
