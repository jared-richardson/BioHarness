from __future__ import annotations

# ruff: noqa: F403,F405
from typing import Any, Callable

from bio_harness.core.request_scope import (
    semantically_requests_long_read_rna_stringtie_pipeline,
    semantically_requests_stringtie_quant,
)
from bio_harness.core.analysis_spec_seed_helpers import (
    explicit_requested_skill_seed as _explicit_requested_skill_seed,
    has_query_cue as _has_query_cue,
    is_count_matrix_de_request as _is_count_matrix_de_request,
    is_local_variant_annotation_task as _is_local_variant_annotation_task,
    smoke_requested_skills as _smoke_requested_skills,
)
from bio_harness.core.analysis_spec_support import *


# ---------------------------------------------------------------------------
# Per-analysis-type profile seed builders
# ---------------------------------------------------------------------------
# Each builder takes (query_l, skills, available_skill_names) and returns
# a ProfileSeed dict.  Keeping each type in its own function makes the
# dispatch table discoverable and extensible.
# ---------------------------------------------------------------------------

_BuilderFn = Callable[[str, set[str], list[str]], dict[str, Any]]


def _seed_direct_skill_smoke(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    requested_skills = _smoke_requested_skills(query_l, available_skill_names)
    chosen = requested_skills[0] if requested_skills else ""
    preferred = [chosen] if chosen else []
    return {
        "biological_objective": "Run the explicitly requested Bio-Harness skill as a single-step smoke test on the provided inputs and verify its declared outputs.",
        "context_facts": _dedupe([
            "single-step direct skill validation task",
            "the explicitly requested skill wrapper is the source of truth",
            "do not infer a larger assay workflow from the input filenames or benchmark history",
            "do not replace the requested skill with bash_run or a multi-step fallback workflow",
        ]),
        "candidate_methods": requested_skills,
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["bash_run"] if tool in skills],
        "parameter_profile": [],
        "acceptance_checks": [
            "the plan uses only the explicitly requested skill wrapper",
            "the requested smoke-test outputs are written under the selected_dir path",
            "the harness does not add alignment, assembly, or other unrelated workflow stages",
        ],
        "rerun_triggers": [
            "the plan introduces extra scientific workflow steps instead of the requested single skill",
            "the plan swaps to a different tool than the explicitly requested skill",
            "the run only succeeds after generic template fallback or runtime repair",
        ],
        "source_provenance": ["direct_skill_smoke_prompt"],
        "open_risks": [],
        "plan_skeleton": [
            (chosen, "Run the explicitly requested skill directly against the provided inputs and write the requested outputs", {})
        ] if chosen else [],
    }


def _seed_artifact_schema_profiling(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["artifact_schema_profile"] if tool in skills]
    return {
        "biological_objective": "Profile the schema of an existing artifact and emit a compact machine-readable data dictionary.",
        "context_facts": _dedupe([
            "post-run artifact inspection task",
            "schema or data-dictionary output is requested",
            "input artifact already exists and should not be regenerated",
        ]),
        "candidate_methods": preferred,
        "chosen_method": "artifact_schema_profile" if "artifact_schema_profile" in skills else "",
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["bash_run"] if tool in skills],
        "parameter_profile": [
            {
                "tool_name": "artifact_schema_profile",
                "settings": {"sample_rows": 25},
                "rationale": "A small sample is enough for lightweight type inference while keeping the inspection fast.",
            },
        ],
        "acceptance_checks": [
            "schema JSON exists at the requested path",
            "the schema describes the existing artifact instead of regenerating it",
        ],
        "rerun_triggers": [
            "the plan rewrites the input artifact instead of profiling it",
            "the emitted schema JSON is missing column-level structure for a tabular artifact",
        ],
        "source_provenance": ["Bio-Harness artifact schema profiler"],
        "open_risks": [],
    }


def _seed_run_reporting(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["multiqc_report", "quarto_report"] if tool in skills]
    chosen = ""
    if "quarto_report" in skills and "quarto" in query_l:
        chosen = "quarto_report"
    elif "multiqc_report" in skills and "multiqc" in query_l:
        chosen = "multiqc_report"
    elif preferred:
        chosen = preferred[0]
    return {
        "biological_objective": "Build a shareable post-run reporting bundle from a completed Bio-Harness run without rerunning the analysis.",
        "context_facts": _dedupe([
            "post-run reporting task",
            "completed run directory is the primary input",
            "optional MultiQC and Quarto rendering should be used only when those binaries are installed",
        ]),
        "candidate_methods": preferred,
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["bash_run"] if tool in skills],
        "parameter_profile": [],
        "acceptance_checks": [
            "report bundle directory exists",
            "summary.json and summary.md are present in the report bundle",
            "the workflow does not rerun the underlying scientific analysis",
        ],
        "rerun_triggers": [
            "the plan calls multiqc or quarto directly instead of using the reporting skill wrapper",
            "the plan attempts benchmark validation instead of report generation",
        ],
        "source_provenance": ["Bio-Harness reporting bundle builder"],
        "open_risks": [
            "MultiQC and Quarto outputs are optional and may be skipped when those binaries are unavailable",
        ],
    }


