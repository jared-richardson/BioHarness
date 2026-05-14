from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from bio_harness.core.scientific_tool_catalog import load_scientific_tool_catalog


DEFAULT_CAPABILITY_CATALOG: Dict[str, Any] = {
    "version": 1,
    "capabilities": [
        {
            "id": "fastqc",
            "name": "Read QC",
            "description": "Quality control and summary reporting for sequencing reads.",
            "enabled": True,
            "keywords": ["fastqc", "qc", "quality control", "multiqc"],
            "plan_signals": ["fastqc", "multiqc"],
            "tool_hints": ["fastqc", "multiqc"],
        },
        {
            "id": "alignment_qc",
            "name": "Alignment QC",
            "description": "Alignment-level QC summaries and per-reference metrics from BAM or CRAM files.",
            "enabled": True,
            "keywords": [
                "alignment qc",
                "mapping qc",
                "samtools flagstat",
                "samtools idxstats",
                "samtools stats",
                "flagstat",
                "idxstats",
            ],
            "plan_signals": [
                "samtools flagstat",
                "samtools idxstats",
                "samtools stats",
                "samtools_flagstat",
                "samtools_idxstats",
                "samtools_stats",
                "flagstat",
                "idxstats",
            ],
            "tool_hints": [
                "samtools flagstat",
                "samtools idxstats",
                "samtools stats",
                "samtools_flagstat",
                "samtools_idxstats",
                "samtools_stats",
                "samtools",
            ],
        },
        {
            "id": "artifact_schema_profiling",
            "name": "Artifact Schema Profiling",
            "description": "Inspect completed artifacts and emit compact schema or data-dictionary summaries.",
            "enabled": True,
            "keywords": [
                "schema",
                "data dictionary",
                "artifact schema",
                "schema profile",
                "artifact_schema_profile",
            ],
            "plan_signals": [
                "schema",
                "data dictionary",
                "artifact schema",
                "schema profile",
                "artifact_schema_profile",
            ],
            "tool_hints": ["artifact_schema_profile"],
        },
        {
            "id": "run_reporting",
            "name": "Run Reporting",
            "description": "Build researcher-facing report bundles and rendered summaries from completed runs.",
            "enabled": True,
            "keywords": [
                "report bundle",
                "run report",
                "reporting",
                "multiqc",
                "quarto",
                "multiqc_report",
                "quarto_report",
            ],
            "plan_signals": [
                "report bundle",
                "run report",
                "reporting",
                "multiqc",
                "quarto",
                "multiqc_report",
                "quarto_report",
            ],
            "tool_hints": ["multiqc_report", "quarto_report"],
        },
        {
            "id": "alignment",
            "name": "Read Alignment",
            "description": "Short/long read alignment and mapping workflows.",
            "enabled": True,
            "keywords": [
                "alignment",
                "align",
                "mapping",
                "map reads",
                "star",
                "star_2pass_align",
                "star_solo_count",
                "hisat2",
                "hisat2_align",
                "bwa",
                "bwa-mem2",
                "minimap2",
                "bowtie",
                "subread",
                "subjunc",
                "subread_align",
            ],
            "plan_signals": [
                "star",
                "star_align",
                "star_2pass_align",
                "star_solo_count",
                "cellranger_count",
                "sc_count_and_cluster",
                "hisat2",
                "hisat2_align",
                "bwa",
                "bwa-mem2",
                "minimap2",
                "bowtie",
                "subread",
                "subjunc",
                "subread_align",
                "align",
                "infer_phylogeny_biopython.py",
                "classify_viral_reads_kmer.py",
            ],
            "tool_hints": [
                "star",
                "star_align",
                "star_2pass_align",
                "star_solo_count",
                "cellranger_count",
                "sc_count_and_cluster",
                "hisat2",
                "hisat2_align",
                "bwa",
                "bwa-mem2",
                "minimap2",
                "bowtie2",
                "subread",
                "subjunc",
                "subread_align",
            ],
        },
        {
            "id": "reference_inputs",
            "name": "Reference Inputs",
            "description": "Workflows requiring genome/transcriptome references and annotations.",
            "enabled": True,
            "keywords": ["reference", "gtf", "gff", "fasta", "genome", "annotation"],
            "plan_signals": [".gtf", ".gff", ".gff3", ".fa", ".fasta", ".fna", "mouse_gtf", "mouse_fasta"],
            "tool_hints": [],
        },
        {
            "id": "quantification",
            "name": "Expression Quantification",
            "description": "Read counting and quantification for expression analyses.",
            "enabled": True,
            "keywords": [
                "quantification",
                "counts",
                "featurecounts",
                "featurecounts_run",
                "salmon",
                "salmon_quant",
                "kallisto",
                "kallisto_quant",
                "stringtie",
                "stringtie_quant",
                "htseq",
            ],
            "plan_signals": [
                "featurecounts",
                "featurecounts_run",
                "salmon",
                "salmon_quant",
                "kallisto",
                "kallisto_quant",
                "stringtie",
                "stringtie_quant",
                "htseq",
                "counts",
            ],
            "tool_hints": [
                "featurecounts",
                "featurecounts_run",
                "salmon",
                "salmon_quant",
                "kallisto",
                "kallisto_quant",
                "stringtie",
                "stringtie_quant",
                "htseq-count",
            ],
        },
        {
            "id": "differential_analysis",
            "name": "Differential Analysis",
            "description": "Differential expression/splicing/statistical comparison workflows.",
            "enabled": True,
            "keywords": [
                "differential expression",
                "differentially expressed",
                "differentially express",
                "differential gene expression",
                "differential",
                "deseq2",
                "deseq2_run",
                "edger",
                "edger_run",
                "limma",
                "limma_voom_run",
                "scanpy_workflow",
                "sc_count_and_cluster",
                "rmats",
                "majiq",
                "dexseq",
            ],
            "plan_signals": [
                "deseq2",
                "deseq2_run",
                "edger",
                "edger_run",
                "limma",
                "limma_voom_run",
                "scanpy_workflow",
                "sc_count_and_cluster",
                "diffexp",
                "differential",
                "rmats",
                "majiq",
                "dexseq",
                "whippet",
                "run_rmats_if_needed.sh",
            ],
            "tool_hints": [
                "deseq2",
                "deseq2_run",
                "edger",
                "edger_run",
                "limma",
                "limma_voom_run",
                "scanpy_workflow",
                "sc_count_and_cluster",
                "rmats",
                "majiq",
                "dexseq",
            ],
        },
        {
            "id": "pathway_enrichment",
            "name": "Pathway Enrichment",
            "description": "Gene-set, pathway, or enrichment analyses such as KEGG, GO, or GSEA.",
            "enabled": True,
            "keywords": [
                "pathway",
                "kegg",
                "gsea",
                "gene set enrichment",
                "go enrichment",
                "enrichment analysis",
                "shared pathway",
                "pathway comparison",
            ],
            "plan_signals": [
                "pathway",
                "kegg",
                "gsea",
                "go enrichment",
                "enrichr",
                "gseapy",
                "fisher_exact",
                "pathway_comparison",
            ],
            "tool_hints": ["gseapy", "enrichr"],
        },
        {
            "id": "group_comparison",
            "name": "Group Comparison",
            "description": "Two or more biological/experimental groups are compared.",
            "enabled": True,
            "keywords": ["control", "treatment", " case ", "condition", "versus", " vs "],
            "plan_signals": ["control", "treatment", " case ", "condition", "versus", " vs "],
            "tool_hints": [],
            "group_signal_mode": "auto",
        },
        {
            "id": "splicing_analysis",
            "name": "Alternative Splicing",
            "description": "Differential and event-level splicing analyses.",
            "enabled": True,
            "keywords": ["splicing", "rmats", "majiq", "dexseq", "spladder", "whippet"],
            "plan_signals": ["rmats", "majiq", "dexseq", "spladder", "whippet", "splicing", "run_rmats_if_needed.sh"],
            "tool_hints": ["rmats", "majiq", "dexseq", "spladder", "whippet"],
        },
        {
            "id": "structural_variant_calling",
            "name": "Structural Variant Calling",
            "description": "Long-read structural-variant calling workflows.",
            "enabled": True,
            "keywords": [
                "structural variant",
                "structural variants",
                "structural variation",
                "long-read sv",
                "long read structural variant",
                "sniffles",
                "sniffles_sv_call",
            ],
            "plan_signals": [
                "structural variant",
                "structural variants",
                "structural variation",
                "sniffles",
                "sniffles_sv_call",
            ],
            "tool_hints": ["sniffles", "sniffles_sv_call"],
        },
        {
            "id": "variant_calling",
            "name": "Variant Calling",
            "description": "SNP/indel/somatic variant calling and filtering workflows.",
            "enabled": True,
            "keywords": [
                "variant",
                "vcf",
                "snv",
                "indel",
                "gatk",
                "gatk_haplotypecaller",
                "freebayes",
                "freebayes_call",
                "bcftools",
                "bcftools_call",
                "varscan",
                "varscan_call",
                "mutect",
            ],
            "plan_signals": [
                "vcf",
                "gatk",
                "gatk_haplotypecaller",
                "freebayes",
                "freebayes_call",
                "bcftools",
                "bcftools_call",
                "varscan",
                "varscan_call",
                "mutect",
                "variant",
            ],
            "tool_hints": [
                "gatk",
                "gatk_haplotypecaller",
                "freebayes",
                "freebayes_call",
                "bcftools",
                "bcftools_call",
                "varscan",
                "varscan_call",
                "mutect2",
            ],
        },
        {
            "id": "genome_assembly",
            "name": "Assembly",
            "description": "Genome/transcriptome assembly workflows.",
            "enabled": True,
            "keywords": [
                "assembly",
                "assemble",
                "spades",
                "spades_assemble",
                "flye",
                "flye_assemble",
                "canu",
                "trinity",
                "trinity_assemble",
            ],
            "plan_signals": [
                "assembly",
                "spades",
                "spades_assemble",
                "flye",
                "flye_assemble",
                "canu",
                "trinity",
                "trinity_assemble",
            ],
            "tool_hints": [
                "spades",
                "spades_assemble",
                "flye",
                "flye_assemble",
                "canu",
                "trinity",
                "trinity_assemble",
            ],
        },
        {
            "id": "annotation",
            "name": "Annotation",
            "description": "Functional/structural annotation workflows.",
            "enabled": True,
            "keywords": [
                "annotation",
                "annotate",
                "prokka",
                "prokka_annotate",
                "snpeff",
                "snpeff_annotate",
                "vep",
                "vep_annotate",
            ],
            "plan_signals": [
                "annotation",
                "prokka",
                "prokka_annotate",
                "snpeff",
                "snpeff_annotate",
                "vep",
                "vep_annotate",
                "blastp",
                "blastp_search",
                "blastn",
                "blastn_search",
                "blastx",
                "blastx_search",
                "tblastn",
                "tblastn_search",
                "tblastx",
                "tblastx_search",
                "psiblast",
                "psiblast_search",
                "deltablast",
                "deltablast_search",
                "rpsblast",
                "rpsblast_search",
                "rpstblastn",
                "rpstblastn_search",
                "blast_formatter",
                "blast_formatter_run",
                "blastdbcmd",
                "blastdbcmd_run",
                "blastdbcheck",
                "blastdbcheck_run",
                "blastdb_aliastool",
                "blastdb_aliastool_run",
                "makeprofiledb",
                "makeprofiledb_run",
                "hmmscan",
                "hmmscan_search",
                "protein_annotation",
                "annotate_proteins",
            ],
            "tool_hints": [
                "prokka",
                "prokka_annotate",
                "snpeff",
                "snpeff_annotate",
                "vep",
                "vep_annotate",
                "blastp",
                "blastp_search",
                "blastn",
                "blastn_search",
                "blastx",
                "blastx_search",
                "tblastn",
                "tblastn_search",
                "tblastx",
                "tblastx_search",
                "psiblast",
                "psiblast_search",
                "deltablast",
                "deltablast_search",
                "rpsblast",
                "rpsblast_search",
                "rpstblastn",
                "rpstblastn_search",
                "blast_formatter",
                "blast_formatter_run",
                "blastdbcmd",
                "blastdbcmd_run",
                "blastdbcheck",
                "blastdbcheck_run",
                "blastdb_aliastool",
                "blastdb_aliastool_run",
                "makeprofiledb",
                "makeprofiledb_run",
                "hmmscan",
                "hmmscan_search",
            ],
        },
        {
            "id": "metabolomics",
            "name": "Metabolomics",
            "description": "Table-first metabolite feature quantification and differential-abundance analysis.",
            "enabled": True,
            "keywords": [
                "metabolomics",
                "metabolite",
                "metabolites",
                "feature table",
                "peak table",
                "metabolic profiling",
                "lc-ms",
                "mass spec",
            ],
            "plan_signals": [
                "metabolomics",
                "metabolite abundance",
                "metabolomics_diff_abundance",
                "feature_table",
                "peak_table",
            ],
        },
        {
            "id": "protein_analysis",
            "name": "Protein Analysis",
            "description": "Protein homology/domain annotation and downstream protein-level analyses.",
            "enabled": True,
            "keywords": [
                "protein analysis",
                "protein function",
                "protein annotation",
                "protein homolog",
                "protein domain",
                "proteomics",
                "blastp",
                "blastx",
                "tblastn",
                "tblastx",
                "psiblast",
                "deltablast",
                "rpsblast",
                "rpstblastn",
                "blast_formatter",
                "blastdbcmd",
                "blastdbcheck",
                "blastdb_aliastool",
                "makeprofiledb",
                "hmmscan",
                "pfam",
                "domain annotation",
                "homology search",
                "prokka",
            ],
            "plan_signals": [
                "protein_analysis",
                "protein_function",
                "blastp",
                "blastp_search",
                "blastx",
                "blastx_search",
                "tblastn",
                "tblastn_search",
                "tblastx",
                "tblastx_search",
                "psiblast",
                "psiblast_search",
                "deltablast",
                "deltablast_search",
                "rpsblast",
                "rpsblast_search",
                "rpstblastn",
                "rpstblastn_search",
                "blast_formatter",
                "blast_formatter_run",
                "blastdbcmd",
                "blastdbcmd_run",
                "blastdbcheck",
                "blastdbcheck_run",
                "blastdb_aliastool",
                "blastdb_aliastool_run",
                "makeprofiledb",
                "makeprofiledb_run",
                "hmmscan",
                "hmmscan_search",
                "pfam",
                "prokka",
                "prokka_annotate",
            ],
            "tool_hints": [
                "blastp",
                "blastp_search",
                "blastx",
                "blastx_search",
                "tblastn",
                "tblastn_search",
                "tblastx",
                "tblastx_search",
                "psiblast",
                "psiblast_search",
                "deltablast",
                "deltablast_search",
                "rpsblast",
                "rpsblast_search",
                "rpstblastn",
                "rpstblastn_search",
                "blast_formatter",
                "blast_formatter_run",
                "blastdbcmd",
                "blastdbcmd_run",
                "blastdbcheck",
                "blastdbcheck_run",
                "blastdb_aliastool",
                "blastdb_aliastool_run",
                "makeprofiledb",
                "makeprofiledb_run",
                "hmmscan",
                "hmmscan_search",
                "prokka",
                "prokka_annotate",
            ],
        },
        {
            "id": "single_cell_analysis",
            "name": "Single-Cell Analysis",
            "description": "Single-cell RNA/ATAC and related analyses.",
            "enabled": True,
            "keywords": [
                "single-cell",
                "single cell",
                "scrna",
                "scanpy",
                "scanpy_workflow",
                "seurat",
                "seurat_rscript_workflow",
                "cellranger",
                "cellranger_count",
                "star_solo_count",
                "sc_count_and_cluster",
            ],
            "plan_signals": [
                "scanpy",
                "scanpy_workflow",
                "seurat",
                "seurat_rscript_workflow",
                "cellranger",
                "cellranger_count",
                "star_solo_count",
                "sc_count_and_cluster",
                "single-cell",
                "single cell",
            ],
            "tool_hints": [
                "scanpy",
                "scanpy_workflow",
                "seurat",
                "seurat_rscript_workflow",
                "cellranger",
                "cellranger_count",
                "star_solo_count",
                "sc_count_and_cluster",
            ],
        },
        {
            "id": "methylation_analysis",
            "name": "Methylation Analysis",
            "description": "Methylation and bisulfite sequencing workflows.",
            "enabled": True,
            "keywords": ["methylation", "bisulfite", "bismark", "methylkit", "methylation_bismark_style"],
            "plan_signals": ["methylation", "bisulfite", "bismark", "methylkit", "methylation_bismark_style"],
            "tool_hints": ["bismark", "methylkit", "methylation_bismark_style"],
        },
        {
            "id": "metagenomics_profiling",
            "name": "Metagenomics Profiling",
            "description": "Taxonomic abundance profiling for metagenomics reads.",
            "enabled": True,
            "keywords": [
                "metagenomics",
                "taxonomic profiling",
                "kraken2",
                "bracken",
                "metagenomics_kraken2_bracken_style",
            ],
            "plan_signals": [
                "metagenomics",
                "kraken2",
                "bracken",
                "metagenomics_kraken2_bracken_style",
                "classify_metagenomics_kmer.py",
                "classify_viral_reads_kmer.py",
            ],
            "tool_hints": ["kraken2", "bracken", "metagenomics_kraken2_bracken_style"],
        },
        {
            "id": "fusion_detection",
            "name": "Fusion Detection",
            "description": "Gene-fusion discovery from RNA-seq reads.",
            "enabled": True,
            "keywords": [
                "fusion",
                "star-fusion",
                "star fusion",
                "fusion_detection",
                "fusion_star_fusion_style",
            ],
            "plan_signals": [
                "fusion",
                "star-fusion",
                "STAR-Fusion",
                "fusion_star_fusion_style",
            ],
            "tool_hints": ["STAR-Fusion", "fusion_star_fusion_style"],
        },
        {
            "id": "cnv_analysis",
            "name": "Copy Number Analysis",
            "description": "Copy-number variation analysis from aligned sequencing data.",
            "enabled": True,
            "keywords": ["cnv", "copy number", "cnvkit", "cnv_cnvkit_style"],
            "plan_signals": ["cnv", "copy number", "cnvkit", "cnv_cnvkit_style"],
            "tool_hints": ["cnvkit.py", "cnv_cnvkit_style"],
        },
        {
            "id": "immune_repertoire_profiling",
            "name": "Immune Repertoire Profiling",
            "description": "Adaptive immune repertoire profiling workflows.",
            "enabled": True,
            "keywords": [
                "immune repertoire",
                "tcr",
                "bcr",
                "mixcr",
                "immune_repertoire_mixcr_style",
            ],
            "plan_signals": [
                "immune repertoire",
                "mixcr",
                "tcr",
                "bcr",
                "immune_repertoire_mixcr_style",
            ],
            "tool_hints": ["mixcr", "immune_repertoire_mixcr_style"],
        },
        {
            "id": "phylogenetics",
            "name": "Phylogenetics",
            "description": "Phylogenetic tree inference and model selection workflows.",
            "enabled": True,
            "keywords": ["phylogeny", "phylogenetics", "iqtree", "iq-tree", "phylogenetics_iqtree_style"],
            "plan_signals": [
                "phylogeny",
                "phylogenetics",
                "iqtree",
                "iqtree2",
                "phylogenetics_iqtree_style",
                "infer_phylogeny_biopython.py",
            ],
            "tool_hints": ["iqtree2", "iqtree", "phylogenetics_iqtree_style"],
        },
        {
            "id": "chipseq_analysis",
            "name": "ChIP-seq Analysis",
            "description": "Peak calling and downstream ChIP-seq analyses.",
            "enabled": True,
            "keywords": ["chip-seq", "chipseq", "peak calling", "macs2", "macs2_chipseq_callpeak"],
            "plan_signals": ["chip-seq", "chipseq", "macs2", "macs2_chipseq_callpeak", "peak"],
            "tool_hints": ["macs2", "macs2_chipseq_callpeak"],
        },
        {
            "id": "atacseq_analysis",
            "name": "ATAC-seq Analysis",
            "description": "ATAC-seq processing and accessibility analyses.",
            "enabled": True,
            "keywords": ["atac-seq", "atacseq", "chromatin accessibility", "peak calling", "macs2_atacseq_callpeak"],
            "plan_signals": ["atac-seq", "atacseq", "accessibility", "macs2", "macs2_atacseq_callpeak", "peak"],
            "tool_hints": ["macs2", "macs2_atacseq_callpeak"],
        },
        {
            "id": "interval_operations",
            "name": "Interval Operations",
            "description": "Genomic interval intersection and overlap summarization workflows.",
            "enabled": True,
            "keywords": [
                "interval operations",
                "genomic interval",
                "interval overlap",
                "bedtools intersect",
                "bedtools coverage",
                "bedtools genomecov",
            ],
            "plan_signals": [
                "bedtools intersect",
                "bedtools coverage",
                "bedtools genomecov",
                "bedtools_intersect",
                "bedtools_coverage",
                "bedtools_genomecov",
            ],
            "tool_hints": [
                "bedtools intersect",
                "bedtools coverage",
                "bedtools genomecov",
                "bedtools_intersect",
                "bedtools_coverage",
                "bedtools_genomecov",
                "bedtools",
            ],
        },
        {
            "id": "coverage_profiling",
            "name": "Coverage Profiling",
            "description": "Genome-wide depth or coverage-track generation from aligned or interval inputs.",
            "enabled": True,
            "keywords": [
                "genome coverage",
                "coverage profile",
                "coverage track",
                "bedgraph coverage",
                "bedtools genomecov",
                "genomecov",
            ],
            "plan_signals": [
                "bedtools genomecov",
                "bedtools_genomecov",
                "genomecov",
                "bedgraph",
            ],
            "tool_hints": [
                "bedtools genomecov",
                "bedtools_genomecov",
                "bedtools",
            ],
        },
    ],
    "custom_tools": [],
}

