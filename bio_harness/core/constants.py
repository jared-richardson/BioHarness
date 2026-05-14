"""Shared constants for the bio_harness runtime.

Centralises timing defaults, regex patterns, and tool metadata that are
used by both the CLI harness (``run_agent_e2e.py``) and the Streamlit
UI (``app.py``).
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Timing defaults
# ---------------------------------------------------------------------------

DEFAULT_HEARTBEAT_SECONDS: int = 15
"""Interval (seconds) between heartbeat ticks in the execution loop."""

DEFAULT_STALL_TIMEOUT_SECONDS: int = 45
"""Seconds of silence before a running step is considered stalled."""

DEFAULT_LIVE_PROCESS_GRACE_SECONDS: int = 900
"""Extended grace period (seconds) when an executor PID is still alive."""

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

PID_STATUS_RE = re.compile(r"\bpid=(\d+)\b")
"""Extracts a numeric PID from a status/log line."""

# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

HEAVY_TOOL_NAMES: frozenset[str] = frozenset({
    "star_align",
    "star_2pass_align",
    "hisat2_align",
    "star_solo_count",
    "fastqc_run",
})
"""Tools that are RAM- or CPU-intensive and may need special scheduling."""

TOOL_STALL_GRACE_HINTS: dict[str, int] = {
    "bcftools_call": 1800,
    "freebayes_call": 1800,
    "gatk_haplotypecaller": 3600,
    "gatk_mutect2_call": 3600,
    "spades_assemble": 3600,
    "prokka_annotate": 2400,
    "subread_align": 2700,
    "varscan_call": 1800,
    "star_align": 2700,
    "star_2pass_align": 2700,
    "hisat2_align": 2700,
    "bwa_mem_align": 2700,
    "bowtie2_align": 2400,
    "minimap2_align": 3000,
    "sniffles_sv_call": 3000,
    "star_solo_count": 3000,
    "deseq2_run": 600,
    "edger_run": 600,
    "limma_voom_run": 600,
    "scanpy_workflow": 600,
    "stringtie_quant": 600,
    "dexseq_run": 1800,
    "seurat_rscript_workflow": 900,
    "sc_count_and_cluster": 1200,
    "majiq_run": 1800,
    "rmats_run": 1800,
}
"""Per-tool stall grace overrides (seconds).  Longer timeouts for tools
that legitimately run for extended periods (e.g. genome alignment)."""

PLAN_TOOL_EXEC_HINTS: dict[str, list[str]] = {
    "star_align": ["star"],
    "star_2pass_align": ["star"],
    "star_solo_count": ["star"],
    "hisat2_align": ["hisat2"],
    "bwa_mem_align": ["bwa"],
    "bowtie2_align": ["bowtie2"],
    "minimap2_align": ["minimap2"],
    "featurecounts_run": ["featureCounts"],
    "deseq2_run": ["Rscript"],
    "edger_run": ["Rscript"],
    "limma_voom_run": ["Rscript"],
    "gatk_haplotypecaller": ["gatk"],
    "gatk_mutect2_call": ["gatk"],
    "bcftools_call": ["bcftools"],
    "freebayes_call": ["freebayes"],
    "subread_align": ["subread", "subjunc"],
    "stringtie_quant": ["stringtie"],
    "varscan_call": ["varscan"],
    "dexseq_run": ["Rscript"],
    "majiq_run": ["majiq"],
    "rmats_run": ["rmats"],
    "blastp_search": ["blastp"],
    "blastn_search": ["blastn"],
    "blastx_search": ["blastx"],
    "tblastx_search": ["tblastx"],
    "tblastn_search": ["tblastn"],
    "psiblast_search": ["psiblast"],
    "deltablast_search": ["deltablast"],
    "rpsblast_search": ["rpsblast"],
    "rpstblastn_search": ["rpstblastn"],
    "makeblastdb_run": ["makeblastdb"],
    "blast_formatter_run": ["blast_formatter"],
    "blastdbcmd_run": ["blastdbcmd"],
    "blastdbcheck_run": ["blastdbcheck"],
    "blastdb_aliastool_run": ["blastdb_aliastool"],
    "makeprofiledb_run": ["makeprofiledb"],
    "bedtools_intersect": ["bedtools"],
    "bedtools_coverage": ["bedtools"],
    "bedtools_genomecov": ["bedtools"],
    "hmmscan_search": ["hmmscan"],
    "prokka_annotate": ["prokka"],
    "macs2_chipseq_callpeak": ["macs2"],
    "macs2_atacseq_callpeak": ["macs2"],
    "mafft_align": ["mafft"],
    "samtools_flagstat": ["samtools"],
    "samtools_idxstats": ["samtools"],
    "samtools_stats": ["samtools"],
    "sniffles_sv_call": ["sniffles"],
    "methylation_bismark_style": ["bismark"],
    "metagenomics_kraken2_bracken_style": ["kraken2", "bracken"],
    "fusion_star_fusion_style": ["STAR-Fusion", "star-fusion"],
    "cnv_cnvkit_style": ["cnvkit.py"],
    "immune_repertoire_mixcr_style": ["mixcr"],
    "phylogenetics_iqtree_style": ["iqtree2", "iqtree"],
}
"""Maps tool names to the executable binaries they require on PATH."""

PLAN_INPUT_PATH_KEYS: dict[str, list[str]] = {
    "spades_assemble": ["reads_1", "reads_2"],
    "minimap2_align": ["reads", "reads_1", "reads_2", "reference_fasta"],
    "bwa_mem_align": ["reads_1", "reads_2", "reference_fasta"],
    "bowtie2_align": ["reads_1", "reads_2", "reference_fasta"],
    "hisat2_align": ["reads_1", "reads_2", "reference_fasta"],
    "subread_align": ["reads_1", "reads_2", "reference_fasta"],
    "star_align": ["reads_1", "reads_2", "reference_fasta", "annotation_gtf"],
    "star_2pass_align": ["reads_1", "reads_2", "reference_fasta", "annotation_gtf"],
    "gatk_haplotypecaller": ["reference_fasta", "input_bam"],
    "gatk_mutect2_call": ["reference_fasta", "tumor_bam", "normal_bam"],
    "bcftools_call": ["reference_fasta", "input_bam"],
    "freebayes_call": ["reference_fasta", "input_bam"],
    "stringtie_quant": ["input_bam", "annotation_gtf"],
    "varscan_call": ["reference_fasta", "input_bam"],
    "rmats_run": ["group1_bams", "group2_bams", "annotation_gtf"],
    "methylation_bismark_style": ["genome_folder", "reads_1", "reads_2"],
    "metagenomics_kraken2_bracken_style": ["database", "reads_1", "reads_2"],
    "fusion_star_fusion_style": ["genome_lib_dir", "reads_1", "reads_2"],
    "cnv_cnvkit_style": ["input_bam", "reference_fasta"],
    "immune_repertoire_mixcr_style": ["reads_1", "reads_2"],
    "mafft_align": ["input_fasta"],
    "sniffles_sv_call": ["input_bam", "reference_fasta"],
    "phylogenetics_iqtree_style": ["alignment_fasta"],
    "blastn_search": ["query_fasta", "database"],
    "blastx_search": ["query_fasta", "database"],
    "tblastx_search": ["query_fasta", "database"],
    "tblastn_search": ["query_fasta", "database"],
    "psiblast_search": ["query_fasta", "database"],
    "deltablast_search": ["query_fasta", "database"],
    "rpsblast_search": ["query_fasta", "database"],
    "rpstblastn_search": ["query_fasta", "database"],
    "makeblastdb_run": ["input_fasta"],
    "blast_formatter_run": ["archive_file"],
    "blastdbcmd_run": ["database", "entry_batch"],
    "blastdbcheck_run": ["directory"],
    "blastdb_aliastool_run": ["dblist"],
    "makeprofiledb_run": ["input_list"],
    "bedtools_intersect": ["a_intervals", "b_intervals"],
    "bedtools_coverage": ["a_intervals", "b_features"],
    "bedtools_genomecov": ["input_bam", "input_bed", "genome_file"],
    "samtools_flagstat": ["input_bam"],
    "samtools_idxstats": ["input_bam"],
    "samtools_stats": ["input_bam", "reference_fasta"],
}
"""Maps tool names to the argument keys that hold input file paths.
Used for automatic path resolution and validation."""