def _seed_bacterial_evolution(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [
        tool
        for tool in [
            "spades_assemble",
            "bwa_mem_align",
            "freebayes_call",
            "snpeff_annotate",
            "bcftools_filter_run",
            "prodigal_annotate",
            "prokka_annotate",
            "bcftools_isec_run",
            "bcftools_norm_run",
            "shared_variants_export_run",
            "tabix_index_run",
        ]
        if tool in skills
    ]
    candidates = [tool for tool in ["freebayes_call", "bcftools_call"] if tool in skills]
    chosen = "freebayes_call" if "freebayes_call" in skills else ("bcftools_call" if "bcftools_call" in skills else "")
    discouraged = [tool for tool in ["gatk_haplotypecaller", "gatk_mutect2_call"] if tool in skills]
    filter_tool = "bcftools_filter_run" if "bcftools_filter_run" in skills else "bash_run"
    subtract_tool = "bcftools_isec_run" if "bcftools_isec_run" in skills else "bash_run"
    normalize_tool = "bcftools_norm_run" if "bcftools_norm_run" in skills else "bash_run"
    export_tool = "shared_variants_export_run" if "shared_variants_export_run" in skills else "bash_run"
    return {
        "biological_objective": "Identify variants shared by the evolved lines but absent from the ancestor-supported background callset, using the assembled ancestor scaffolds as the working coordinate system and normalized allele representations before final shared-hit export.",
        "context_facts": _dedupe(
            [
                "experimental evolution / ancestor-versus-evolved setup",
                "bacterial or haploid-like variant calling context" if _has_query_cue(query_l, "bacteria", "bacterial", "e. coli", "ecoli", "haploid", "isolate") else "",
                "short-read resequencing workflow" if _has_query_cue(query_l, "illumina", "paired-end", "short read", "fastq") else "",
                "ancestor subtraction is mandatory before declaring shared evolved variants",
                "assembled ancestor scaffolds are the preferred working reference when SPAdes produces both contigs and scaffolds",
            ]
        ),
        "candidate_methods": candidates,
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": discouraged,
        "parameter_profile": [
            {"tool_name": "spades_assemble", "settings": {"careful": True}, "rationale": "Use careful mode for small-variant-sensitive assembly-based reference construction."},
            {"tool_name": "freebayes_call", "settings": {"ploidy": 1}, "rationale": "Bacterial evolution workflows typically need haploid calling rather than diploid defaults."},
            {"tool_name": "snpeff_annotate", "settings": {}, "rationale": "Use documented ANN semantics for downstream effect export and filtering."},
            {
                "tool_name": filter_tool,
                "settings": {} if filter_tool != "bash_run" else {"tool": "bcftools"},
                "rationale": "Keep one filter operation per step so comparison-ready VCF staging stays atomic.",
            },
            {
                "tool_name": subtract_tool,
                "settings": {"mode": "complement"},
                "rationale": "Use one explicit subtraction operation per evolved branch instead of one compound shell block.",
            },
            {
                "tool_name": normalize_tool,
                "settings": {"multiallelic_mode": "-any"},
                "rationale": "Normalize one annotated branch-local VCF at a time before any shared export.",
            },
            {
                "tool_name": export_tool,
                "settings": {"min_impact": "MODERATE", "status": "shared", "header_case": "upper"},
                "rationale": "Materialize the final shared-variant CSV with one dedicated atomic export step.",
            },
        ],
        "acceptance_checks": [
            "assembled ancestor reference is annotated before SnpEff runs",
            "assembled ancestor scaffolds are used as the working reference when available",
            "ancestor-supported variants are removed before reporting shared evolved hits",
            "ancestor subtraction happens on each evolved line before the two evolved lines are intersected",
            "each evolved line is annotated separately before shared-variant intersection",
            "equivalent indels and multi-base alleles are normalized before the final shared-variant comparison",
            "annotation fields are parseable from standard ANN output",
            "final shared-variant CSV uses exact columns chrom,pos,ref,alt,gene,impact,effect,status with comma separators",
        ],
        "rerun_triggers": [
            "the plan calls snpeff_annotate without first producing an ancestor GFF annotation",
            "ancestor is aligned but not called as a background variant set",
            "shared variant overlap with truth is near zero or final CSV has thousands of rows",
            "the plan annotates only one evolved line or reuses one branch's BAM/VCF paths for a sibling branch",
            "the plan compares evolved calls without normalizing equivalent indel / MNP representations before the final shared export",
            "annotation export is using non-standard INFO keys, raw unfiltered VCFs, ignores the ancestor callset, or intersects evolved lines before ancestor subtraction",
        ],
        "source_provenance": [
            "BioAgentBench evolution task recipe",
            "FreeBayes documentation",
            "SPAdes manual",
            "SnpEff ANN documentation",
        ],
        "open_risks": [
            "calling evolved lines directly without subtracting ancestor-supported sites will inflate false positives",
            "reusing evol2 artifacts for evol1 (or vice versa) will produce missing-input failures or silently corrupt branch-specific results",
            "working on contig coordinates when scaffold coordinates are available can shift benchmark-facing variant identifiers even when the underlying mutations are correct",
            "intersecting raw VCFs instead of filtered annotated VCFs will degrade precision",
        ],
        "plan_skeleton": [
            ("spades_assemble", "Assemble the ancestor reads into the working bacterial reference, preferring the scaffolded assembly output when SPAdes produces it"),
            ("prodigal_annotate", "Annotate the assembled ancestor scaffold reference to produce a GFF for downstream variant effect annotation"),
            ("bwa_mem_align", "Align ancestor reads to the assembled scaffold reference"),
            ("freebayes_call", "Call haploid ancestor variants to define background / assembly-supporting sites"),
            ("bwa_mem_align", "Align each evolved line to the same assembled scaffold reference"),
            ("freebayes_call", "Call haploid evolved-line variants with quality-aware filtering"),
            (
                filter_tool,
                "Filter one ancestor or evolved callset at a time into a comparison-ready VCF using one filter command per step",
                {} if filter_tool != "bash_run" else {"tool": "bcftools", "meta_mode": True},
            ),
            (
                subtract_tool,
                "Subtract the ancestor-supported sites from each evolved callset separately before any evolved-evolved comparison",
                {
                    "parameter_hints": (
                        {"mode": "complement"}
                        if subtract_tool != "bash_run"
                        else {"action": "bcftools isec -C -w1"}
                    ),
                    "downstream_constraints": [
                        "Materialize concrete branch-local minus-ancestor VCFs such as evol1_subtracted_anc.vcf.gz and evol2_subtracted_anc.vcf.gz before annotation.",
                        "Do not substitute shared-with-ancestor intersections or raw-call SNP filtering for the minus-ancestor subtraction step.",
                    ],
                },
            ),
            ("snpeff_annotate", "Annotate the ancestor-subtracted evolved variants with ANN-compatible fields"),
            (
                normalize_tool,
                "Normalize each annotated evolved callset separately before shared export",
                {"multiallelic_mode": "-any"} if normalize_tool != "bash_run" else {"tool": "bcftools"},
            ),
            (
                export_tool,
                "Write the final shared-variant CSV from the normalized annotated evolved callsets with the exact required columns",
                {"tool": "python3"} if export_tool == "bash_run" else {"header_case": "upper", "min_impact": "MODERATE"},
            ),
        ],
    }


def _seed_structural_variant_calling(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["sniffles_sv_call", "minimap2_align"] if tool in skills]
    chosen = "sniffles_sv_call" if "sniffles_sv_call" in skills else ""
    discouraged = [
        tool
        for tool in [
            "bcftools_call",
            "freebayes_call",
            "gatk_haplotypecaller",
            "gatk_mutect2_call",
            "varscan_call",
        ]
        if tool in skills
    ]
    return {
        "biological_objective": "Call structural variants from a coordinate-sorted long-read alignment against the matching reference genome.",
        "context_facts": _dedupe(
            [
                "structural-variant calling workflow",
                "long-read alignments are the expected input for Sniffles",
                "the provided BAM or CRAM must already be coordinate-sorted against the matching reference FASTA",
                "do not replace structural-variant calling with SNP or small-indel callers",
            ]
        ),
        "candidate_methods": [tool for tool in ["sniffles_sv_call"] if tool in skills],
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": discouraged,
        "parameter_profile": [
            {
                "tool_name": "sniffles_sv_call",
                "settings": {"min_support": 3, "min_sv_length": 50, "threads": 4},
                "rationale": "Use conservative default support and minimum-size thresholds for practical long-read structural-variant calling.",
            },
        ],
        "acceptance_checks": [
            "structural-variant VCF exists at the requested output path",
            "the plan uses a structural-variant caller rather than a SNP or indel caller",
            "the selected BAM or CRAM input is aligned against the same reference FASTA supplied to Sniffles",
        ],
        "rerun_triggers": [
            "the plan swaps to a SNP or small-indel caller instead of Sniffles",
            "the aligned BAM or CRAM is missing, unindexed, or appears to target a different reference build",
            "the output VCF is empty despite a non-empty long-read alignment input",
        ],
        "source_provenance": ["Sniffles documentation"],
        "open_risks": [
            "calling directly from a mismatched or low-coverage long-read alignment can suppress true structural variants",
        ],
        "plan_skeleton": [
            (
                "sniffles_sv_call",
                "Call structural variants from the aligned long-read BAM or CRAM and write a VCF under the selected output path",
                {"min_support": 3, "min_sv_length": 50, "threads": 4},
            ),
        ],
    }


def _seed_long_read_assembly(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    is_metagenome = any(
        token in query_l
        for token in ("metagenome", "metagenomic", "multiple organisms", "mixed organisms", "community")
    )
    if "pacbio" in query_l and "hifi" in query_l:
        read_mode = "pacbio-hifi"
    elif "pacbio" in query_l:
        read_mode = "pacbio-raw"
    else:
        read_mode = "nano-raw"
    flye_settings: dict[str, Any] = {
        "threads": 2 if is_metagenome else 4,
        "read_mode": read_mode,
        "genome_size": "100k" if is_metagenome else "5m",
    }
    if is_metagenome:
        flye_settings["meta_mode"] = True
    preferred = [tool for tool in ["flye_assemble"] if tool in skills]
    chosen = "flye_assemble" if "flye_assemble" in skills else ""
    context_facts = [
        "long-read genome assembly workflow",
        "Oxford Nanopore or PacBio reads are appropriate inputs for Flye",
        "do not replace long-read assembly with comparative-genomics bash wrappers",
    ]
    open_risks = [
        "genome size is only approximate and may need adjustment for unusual organisms or metagenomes",
    ]
    if is_metagenome:
        context_facts.extend(
            [
                "metagenome-style long-read assembly benefits from Flye meta mode",
                "when organism composition is unknown, prefer low-memory conservative defaults over optimistic genome-size guesses",
            ]
        )
        open_risks.append(
            "metagenome assemblies can still require additional memory tuning or dataset subsetting when coverage is highly uneven"
        )
    return {
        "biological_objective": "Assemble long-read sequencing data into a de novo contig FASTA.",
        "context_facts": _dedupe(context_facts),
        "candidate_methods": [tool for tool in ["flye_assemble"] if tool in skills],
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["bash_run", "spades_assemble"] if tool in skills],
        "parameter_profile": [
            {
                "tool_name": "flye_assemble",
                "settings": dict(flye_settings),
                "rationale": (
                    "Use Flye directly for long-read assembly with assay-aware defaults."
                    if not is_metagenome
                    else "Use Flye meta mode with conservative resource settings when the prompt indicates a metagenome."
                ),
            },
        ]
        if chosen
        else [],
        "acceptance_checks": [
            "assembly FASTA exists at the requested output path",
            "the workflow uses a long-read assembly tool rather than a generic shell script",
        ],
        "rerun_triggers": [
            "the plan drops Flye and falls back to bash_run for assembly",
            "assembly FASTA is missing or empty",
        ],
        "source_provenance": ["Flye documentation"],
        "open_risks": open_risks,
        "plan_skeleton": [
            (
                "flye_assemble",
                "Assemble the long reads with Flye and write the contig FASTA under the requested output directory",
                dict(flye_settings),
            ),
        ],
    }


def _seed_long_read_rna(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    no_annotation_provided = any(
        token in query_l
        for token in (
            "no annotation file is provided",
            "no annotation is provided",
            "without annotation",
            "without a gtf",
            "without gtf",
            "no gtf",
            "no gff",
        )
    )
    annotation_backed_quant = (
        not no_annotation_provided
        and semantically_requests_long_read_rna_stringtie_pipeline(query_l)
        and "minimap2_align" in skills
        and "stringtie_quant" in skills
    )
    preferred = (
        [tool for tool in ["minimap2_align", "stringtie_quant"] if tool in skills]
        if annotation_backed_quant
        else [tool for tool in ["minimap2_align"] if tool in skills]
    )
    chosen = "minimap2_align + stringtie_quant" if annotation_backed_quant else ("minimap2_align" if "minimap2_align" in skills else "")
    context_facts = [
        "long-read RNA / isoform workflow",
        "Oxford Nanopore direct-RNA data should use splice-aware long-read alignment",
        "do not replace long-read RNA alignment with short-read transcript quantification wrappers",
    ]
    rerun_triggers = [
        "the plan substitutes Salmon or kallisto for long-read RNA alignment",
        "no splice-aware long-read alignment output is produced",
    ]
    open_risks = []
    if annotation_backed_quant:
        context_facts.extend(
            [
                "the request includes annotation-backed isoform abundance estimation",
                "align the long reads first, then quantify transcripts with StringTie from the aligned BAM plus annotation GTF",
            ]
        )
        rerun_triggers.append("the plan drops StringTie despite annotation-backed isoform quantification being requested")
    else:
        context_facts.append("current first-line wrapper support is alignment-first rather than full isoform quantification")
        open_risks.append(
            "full isoform quantification support remains partial and may require later family completion"
        )
    if no_annotation_provided:
        context_facts.extend(
            [
                "the request explicitly lacks a transcript annotation file",
                "without transcript models, preserve an alignment-ready BAM and do not invent annotation-backed isoform quantification",
            ]
        )
        rerun_triggers.append("the plan fabricates an annotation file or claims isoform quantification completed without one")
        open_risks.append(
            "annotation-free long-read RNA requests should degrade to alignment-first handoff rather than fabricated quantification"
        )
    return {
        "biological_objective": "Align long-read RNA reads with a splice-aware long-read aligner and preserve isoform-analysis context.",
        "context_facts": _dedupe(context_facts),
        "candidate_methods": (
            ["minimap2_align + stringtie_quant", "minimap2_align"]
            if annotation_backed_quant
            else [tool for tool in ["minimap2_align"] if tool in skills]
        ),
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["salmon_quant", "kallisto_quant", "bash_run"] if tool in skills],
        "parameter_profile": (
            [
                {
                    "tool_name": "minimap2_align",
                    "settings": {"preset": "splice", "threads": 4},
                    "rationale": "Prefer the splice-aware minimap2 preset for long-read RNA alignment.",
                },
                {
                    "tool_name": "stringtie_quant",
                    "settings": {"threads": 4},
                    "rationale": "Use StringTie after long-read alignment when annotation-backed isoform abundance is requested.",
                },
            ]
            if annotation_backed_quant and chosen
            else [
                {
                    "tool_name": "minimap2_align",
                    "settings": {"preset": "splice", "threads": 4},
                    "rationale": "Prefer the splice-aware minimap2 preset for long-read RNA alignment.",
                },
            ]
            if chosen
            else []
        ),
        "acceptance_checks": [
            "splice-aware long-read alignment output exists",
            (
                "annotation-backed isoform abundance output exists"
                if annotation_backed_quant
                else "the workflow preserves long-read RNA / isoform intent instead of switching to short-read transcript quantification"
            ),
        ],
        "rerun_triggers": rerun_triggers,
        "source_provenance": (
            ["minimap2 long-read RNA documentation", "StringTie documentation"]
            if annotation_backed_quant
            else ["minimap2 long-read RNA documentation"]
        ),
        "open_risks": open_risks,
        "plan_skeleton": (
            [
                (
                    "minimap2_align",
                    "Align the long-read RNA reads with a splice-aware minimap2 preset and write a coordinate-sorted BAM",
                    {"preset": "splice", "threads": 4},
                ),
                (
                    "stringtie_quant",
                    "Quantify isoform abundance from the aligned BAM using the provided annotation GTF and write transcript outputs",
                    {"threads": 4},
                ),
            ]
            if annotation_backed_quant and chosen
            else [
                (
                    "minimap2_align",
                    "Align the long-read RNA reads with a splice-aware minimap2 preset and write a coordinate-sorted BAM",
                    {"preset": "splice", "threads": 4},
                ),
            ]
        ),
    }


def _seed_proteomics(
    query_l: str,
    skills: set[str],
    available_skill_names: list[str],
) -> dict[str, Any]:
    """Return the deterministic seed for table-first proteomics analysis."""

    preferred = [tool for tool in ["proteomics_diff_abundance"] if tool in skills]
    chosen_method = "proteomics_diff_abundance" if "proteomics_diff_abundance" in skills else ""
    return {
        "biological_objective": "Identify differentially abundant proteins between two conditions from a processed abundance matrix and sample metadata table.",
        "context_facts": _dedupe(
            [
                "table-first proteomics workflow",
                "processed abundance matrix and metadata table are the expected inputs",
                "this workflow is differential protein abundance, not protein-sequence annotation or homology search",
                "missing values should be handled deterministically rather than ignored or silently dropped",
                "do not replace proteomics differential abundance with RNA-seq differential-expression wrappers",
            ]
        ),
        "candidate_methods": ["proteomics_diff_abundance"],
        "chosen_method": chosen_method,
        "preferred_tools": preferred,
        "discouraged_tools": [
            tool
            for tool in [
                "deseq2_run",
                "edger_run",
                "limma_voom_run",
                "blastp_search",
                "hmmscan_search",
                "bash_run",
            ]
            if tool in skills
        ],
        "parameter_profile": [
            {
                "tool_name": "proteomics_diff_abundance",
                "settings": {
                    "normalization_method": "median_center",
                    "min_present_fraction": 0.5,
                    "impute_method": "protein_median",
                },
                "rationale": "Use deterministic median-centering, bounded missingness filtering, and protein-median imputation for processed benchmark-style proteomics tables.",
            },
        ]
        if chosen_method
        else [],
        "acceptance_checks": [
            "proteomics differential-abundance CSV exists",
            "proteomics QC summary JSON exists",
            "the workflow uses the proteomics wrapper rather than RNA-seq differential-expression wrappers",
        ],
        "rerun_triggers": [
            "the plan switches to deseq2_run, edger_run, or limma_voom_run for protein abundance tables",
            "the abundance matrix contains malformed non-numeric values",
            "the metadata table does not preserve the intended comparison groups",
        ],
        "source_provenance": [
            "processed proteomics benchmark corpus",
            "repo-local deterministic proteomics workflow",
        ],
        "open_risks": [
            "raw mzML processing remains intentionally out of scope for v1",
            "very high missingness can reduce differential-protein recovery",
        ],
        "plan_skeleton": [
            (
                "proteomics_diff_abundance",
                "Run deterministic differential protein abundance analysis on the abundance matrix and metadata table",
                {
                    "normalization_method": "median_center",
                    "min_present_fraction": 0.5,
                    "impute_method": "protein_median",
                },
            ),
        ],
    }


def _seed_metabolomics(
    query_l: str,
    skills: set[str],
    available_skill_names: list[str],
) -> dict[str, Any]:
    """Return the deterministic seed for table-first metabolomics analysis."""

    del query_l, available_skill_names
    preferred = [tool for tool in ["metabolomics_diff_abundance"] if tool in skills]
    chosen_method = "metabolomics_diff_abundance" if "metabolomics_diff_abundance" in skills else ""
    return {
        "biological_objective": "Identify differentially abundant metabolite features between two conditions from a processed feature-intensity matrix and sample metadata table.",
        "context_facts": _dedupe(
            [
                "table-first metabolomics workflow",
                "processed feature-intensity matrix and metadata table are the expected inputs",
                "this workflow is differential metabolite abundance, not RNA-seq differential expression or proteomics differential protein abundance",
                "missing values should be handled deterministically rather than ignored or silently dropped",
                "do not replace metabolomics differential abundance with RNA-seq or proteomics wrappers",
            ]
        ),
        "candidate_methods": ["metabolomics_diff_abundance"],
        "chosen_method": chosen_method,
        "preferred_tools": preferred,
        "discouraged_tools": [
            tool
            for tool in [
                "proteomics_diff_abundance",
                "deseq2_run",
                "edger_run",
                "limma_voom_run",
                "bash_run",
            ]
            if tool in skills
        ],
        "parameter_profile": [
            {
                "tool_name": "metabolomics_diff_abundance",
                "settings": {
                    "normalization_method": "median_center",
                    "min_present_fraction": 0.5,
                    "impute_method": "feature_median",
                },
                "rationale": "Use deterministic median-centering, bounded missingness filtering, and feature-median imputation for processed benchmark-style metabolomics tables.",
            },
        ]
        if chosen_method
        else [],
        "acceptance_checks": [
            "metabolomics differential-abundance CSV exists",
            "metabolomics QC summary JSON exists",
            "the workflow uses the metabolomics wrapper rather than RNA-seq or proteomics wrappers",
        ],
        "rerun_triggers": [
            "the plan switches to proteomics_diff_abundance or RNA-seq differential-expression wrappers for metabolomics feature tables",
            "the feature table contains malformed non-numeric values",
            "the metadata table does not preserve the intended comparison groups",
        ],
        "source_provenance": [
            "processed metabolomics benchmark corpus",
            "repo-local deterministic metabolomics workflow",
        ],
        "open_risks": [
            "raw LC-MS feature extraction remains intentionally out of scope for v1",
            "very high missingness can reduce differential-feature recovery",
        ],
        "plan_skeleton": [
            (
                "metabolomics_diff_abundance",
                "Run deterministic differential metabolite-feature abundance analysis on the feature matrix and metadata table",
                {
                    "normalization_method": "median_center",
                    "min_present_fraction": 0.5,
                    "impute_method": "feature_median",
                },
            ),
        ],
    }


def _seed_transcript_quantification(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    if semantically_requests_stringtie_quant(query_l) and "stringtie_quant" in skills:
        return {
            "biological_objective": "Quantify transcripts directly from the provided aligned RNA-seq BAM using the supplied annotation GTF.",
            "context_facts": _dedupe(
                [
                    "alignment-based transcript quantification workflow",
                    "the request already provides an aligned BAM plus annotation GTF",
                    "the deliverables are StringTie-style outputs such as an assembled GTF or gene abundance table",
                    "do not replace the aligned-BAM workflow with FASTQ pseudoalignment or gene-counting stages",
                ]
            ),
            "candidate_methods": ["stringtie_quant"],
            "chosen_method": "stringtie_quant",
            "preferred_tools": ["stringtie_quant"],
            "discouraged_tools": [
                tool
                for tool in ["salmon_quant", "kallisto_quant", "featurecounts_run", "bash_run"]
                if tool in skills
            ],
            "parameter_profile": [
                {
                    "tool_name": "stringtie_quant",
                    "settings": {"threads": 4, "estimate_reference_only": True},
                    "rationale": "Use the alignment-based StringTie wrapper with the provided annotation instead of switching to pseudoalignment.",
                },
            ],
            "acceptance_checks": [
                "assembled or quantified GTF output exists",
                "gene abundance TSV is produced when requested",
                "the plan preserves the provided BAM and annotation GTF instead of substituting transcriptome references or gene-counting stages",
            ],
            "rerun_triggers": [
                "the plan replaces the aligned-BAM StringTie workflow with Salmon, kallisto, or featureCounts",
                "the annotation GTF is swapped to an unrelated reference or omitted from the final plan",
            ],
            "source_provenance": ["Bio-Harness StringTie wrapper"],
            "open_risks": [],
            "plan_skeleton": [
                (
                    "stringtie_quant",
                    "Quantify transcripts directly from the provided aligned BAM and annotation GTF with StringTie",
                    {"threads": 4, "estimate_reference_only": True},
                ),
            ],
        }
    preferred = [tool for tool in ["salmon_quant", "kallisto_quant"] if tool in skills]
    chosen = "salmon_quant" if "salmon_quant" in skills else ("kallisto_quant" if "kallisto_quant" in skills else "")
    discouraged = [tool for tool in ["star_align", "featurecounts_run"] if tool in skills and _has_query_cue(query_l, "transcript", "transcriptome", "salmon", "kallisto")]
    return {
        "biological_objective": "Estimate transcript-level abundance or counts with output semantics that match the requested deliverable.",
        "context_facts": _dedupe(
            [
                "transcript-level deliverable",
                "paired-end RNA-seq" if _has_query_cue(query_l, "paired-end", "paired end", "reads_1", "reads_2") else "",
                "transcriptome reference expected" if _has_query_cue(query_l, "transcriptome", "transcript fasta", "transcriptome.fa") else "",
            ]
        ),
        "candidate_methods": preferred,
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": discouraged,
        "parameter_profile": [
            {"tool_name": "salmon_quant", "settings": {"validateMappings": True, "library_type": "A"}, "rationale": "Use selective alignment / validation and infer library type when metadata is incomplete."},
            {"tool_name": "kallisto_quant", "settings": {}, "rationale": "Use as a secondary quantification option when method comparison is needed."},
        ],
        "acceptance_checks": [
            "transcript identifiers match the reference naming scheme",
            "exported count column reflects tool-native count semantics",
            "truth comparison is performed at the transcript level",
        ],
        "rerun_triggers": [
            "exported output uses length or TPM where counts were requested",
            "identifier overlap with truth is low despite successful quantification",
        ],
        "source_provenance": [
            "Salmon documentation",
            "Salmon selective alignment paper",
        ],
        "open_risks": [
            "library type may need explicit override if metadata is available later",
        ],
    }


def _seed_rna_seq_de(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    if _is_count_matrix_de_request(query_l) and "deseq2_run" in skills:
        return {
            "biological_objective": "Run differential expression directly from the provided count matrix and sample metadata.",
            "context_facts": _dedupe(
                [
                    "count matrix and sample metadata are already provided",
                    "do not insert alignment, quantification, or feature-counting stages before the DE run",
                    "the request is for gene-level differential expression, not exon-usage or alternative splicing",
                ]
            ),
            "candidate_methods": [tool for tool in ["deseq2_run", "edger_run", "limma_voom_run"] if tool in skills],
            "chosen_method": "deseq2_run",
            "preferred_tools": [tool for tool in ["deseq2_run", "edger_run", "limma_voom_run"] if tool in skills],
            "discouraged_tools": [
                tool
                for tool in [
                    "featurecounts_run",
                    "star_align",
                    "star_2pass_align",
                    "salmon_quant",
                    "kallisto_quant",
                    "dexseq_run",
                    "rmats_run",
                    "majiq_run",
                ]
                if tool in skills
            ],
            "parameter_profile": [
                {
                    "tool_name": "deseq2_run",
                    "settings": {},
                    "rationale": "The provided inputs already satisfy the direct count-matrix DESeq2 path.",
                },
            ],
            "acceptance_checks": [
                "metadata rows match count-matrix columns exactly",
                "the DE tool runs directly on the provided count matrix and metadata",
                "the plan does not drift into DEXSeq or alternative-splicing analysis",
            ],
            "rerun_triggers": [
                "the plan adds alignment, counting, or pseudoalignment despite explicit count-table inputs",
                "the plan selects dexseq_run, rmats_run, or majiq_run for a gene-level DE request",
            ],
            "source_provenance": ["DESeq2 vignette", "explicit count-matrix DE request"],
            "open_risks": [],
            "plan_skeleton": [
                ("deseq2_run", "Run differential expression directly from the provided count matrix and sample metadata", {}),
            ],
        }
    preferred = [tool for tool in ["featurecounts_run", "deseq2_run", "salmon_quant", "kallisto_quant", "edger_run", "limma_voom_run"] if tool in skills]
    chosen = "featurecounts_run + deseq2_run" if {"featurecounts_run", "deseq2_run"}.issubset(skills) else ("deseq2_run" if "deseq2_run" in skills else "")
    return {
        "biological_objective": "Produce a differential-expression analysis whose counting/import route and model design match the experimental comparison.",
        "context_facts": _dedupe(
            [
                "group comparison is required",
                "replicate-aware differential expression",
                "paired-end counting likely required" if _has_query_cue(query_l, "paired-end", "paired end", "reads_1", "reads_2") else "",
            ]
        ),
        "candidate_methods": preferred,
        "chosen_method": chosen,
        "preferred_tools": [tool for tool in ["featurecounts_run", "deseq2_run", "salmon_quant"] if tool in skills],
        "discouraged_tools": [tool for tool in ["freebayes_call", "gatk_haplotypecaller", "bcftools_call"] if tool in skills],
        "parameter_profile": [
            {"tool_name": "featurecounts_run", "settings": {"count_read_pairs": True}, "rationale": "Use explicit paired-end counting when paired-end RNA-seq is present."},
            {"tool_name": "deseq2_run", "settings": {}, "rationale": "A DESeq2 run is only interpretable with an explicit design and contrast."},
        ],
        "acceptance_checks": [
            "metadata rows match count columns exactly",
            "design formula matches the biological comparison",
            "DE output includes standard result statistics for the intended contrast",
        ],
        "rerun_triggers": [
            "metadata mismatch or all-zero counts",
            "paired-end counting was omitted for paired-end reads",
            "design formula is generic but the request implies covariates or pairing",
        ],
        "source_provenance": [
            "DESeq2 vignette",
            "tximport vignette",
            "Rsubread documentation",
        ],
        "open_risks": [
            "strandedness may still be ambiguous without metadata",
        ],
        "plan_skeleton": [
            ("star_align", "Align RNA-seq reads to reference genome", {"outSAMtype": "BAM SortedByCoordinate"}),
            ("featurecounts_run", "Count reads per gene", {"count_read_pairs": True}),
            ("deseq2_run", "Run differential expression analysis", {}),
        ],
    }


def _seed_alternative_splicing(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["star_align", "star_2pass_align", "rmats_run", "dexseq_run", "majiq_run"] if tool in skills]
    chosen = "star_align + rmats_run" if {"star_align", "rmats_run"}.issubset(skills) else ("rmats_run" if "rmats_run" in skills else "")
    return {
        "biological_objective": "Run splice-aware analysis with a dedicated splicing method rather than generic differential-expression tooling alone.",
        "context_facts": ["alternative splicing is part of the requested deliverable"],
        "candidate_methods": preferred,
        "chosen_method": chosen,
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["featurecounts_run", "deseq2_run"] if tool in skills],
        "parameter_profile": [
            {"tool_name": "star_align", "settings": {"splice_aware": True}, "rationale": "Splicing analyses require splice-aware alignment or equivalent splice graph construction."},
        ],
        "acceptance_checks": [
            "splicing event tables are produced",
            "at least one quantified event is reported",
        ],
        "rerun_triggers": ["zero splicing events", "alignment produced but splicing tool not run"],
        "source_provenance": ["rMATS documentation", "DEXSeq documentation"],
        "open_risks": ["sample size limitations may reduce splicing detection power"],
    }


def _seed_metagenomics_classification(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["spades_assemble", "bash_run", "fastqc_run"] if tool in skills]
    chosen_method = "spades_assemble + bash_run" if {"spades_assemble", "bash_run"}.issubset(skills) else ("bash_run" if "bash_run" in skills else "")
    return {
        "biological_objective": "Assemble the metagenomic reads and classify them taxonomically to produce community composition profiles.",
        "context_facts": _dedupe([
            "shotgun metagenomics workflow",
            "paired-end reads expected" if _has_query_cue(query_l, "paired-end", "paired end", "reads_1") else "",
            "The deliverable includes a metagenome assembly contigs FASTA in addition to the classification report.",
            (
                "A repo-local helper script is available at "
                f"{METAGENOMICS_KMER_HELPER_SCRIPT} to classify reads against the staged bacterial reference panel."
            ),
            "Write a Kraken-style report instead of a placeholder unclassified-only table.",
            "Do not fabricate placeholder contigs or stub assembly outputs.",
        ]),
        "candidate_methods": ["metaSPAdes assembly followed by reference-panel k-mer taxonomic profiling"],
        "chosen_method": chosen_method,
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["metagenomics_kraken2_bracken_style"] if tool in skills],
        "parameter_profile": [
            {
                "tool_name": "spades_assemble",
                "settings": {
                    "meta_mode": True,
                },
                "rationale": "Use metaSPAdes to produce the requested metagenome contigs FASTA from paired-end shotgun reads.",
            },
            {
                "tool_name": "bash_run",
                "settings": {
                    "tool": "python3",
                    "helper_script": str(METAGENOMICS_KMER_HELPER_SCRIPT),
                },
                "rationale": "Use the repo-local k-mer helper to produce the required Kraken-style report when native metagenomics classifiers are unavailable.",
            },
        ],
        "acceptance_checks": [
            "metagenome contigs FASTA exists",
            "taxonomic classification report exists",
            "report includes unclassified, root, genus, and species rows",
            "community profile matches Kraken-style tabular format",
        ],
        "rerun_triggers": [
            "assembly contigs FASTA is missing",
            "classification rate is extremely low",
            "the workflow emits a placeholder unclassified-only report",
        ],
        "source_provenance": ["BioAgentBench metagenomics task", "repo-local staged bacterial reference panel"],
        "open_risks": [
            "reference panel coverage affects classification accuracy",
            "metagenome assembly quality depends on community complexity and depth",
        ],
        "plan_skeleton": [
            (
                "spades_assemble",
                "Assemble the paired-end metagenomics reads with metaSPAdes and write the requested contigs FASTA under the assembly output directory",
                {"meta_mode": True},
            ),
            (
                "bash_run",
                "Classify the paired-end metagenomics reads against the staged bacterial reference panel with the repo-local k-mer helper and write the requested Kraken-style report",
                {"tool": "python3", "helper_script": str(METAGENOMICS_KMER_HELPER_SCRIPT)},
            ),
        ],
    }


def _seed_single_cell(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["sc_count_and_cluster", "scanpy_workflow", "bash_run"] if tool in skills]
    return {
        "biological_objective": "Preprocess single-cell RNA-seq data and identify cell clusters with marker genes.",
        "context_facts": _dedupe([
            "single-cell RNA-seq workflow",
            "scanpy-based analysis" if _has_query_cue(query_l, "scanpy", "python") else "",
            "seurat-based analysis" if _has_query_cue(query_l, "seurat", "r ") else "",
        ]),
        "candidate_methods": ["scanpy", "seurat"],
        "chosen_method": "sc_count_and_cluster" if "sc_count_and_cluster" in skills else ("scanpy_workflow" if "scanpy_workflow" in skills else ""),
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [
            {"tool_name": "sc_count_and_cluster", "settings": {
                "min_genes": 3, "min_cells": 1, "kmer_size": 25, "leiden_resolution": 0.5,
            }, "rationale": "Kmer-based counting + scanpy for small 10x benchmark data."},
            {"tool_name": "scanpy_workflow", "settings": {
                "min_genes": 300, "min_cells": 20, "max_mito_pct": 15,
                "n_hvgs": 2000, "leiden_resolution": 0.3,
            }, "rationale": "Standard scanpy preprocessing and clustering parameters."},
        ],
        "acceptance_checks": ["UMAP/clustering output exists", "marker gene tables produced"],
        "rerun_triggers": ["clustering produces only 1 cluster", "excessive cell filtering"],
        "source_provenance": ["Scanpy tutorials", "BioAgentBench single-cell task"],
        "open_risks": ["optimal resolution may need tuning"],
        "plan_skeleton": [
            ("sc_count_and_cluster", "Demultiplex, count UMIs, cluster cells, find marker genes", {
                "min_genes": 3, "min_cells": 1, "kmer_size": 25, "leiden_resolution": 0.5,
            }),
        ],
    }


def _seed_spatial_transcriptomics(
    query_l: str,
    skills: set[str],
    available_skill_names: list[str],
) -> dict[str, Any]:
    """Return the deterministic seed for processed spatial transcriptomics."""

    preferred = [
        tool
        for tool in ["spatial_transcriptomics_workflow", "scanpy_workflow", "bash_run"]
        if tool in skills
    ]
    chosen_method = (
        "spatial_transcriptomics_workflow"
        if "spatial_transcriptomics_workflow" in skills
        else ("scanpy_workflow" if "scanpy_workflow" in skills else "")
    )
    return {
        "biological_objective": (
            "Identify spatial domains and marker genes from a processed spatial transcriptomics dataset."
        ),
        "context_facts": _dedupe(
            [
                "processed spatial transcriptomics workflow",
                "Visium-style spot coordinates" if _has_query_cue(query_l, "visium", "spot", "spots") else "",
                "AnnData spatial input" if _has_query_cue(query_l, "h5ad", "anndata") else "",
                "deterministic spatial clustering and marker extraction",
            ]
        ),
        "candidate_methods": ["spatial_transcriptomics_workflow", "scanpy_workflow"],
        "chosen_method": chosen_method,
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [
            {
                "tool_name": "spatial_transcriptomics_workflow",
                "settings": {
                    "min_genes": 3,
                    "min_cells": 2,
                    "n_hvgs": 50,
                    "n_pcs": 10,
                },
                "rationale": "Deterministic processed-input spatial workflow for benchmark-style Visium inputs.",
            },
        ],
        "acceptance_checks": [
            "spatial domain assignments CSV exists",
            "spatial marker genes CSV exists",
            "processed spatial AnnData output exists",
        ],
        "rerun_triggers": [
            "all retained spots collapse into one domain despite visible spatial structure",
            "marker genes file is empty",
            "spatial coordinates are missing or malformed",
        ],
        "source_provenance": [
            "processed-input spatial transcriptomics benchmark corpus",
            "repo-local deterministic spatial workflow",
        ],
        "open_risks": [
            "raw-image registration is intentionally out of scope for v1",
            "domain count may need tuning on higher-complexity tissues",
        ],
        "plan_skeleton": [
            (
                "spatial_transcriptomics_workflow",
                "Run deterministic spatial domain identification and marker extraction on the processed AnnData input",
                {
                    "min_genes": 3,
                    "min_cells": 2,
                    "n_hvgs": 50,
                    "n_pcs": 10,
                },
            ),
        ],
    }


def _seed_germline_variant_calling(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["bwa_mem_align", "gatk_haplotypecaller", "bash_run"] if tool in skills]
    return {
        "biological_objective": "Call germline variants and benchmark against a truth set (e.g., GIAB).",
        "context_facts": _dedupe([
            "germline variant calling workflow",
            "GIAB truth set benchmarking" if _has_query_cue(query_l, "giab", "genome in a bottle", "nist") else "",
        ]),
        "candidate_methods": ["gatk_haplotypecaller", "deepvariant"],
        "chosen_method": "gatk_haplotypecaller" if "gatk_haplotypecaller" in skills else "",
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [
            {"tool_name": "bwa_mem_align", "settings": {"postprocess_mode": "fixmate_markdup_q20"}, "rationale": "Standard preprocessing for germline variant calling."},
        ],
        "acceptance_checks": ["VCF output exists", "hap.py benchmarking results produced if truth set available"],
        "rerun_triggers": ["zero variants called", "extremely low recall against truth set"],
        "source_provenance": ["GATK best practices", "BioAgentBench GIAB task"],
        "open_risks": ["reference genome version must match truth set"],
        "plan_skeleton": [
            ("bwa_mem_align", "Align reads to reference", {"postprocess_mode": "fixmate_markdup_q20"}),
            ("gatk_haplotypecaller", "Call germline variants", {}),
            ("bash_run", "Benchmark with hap.py", {"tool": "hap.py"}),
        ],
    }


def _seed_somatic_variant_calling(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["bwa_mem_align", "gatk_mutect2_call", "bash_run"] if tool in skills]
    return {
        "biological_objective": "Call somatic variants from tumor-normal paired samples.",
        "context_facts": _dedupe([
            "somatic variant calling workflow",
            "tumor-normal paired analysis",
            "Mutect2 somatic caller" if _has_query_cue(query_l, "mutect") else "",
        ]),
        "candidate_methods": ["gatk_mutect2_call", "bcftools_call"],
        "chosen_method": "gatk_mutect2_call" if "gatk_mutect2_call" in skills else "",
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [
            {"tool_name": "bwa_mem_align", "settings": {"postprocess_mode": "fixmate_markdup_q20"}, "rationale": "Standard preprocessing for somatic variant calling."},
        ],
        "acceptance_checks": ["Somatic VCF output exists", "filtered variants present"],
        "rerun_triggers": ["zero somatic variants called", "no PASS variants in output"],
        "source_provenance": ["GATK best practices for somatic"],
        "open_risks": ["tumor purity may affect sensitivity", "matched normal required for Mutect2"],
        "plan_skeleton": [
            ("bwa_mem_align", "Align tumor reads to reference", {"postprocess_mode": "fixmate_markdup_q20"}),
            ("bwa_mem_align", "Align normal reads to reference", {"postprocess_mode": "fixmate_markdup_q20"}),
            ("gatk_mutect2_call", "Call somatic variants", {}),
        ],
    }


def _seed_viral_metagenomics(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    trim_tool = "fastp_run" if "fastp_run" in skills else "bash_run"
    preferred = [tool for tool in ["fastp_run", "bash_run", "fastqc_run"] if tool in skills]
    if "minimap2_align" not in skills:
        return {
            "biological_objective": "Identify and classify viruses from the paired-end reads and report per-virus coverage and abundance.",
            "context_facts": _dedupe([
                "viral metagenomics workflow",
                "paired-end viral reference-panel classification",
                (
                    "A repo-local helper script is available at "
                    f"{VIRAL_KMER_HELPER_SCRIPT} to classify reads against the staged viral FASTA panel."
                ),
                "Do not fabricate placeholder viral reports or empty detected-virus lists.",
            ]),
            "candidate_methods": ["reference-panel viral k-mer classification"],
            "chosen_method": "python_kmer_viral_classification",
            "preferred_tools": [tool for tool in ["bash_run", "fastqc_run"] if tool in skills],
            "discouraged_tools": [],
            "parameter_profile": [
                {
                    "tool_name": "bash_run",
                    "settings": {
                        "tool": "python3",
                        "helper_script": str(VIRAL_KMER_HELPER_SCRIPT),
                    },
                    "rationale": "Use the repo-local viral helper to compute per-virus coverage and abundance in the current environment.",
                },
            ],
            "acceptance_checks": ["classification report with per-virus coverage exists", "detected viruses list produced"],
            "rerun_triggers": ["zero classified reads", "no classification report", "detected viruses list is empty"],
            "source_provenance": ["BioAgentBench viral-metagenomics task"],
            "open_risks": ["viral reference panel must cover the sequenced viruses"],
            "plan_skeleton": [
                (
                    "bash_run",
                    "Classify the paired-end reads against the staged viral reference panel with the repo-local helper and write the requested coverage and detection outputs",
                    {"tool": "python3", "helper_script": str(VIRAL_KMER_HELPER_SCRIPT)},
                ),
            ],
        }
    return {
        "biological_objective": "Identify and classify viruses against a staged viral reference panel and report coverage and abundance.",
        "context_facts": _dedupe([
            "viral metagenomics workflow",
            "paired-end viral reference-panel classification",
            "repo-local helper-backed viral classification path",
        ]),
        "candidate_methods": ["helper-backed viral reference classification"],
        "chosen_method": f"{trim_tool} + bash_run" if trim_tool else "bash_run",
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [
            {
                "tool_name": trim_tool,
                "settings": {"length_required": 30, "detect_adapter_for_pe": True},
                "rationale": "Trim paired-end viral reads before reference-panel classification.",
            },
            {
                "tool_name": "bash_run",
                "settings": {"tool": "python3", "helper_script": str(VIRAL_KMER_HELPER_SCRIPT)},
                "rationale": "Use the repo-local viral classifier helper to materialize the required classification outputs.",
            },
        ],
        "acceptance_checks": ["classification report with per-virus coverage exists", "detected viruses list produced"],
        "rerun_triggers": ["zero classified reads", "no classification report"],
        "source_provenance": ["BioAgentBench viral-metagenomics task"],
        "open_risks": ["viral reference panel must cover expected viruses"],
        "plan_skeleton": [
            (
                trim_tool,
                "Quality trim paired-end reads before viral classification",
                {"length_required": 30, "detect_adapter_for_pe": True},
            ),
            (
                "bash_run",
                "Classify trimmed reads against the staged viral reference panel and write coverage and detection outputs",
                {"tool": "python3", "helper_script": str(VIRAL_KMER_HELPER_SCRIPT)},
            ),
        ],
    }


def _seed_variant_annotation(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["snpeff_annotate", "bash_run"] if tool in skills]
    is_cf_task = _has_query_cue(query_l, "cystic", "cftr", "recessive", "affected siblings")
    is_local_task = not is_cf_task and _is_local_variant_annotation_task(query_l)
    if is_local_task:
        return {
            "biological_objective": "Annotate the provided VCF against the supplied FASTA/GFF reference and retain only HIGH and MODERATE impact variants.",
            "context_facts": _dedupe([
                "variant annotation workflow",
                "local reference FASTA and GFF define the annotation coordinate system",
                "the task already provides an input VCF that should be annotated rather than recalled",
                "the filtered deliverable is a HIGH/MODERATE-impact VCF, not a clinical CSV",
            ]),
            "candidate_methods": ["custom snpeff database + impact filter"],
            "chosen_method": "snpeff_annotate" if "snpeff_annotate" in skills else "",
            "preferred_tools": preferred,
            "discouraged_tools": [],
            "parameter_profile": [
                {
                    "tool_name": "snpeff_annotate",
                    "settings": {"genome_db": "custom_ref"},
                    "rationale": "Use the provided FASTA and GFF to build a local annotation database instead of a stock human genome.",
                },
                {
                    "tool_name": "bash_run",
                    "settings": {"tool": "SnpSift", "impact_levels": ["HIGH", "MODERATE"]},
                    "rationale": "Retain only the requested HIGH and MODERATE impact variants from the annotated VCF.",
                },
            ],
            "acceptance_checks": [
                "annotated VCF exists and is derived from the provided local FASTA/GFF inputs",
                "filtered pathogenic VCF contains only HIGH and MODERATE impact variants",
            ],
            "rerun_triggers": [
                "the plan switches to GRCh37.75 or another external genome database instead of the provided FASTA/GFF pair",
                "the workflow tries to call variants from reads instead of annotating the provided VCF",
                "zero annotated variants or zero HIGH/MODERATE variants are emitted from the provided input VCF",
            ],
            "source_provenance": ["SnpEff documentation", "BioAgentBench variant-annotation task"],
            "open_risks": ["the local GFF, FASTA, and VCF coordinates must remain in the same reference frame"],
            "plan_skeleton": [
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
            ],
        }
    return {
        "biological_objective": (
            "Identify the causal variant in an annotated family VCF and export a clinically interpretable CSV."
            if is_cf_task
            else "Annotate variants with functional impact and filter for clinically relevant mutations."
        ),
        "context_facts": _dedupe([
            "variant annotation workflow",
            "clinical relevance filtering" if _has_query_cue(query_l, "clinvar", "clinical", "cystic", "cftr") else "",
            "recessive family-segregation filter" if is_cf_task else "",
            "affected siblings should be homozygous alternate while parents remain carriers" if is_cf_task else "",
        ]),
        "candidate_methods": ["annotated VCF + segregation filter + ClinVar join" if is_cf_task else "snpeff + snpsift"],
        "chosen_method": "snpeff_annotate" if "snpeff_annotate" in skills else "",
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [
            {"tool_name": "snpeff_annotate", "settings": {"genome_db": "GRCh37.75"}, "rationale": "Standard human genome annotation database."},
        ],
        "acceptance_checks": [
            "annotated VCF exists",
            "filtered variant table produced",
            "final CSV contains the requested clinical columns" if is_cf_task else "",
        ],
        "rerun_triggers": [
            "annotation database not found",
            "zero annotated variants",
            "recessive segregation filter returns zero CFTR candidates" if is_cf_task else "",
        ],
        "source_provenance": ["SnpEff documentation", "BioAgentBench cystic-fibrosis task"],
        "open_risks": ["genome build must match input VCF coordinates"],
        "plan_skeleton": [
            ("snpeff_annotate", "Annotate variants with SnpEff if the input VCF is not already annotated", {"genome_db": "GRCh37.75"}),
            ("bash_run", "Filter for recessive segregation across affected siblings and parents", {"tool": "python3" if is_cf_task else "SnpSift"}),
            ("bash_run", "Join ClinVar annotations when a matching local ClinVar VCF is available", {"tool": "python3" if is_cf_task else "SnpSift"}),
            ("bash_run", "Export the final clinically relevant CSV", {"tool": "python3" if is_cf_task else ""}),
        ],
    }


def _seed_comparative_genomics(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["bash_run"] if tool in skills]
    return {
        "biological_objective": "Compute pairwise ANI (Average Nucleotide Identity) and identify closest genome pairs.",
        "context_facts": [
            "comparative genomics workflow",
            "minimap2 asm20 preset for whole-genome alignment",
            "PAF-based ANI computation",
        ],
        "candidate_methods": ["minimap2 all-vs-all + PAF ANI"],
        "chosen_method": "minimap2",
        "preferred_tools": preferred,
        "discouraged_tools": [],
        "parameter_profile": [],
        "acceptance_checks": [
            "distance matrix CSV exists with correct dimensions",
            "summary TSV with ANI and aligned fraction per pair",
            "closest pair identified",
        ],
        "rerun_triggers": ["minimap2 not found", "zero alignments in PAF"],
        "source_provenance": ["minimap2 documentation", "ANI computation methodology"],
        "open_risks": ["highly divergent genomes (>25% divergence) may have low aligned fraction"],
        "plan_skeleton": [
            ("bash_run", "All-vs-all pairwise alignment with minimap2", {"tool": "minimap2"}),
            ("bash_run", "Compute ANI distance matrix from PAF alignments", {"tool": "python3"}),
        ],
    }


def _seed_phylogenetics(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["bash_run"] if tool in skills]
    return {
        "biological_objective": "Infer a phylogenetic tree from the provided homologous sequences and write a valid Newick tree.",
        "context_facts": _dedupe([
            "phylogenetics workflow",
            "protein or nucleotide homologs are provided in a multi-sequence FASTA",
            (
                "A repo-local helper script is available at "
                f"{PHYLOGENY_HELPER_SCRIPT} to build a distance-based tree with Biopython."
            ),
            "Do not fabricate placeholder Newick trees or one-tip outputs.",
        ]),
        "candidate_methods": ["distance-based phylogeny with a Biopython helper"],
        "chosen_method": "python_biopython_phylogeny",
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["phylogenetics_iqtree_style"] if tool in skills],
        "parameter_profile": [
            {
                "tool_name": "bash_run",
                "settings": {
                    "tool": "python3",
                    "helper_script": str(PHYLOGENY_HELPER_SCRIPT),
                },
                "rationale": "Use the repo-local Biopython helper to infer a phylogenetic tree when native aligner/tree binaries are unavailable.",
            },
        ],
        "acceptance_checks": [
            "final Newick tree exists at the requested output path",
            "tree contains all taxa from the provided FASTA",
            "tree is not a placeholder single-tip output",
        ],
        "rerun_triggers": [
            "output tree is missing or not parseable as Newick",
            "output tree drops taxa from the input FASTA",
            "the workflow falls back to a placeholder tree or missing-tool wrapper output",
        ],
        "source_provenance": ["BioAgentBench phylogenetics task", "Biopython Phylo tree-construction utilities"],
        "open_risks": ["distance-based inference may be sensitive to poor homology or heavily gapped inputs"],
        "plan_skeleton": [
            (
                "bash_run",
                "Align the provided sequences conceptually and infer a phylogenetic tree using the repo-local Biopython helper, then write the requested Newick output",
                {"tool": "python3", "helper_script": str(PHYLOGENY_HELPER_SCRIPT)},
            ),
        ],
    }


def _seed_multi_model_dge_pathway(query_l: str, skills: set[str], available_skill_names: list[str]) -> dict[str, Any]:
    preferred = [tool for tool in ["bash_run"] if tool in skills]
    return {
        "biological_objective": "Perform comparative differential expression analysis across the mouse models and identify shared KEGG pathways.",
        "context_facts": _dedupe([
            "DGE + pathway enrichment workflow",
            "mouse model" if _has_query_cue(query_l, "mouse", "mus musculus") else "",
            "Use repo-local helper scripts that already exist in the project when they implement the requested multi-model comparison.",
            (
                "The repo-local compare_pathways.py helper under bio_harness/pipeline_scripts is available for "
                "real multi-model DE plus KEGG comparison."
            ),
            "Do not replace this assay type with exon-level or count-only DE wrappers.",
            "Do not fabricate placeholder pathway databases, toy pathway names, or mock enrichment results.",
            "Do not guess missing intermediate files; downstream steps must consume concrete outputs produced by prior steps.",
        ]),
        "candidate_methods": ["repo-local compare_pathways.py helper"],
        "chosen_method": "bash_run" if "bash_run" in skills else "",
        "preferred_tools": preferred,
        "discouraged_tools": [tool for tool in ["dexseq_run", "deseq2_run", "edger_run", "limma_voom_run"] if tool in skills],
        "parameter_profile": [
            {
                "tool_name": "bash_run",
                "settings": {
                    "tool": "python3",
                    "helper_script": str(COMPARE_PATHWAYS_HELPER_SCRIPT),
                },
                "rationale": "Use the repo-local helper to compute the requested multi-model DE and KEGG comparison without inventing sample assignments.",
            },
        ],
        "acceptance_checks": ["DE gene table exists", "pathway enrichment results produced"],
        "rerun_triggers": [
            "zero DE genes found",
            "enrichment analysis fails",
            "the workflow bypasses compare_pathways.py with inline DE or enrichment code",
        ],
        "source_provenance": ["BioAgentBench alzheimer-mouse task"],
        "open_risks": ["GMT gene set file must be in data_root for enrichment"],
        "plan_skeleton": [
            (
                "bash_run",
                "Invoke compare_pathways.py to filter counts, run DGE, and compute shared KEGG enrichment",
                {"tool": "python3", "helper_script": str(COMPARE_PATHWAYS_HELPER_SCRIPT)},
            ),
        ],
    }


# ---------------------------------------------------------------------------
# Dispatch table mapping analysis_type -> builder function
# ---------------------------------------------------------------------------

_PROFILE_BUILDERS: dict[str, _BuilderFn] = {
    "direct_skill_smoke": _seed_direct_skill_smoke,
    "artifact_schema_profiling": _seed_artifact_schema_profiling,
    "run_reporting": _seed_run_reporting,
    "bacterial_evolution_variant_calling": _seed_bacterial_evolution,
    "long_read_assembly": _seed_long_read_assembly,
    "long_read_rna": _seed_long_read_rna,
    "metabolomics": _seed_metabolomics,
    "proteomics": _seed_proteomics,
    "structural_variant_calling": _seed_structural_variant_calling,
    "transcript_quantification": _seed_transcript_quantification,
    "rna_seq_differential_expression": _seed_rna_seq_de,
    "alternative_splicing": _seed_alternative_splicing,
    "metagenomics_classification": _seed_metagenomics_classification,
    "single_cell_rna_seq": _seed_single_cell,
    "spatial_transcriptomics": _seed_spatial_transcriptomics,
    "germline_variant_calling": _seed_germline_variant_calling,
    "somatic_variant_calling": _seed_somatic_variant_calling,
    "viral_metagenomics": _seed_viral_metagenomics,
    "variant_annotation": _seed_variant_annotation,
    "comparative_genomics": _seed_comparative_genomics,
    "phylogenetics": _seed_phylogenetics,
    "multi_model_dge_pathway": _seed_multi_model_dge_pathway,
}

_DEFAULT_SEED: dict[str, Any] = {
    "biological_objective": "Produce a biologically plausible result with method choices that fit the request.",
    "context_facts": [],
    "candidate_methods": [],
    "chosen_method": "",
    "preferred_tools": [],
    "discouraged_tools": [],
    "parameter_profile": [],
    "acceptance_checks": ["required deliverables should exist and match the requested output semantics"],
    "rerun_triggers": [],
    "source_provenance": ["request contract and available tool metadata"],
    "open_risks": [],
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _profile_seed(analysis_type: str, user_query: str, available_skill_names: list[str]) -> dict[str, Any]:
    """Build a profile seed for the given analysis type.

    Uses a dispatch table to look up the builder for the analysis type.
    Falls back to a generic default when no specific builder is registered.
    """
    query_l = str(user_query or "").lower()
    skills = {str(name).strip() for name in available_skill_names if str(name).strip()}

    # Check for explicit skill override first (before dispatch table).
    explicit_seed = _explicit_requested_skill_seed(analysis_type, user_query, skills)
    if explicit_seed is not None:
        return explicit_seed

    builder = _PROFILE_BUILDERS.get(analysis_type)
    if builder is not None:
        return builder(query_l, skills, available_skill_names)

    # Fallback: generic seed with top-4 available skills as candidates.
    fallback = dict(_DEFAULT_SEED)
    fallback["candidate_methods"] = [tool for tool in available_skill_names[:4] if tool]
    return fallback