_CAPABILITY_HINT_SKIP_IDS = {"group_comparison", "reference_inputs"}
_MUSCLE_ALIGNMENT_CONTEXT = (
    "phylog",
    "newick",
    "bootstrap",
    "tree",
    "multiple sequence alignment",
    "sequence alignment",
    "msa",
    "align sequences",
    "align the sequences",
    "protein alignment",
    "mafft",
    "raxml",
    "iqtree",
)
_ANATOMICAL_MUSCLE_CONTEXT = (
    "skeletal muscle",
    "cardiac muscle",
    "smooth muscle",
    "muscle samples",
    "muscle tissue",
    "muscle cells",
    "muscle fibers",
)


def normalize_capability_id(raw: str) -> str:
    text = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    return text or "custom_capability"


def default_capability_catalog() -> Dict[str, Any]:
    return copy.deepcopy(DEFAULT_CAPABILITY_CATALOG)


def _normalize_catalog(catalog: Dict[str, Any]) -> Dict[str, Any]:
    out = default_capability_catalog()
    if not isinstance(catalog, dict):
        return out

    out["version"] = int(catalog.get("version", out["version"]))

    merged: Dict[str, Dict[str, Any]] = {}
    for entry in out.get("capabilities", []):
        cid = normalize_capability_id(entry.get("id", ""))
        if cid:
            merged[cid] = dict(entry)

    for entry in catalog.get("capabilities", []) if isinstance(catalog.get("capabilities"), list) else []:
        if not isinstance(entry, dict):
            continue
        cid = normalize_capability_id(entry.get("id", ""))
        if not cid:
            continue
        base = merged.get(cid, {"id": cid, "name": cid.replace("_", " ").title(), "description": "", "enabled": True})
        merged[cid] = {
            **base,
            **entry,
            "id": cid,
            "enabled": bool(entry.get("enabled", base.get("enabled", True))),
            "keywords": [str(x).strip().lower() for x in entry.get("keywords", base.get("keywords", [])) if str(x).strip()],
            "plan_signals": [str(x).strip().lower() for x in entry.get("plan_signals", base.get("plan_signals", [])) if str(x).strip()],
            "tool_hints": [str(x).strip().lower() for x in entry.get("tool_hints", base.get("tool_hints", [])) if str(x).strip()],
        }

    # ── Catalog migrations ──────────────────────────────────────────────
    # Migration: Replace bare "protein" in protein_analysis keywords/plan_signals
    # with specific multi-word phrases so that phylogenetics prompts like
    # "protein sequences for phylogenetics" no longer trigger protein_analysis.
    pa = merged.get("protein_analysis")
    if pa and "protein" in pa.get("keywords", []):
        updated_kw = [k for k in pa["keywords"] if k != "protein"]
        for new_kw in ("protein analysis", "protein function", "protein annotation",
                       "protein homolog", "protein domain"):
            if new_kw not in updated_kw:
                updated_kw.append(new_kw)
        pa["keywords"] = updated_kw
    if pa and "protein" in pa.get("plan_signals", []):
        updated_ps = [s for s in pa["plan_signals"] if s != "protein"]
        for new_ps in ("protein_analysis", "protein_function"):
            if new_ps not in updated_ps:
                updated_ps.append(new_ps)
        pa["plan_signals"] = updated_ps

    # Migration: Replace bare "protein" in annotation plan_signals with
    # specific phrases (protein_annotation, annotate_proteins).
    ann = merged.get("annotation")
    if ann and "protein" in ann.get("plan_signals", []):
        updated_ps = [s for s in ann["plan_signals"] if s != "protein"]
        for new_ps in ("protein_annotation", "annotate_proteins"):
            if new_ps not in updated_ps:
                updated_ps.append(new_ps)
        ann["plan_signals"] = updated_ps

    # Also update domain/homology to more specific forms if still bare.
    if pa:
        kw = pa.get("keywords", [])
        if "domain" in kw and "domain annotation" not in kw:
            kw[kw.index("domain")] = "domain annotation"
            pa["keywords"] = kw
        if "homology" in kw and "homology search" not in kw:
            kw[kw.index("homology")] = "homology search"
            pa["keywords"] = kw

    # Migration: recognize end-to-end single-cell workflows as satisfying both
    # single_cell_analysis and alignment in persisted catalogs created before
    # sc_count_and_cluster/cellranger_count signals were added.
    alignment = merged.get("alignment")
    if alignment:
        for token in ("cellranger_count", "sc_count_and_cluster"):
            if token not in alignment.get("plan_signals", []):
                alignment.setdefault("plan_signals", []).append(token)
            if token not in alignment.get("tool_hints", []):
                alignment.setdefault("tool_hints", []).append(token)

    single_cell = merged.get("single_cell_analysis")
    if single_cell:
        for field in ("keywords", "plan_signals", "tool_hints"):
            values = single_cell.setdefault(field, [])
            if "sc_count_and_cluster" not in values:
                values.append("sc_count_and_cluster")

    _augment_capabilities_from_scientific_catalog(merged)

    out["capabilities"] = sorted(merged.values(), key=lambda x: x.get("id", ""))
    custom_tools = catalog.get("custom_tools", [])
    out["custom_tools"] = custom_tools if isinstance(custom_tools, list) else []
    return out


def _augment_capabilities_from_scientific_catalog(merged: Dict[str, Dict[str, Any]]) -> None:
    """Add bundled scientific tool names and aliases to relevant capabilities."""
    try:
        tool_catalog = load_scientific_tool_catalog()
    except Exception:
        return

    for entry in tool_catalog.get("tools", []) if isinstance(tool_catalog.get("tools"), list) else []:
        if not isinstance(entry, dict) or not bool(entry.get("augment_capability_catalog", False)):
            continue
        terms = [
            str(value).strip().lower()
            for value in [entry.get("name", "")] + list(entry.get("aliases", []) or [])
            if str(value).strip()
        ]
        if not terms:
            continue
        for raw_capability in entry.get("capabilities", []) if isinstance(entry.get("capabilities"), list) else []:
            capability_id = normalize_capability_id(raw_capability)
            if not capability_id or capability_id in _CAPABILITY_HINT_SKIP_IDS:
                continue
            capability = merged.get(capability_id)
            if not capability:
                continue
            for field in ("keywords", "plan_signals", "tool_hints"):
                current = [str(value).strip().lower() for value in capability.get(field, []) if str(value).strip()]
                capability[field] = list(dict.fromkeys(current + terms))


def load_capability_catalog(path: Path) -> Dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        catalog = _normalize_catalog(default_capability_catalog())
        path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        return catalog
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = default_capability_catalog()
    normalized = _normalize_catalog(raw)
    path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized


def save_capability_catalog(path: Path, catalog: Dict[str, Any]) -> None:
    normalized = _normalize_catalog(catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")


def capability_index(catalog: Dict[str, Any], *, enabled_only: bool = False) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for cap in catalog.get("capabilities", []) if isinstance(catalog.get("capabilities"), list) else []:
        if not isinstance(cap, dict):
            continue
        cid = normalize_capability_id(cap.get("id", ""))
        if not cid:
            continue
        if enabled_only and not bool(cap.get("enabled", True)):
            continue
        result[cid] = cap
    return result


def infer_capabilities_from_text(text: str, catalog: Dict[str, Any], *, enabled_only: bool = True) -> List[str]:
    import re as _re
    hay = f" {(text or '').lower()} "
    matches: List[str] = []
    for cid, cap in capability_index(catalog, enabled_only=enabled_only).items():
        for kw in cap.get("keywords", []):
            k = str(kw).strip().lower()
            if not k:
                continue
            # Use word-boundary matching to prevent partial matches
            # (e.g. "rmats" matching inside "formats").
            if _re.search(rf"\b{_re.escape(k)}\b", hay):
                if cid not in matches:
                    matches.append(cid)
                break
    if "phylogenetics" in matches and _suppress_ambiguous_muscle_hint(hay):
        matches.remove("phylogenetics")
    return matches


def infer_tool_hints_from_text(text: str, catalog: Dict[str, Any], *, enabled_only: bool = True) -> List[str]:
    import re as _re
    hay = f" {(text or '').lower()} "
    hints: List[str] = []
    for _, cap in capability_index(catalog, enabled_only=enabled_only).items():
        for hint in cap.get("tool_hints", []):
            h = str(hint).strip().lower()
            if not h:
                continue
            if h == "muscle" and _suppress_ambiguous_muscle_hint(hay):
                continue
            # Use word-boundary matching to prevent partial matches.
            if _re.search(rf"\b{_re.escape(h)}\b", hay) and h not in hints:
                hints.append(h)
    return hints


def _suppress_ambiguous_muscle_hint(hay: str) -> bool:
    """Return True when bare ``muscle`` likely refers to anatomy, not MSA.

    MUSCLE is a valid multiple-sequence aligner, but prompts like
    ``skeletal muscle samples`` should not infer the phylogenetics capability
    or a required ``muscle`` tool hint. Keep the tool in the catalog while
    suppressing the ambiguous bare token outside sequence-alignment context.
    """
    text = str(hay or "").lower()
    if "muscle" not in text:
        return False
    if any(token in text for token in _MUSCLE_ALIGNMENT_CONTEXT):
        return False
    if any(token in text for token in _ANATOMICAL_MUSCLE_CONTEXT):
        return True
    return True


def update_capability_tool_hints(
    catalog: Dict[str, Any],
    *,
    capability_ids: List[str],
    tool_hints: List[str],
    plan_signals: List[str] | None = None,
) -> Dict[str, Any]:
    next_catalog = _normalize_catalog(catalog)
    target_ids = [normalize_capability_id(x) for x in capability_ids if normalize_capability_id(x)]
    hint_tokens = [str(x).strip().lower() for x in tool_hints if str(x).strip()]
    signal_tokens = [str(x).strip().lower() for x in (plan_signals or []) if str(x).strip()]

    for cap in next_catalog.get("capabilities", []):
        cid = normalize_capability_id(cap.get("id", ""))
        if cid not in target_ids:
            continue
        merged_hints = list(dict.fromkeys(list(cap.get("tool_hints", [])) + hint_tokens))
        merged_signals = list(dict.fromkeys(list(cap.get("plan_signals", [])) + hint_tokens + signal_tokens))
        cap["tool_hints"] = merged_hints
        cap["plan_signals"] = merged_signals
    return next_catalog
