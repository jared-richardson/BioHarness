"""Helper utilities for analysis-spec profile seeding."""

from __future__ import annotations

import re
from typing import Any

from bio_harness.core.analysis_spec_support import _dedupe
from bio_harness.core.request_scope import (
    infer_explicit_requested_skill,
    requested_skill_analysis_type,
)


def has_query_cue(query_l: str, *tokens: str) -> bool:
    """Return whether the lowercased query contains any of *tokens*."""

    return any(token in query_l for token in tokens)


def is_local_variant_annotation_task(query_l: str) -> bool:
    """Return whether the prompt describes a local annotation task."""

    indicators = (
        "genes.gff",
        "reference.fa",
        "input_variants.vcf",
        "provided reference fasta",
        "provided gff",
        "local structured eval",
        "annotated.vcf",
        "filtered_pathogenic.vcf",
    )
    hits = sum(1 for token in indicators if token in query_l)
    return hits >= 2


def is_count_matrix_de_request(query_l: str) -> bool:
    """Return whether a request already provides count-table DE inputs."""

    has_counts = any(
        token in query_l
        for token in (
            "count matrix",
            "counts matrix",
            "counts table",
            "gene_counts",
            "counts.tsv",
            "counts.txt",
        )
    )
    has_metadata = any(
        token in query_l
        for token in ("metadata", "sample metadata", "metadata.tsv", "coldata")
    )
    has_raw_read_cues = any(
        token in query_l
        for token in (
            ".fastq",
            ".fq",
            "reads_1",
            "reads_2",
            "r1.fastq",
            "r2.fastq",
            ".bam",
        )
    )
    has_splicing_cues = any(
        token in query_l for token in ("splicing", "exon", "dexseq", "rmats", "majiq")
    )
    return has_counts and has_metadata and not has_raw_read_cues and not has_splicing_cues


def smoke_requested_skills(
    query_l: str,
    available_skill_names: list[str],
) -> list[str]:
    """Return skill names explicitly mentioned in the query."""

    requested: list[str] = []
    for raw_name in available_skill_names:
        skill_name = str(raw_name or "").strip()
        if not skill_name or skill_name == "bash_run":
            continue
        if skill_name.lower() in query_l and skill_name not in requested:
            requested.append(skill_name)
    return requested


def explicit_requested_skill_seed(
    analysis_type: str,
    user_query: str,
    skills: set[str],
) -> dict[str, Any] | None:
    """Return a focused profile seed for explicitly requested skills."""

    available_skill_names = sorted(skills)
    explicit_skill = infer_explicit_requested_skill(user_query, available_skill_names)
    if not explicit_skill:
        return None
    if requested_skill_analysis_type(explicit_skill) != analysis_type:
        return None

    if explicit_skill == "scanpy_workflow" and explicit_skill in skills:
        return {
            "biological_objective": "Run the deterministic Scanpy workflow directly on the provided pre-counted single-cell dataset.",
            "context_facts": _dedupe(
                [
                    "single-cell RNA-seq workflow",
                    "the user explicitly requested the scanpy_workflow wrapper",
                    "prefer pre-counted matrix or h5ad execution over FASTQ-first single-cell preprocessing",
                    "do not replace the requested wrapper with sc_count_and_cluster when the input is already a pre-counted matrix",
                ]
            ),
            "candidate_methods": ["scanpy_workflow"],
            "chosen_method": "scanpy_workflow",
            "preferred_tools": ["scanpy_workflow"],
            "discouraged_tools": [
                tool for tool in ["sc_count_and_cluster", "bash_run"] if tool in skills
            ],
            "parameter_profile": [
                {
                    "tool_name": "scanpy_workflow",
                    "settings": {
                        "min_genes": 300,
                        "min_cells": 20,
                        "max_mito_pct": 15,
                        "n_hvgs": 2000,
                        "leiden_resolution": 0.3,
                    },
                    "rationale": "Use the deterministic bundled Scanpy preprocessing and clustering path for pre-counted input data.",
                },
            ],
            "acceptance_checks": [
                "processed h5ad output exists",
                "cluster assignments or marker gene tables are produced",
                "the plan uses scanpy_workflow directly instead of a FASTQ-first single-cell counting workflow",
            ],
            "rerun_triggers": [
                "the plan replaces scanpy_workflow with sc_count_and_cluster or a generic bash pipeline",
                "the input is a valid pre-counted dataset but the plan still asks for raw-cell-count preprocessing inputs",
            ],
            "source_provenance": ["Bio-Harness deterministic Scanpy workflow"],
            "open_risks": [],
            "plan_skeleton": [
                (
                    "scanpy_workflow",
                    "Run deterministic Scanpy preprocessing, clustering, and marker-gene export on the provided pre-counted single-cell dataset",
                    {
                        "min_genes": 300,
                        "min_cells": 20,
                        "max_mito_pct": 15,
                        "n_hvgs": 2000,
                        "leiden_resolution": 0.3,
                    },
                ),
            ],
        }

    if explicit_skill == "deseq2_run" and explicit_skill in skills:
        exact_wrapper_requested = bool(
            re.search(r"(?<![A-Za-z0-9_])deseq2_run(?![A-Za-z0-9_])", str(user_query or "").lower())
        )
        if not exact_wrapper_requested and not is_count_matrix_de_request(str(user_query or "").lower()):
            return None
        return {
            "biological_objective": "Run DESeq2 directly from the provided count matrix and sample metadata without inserting alignment or counting stages.",
            "context_facts": _dedupe(
                [
                    "RNA-seq differential expression analysis",
                    "the user explicitly requested the deseq2_run wrapper",
                    "count matrix and sample metadata are the primary inputs",
                    "do not replace the requested wrapper with alignment plus feature counting when counts are already provided",
                ]
            ),
            "candidate_methods": ["deseq2_run"],
            "chosen_method": "deseq2_run",
            "preferred_tools": ["deseq2_run"],
            "discouraged_tools": [
                tool
                for tool in [
                    "star_align",
                    "star_2pass_align",
                    "featurecounts_run",
                    "salmon_quant",
                    "kallisto_quant",
                ]
                if tool in skills
            ],
            "parameter_profile": [
                {
                    "tool_name": "deseq2_run",
                    "settings": {},
                    "rationale": "Keep the run on the direct count-matrix DESeq2 path with an explicit design and contrast.",
                },
            ],
            "acceptance_checks": [
                "metadata rows match count-matrix columns exactly",
                "DESeq2 results table is produced for the requested contrast",
                "the plan does not add alignment or gene-counting stages before deseq2_run",
            ],
            "rerun_triggers": [
                "the plan drifts into raw FASTQ alignment or feature counting despite explicit count-matrix inputs",
                "sample metadata is ignored or regenerated from filenames when a metadata table is already provided",
            ],
            "source_provenance": ["Bio-Harness DESeq2 wrapper"],
            "open_risks": [],
            "plan_skeleton": [
                (
                    "deseq2_run",
                    "Run differential expression directly from the provided count matrix and sample metadata",
                    {},
                ),
            ],
        }

    if explicit_skill == "stringtie_quant" and explicit_skill in skills:
        return {
            "biological_objective": "Run StringTie directly on the provided aligned RNA-seq BAM and annotation GTF.",
            "context_facts": _dedupe(
                [
                    "transcript quantification workflow",
                    "the user explicitly requested the stringtie_quant wrapper",
                    "alignment-based quantification is required because the input is an aligned BAM plus an annotation GTF",
                    "do not replace the requested wrapper with FASTQ pseudoalignment quantification or generic transcriptome workflows",
                ]
            ),
            "candidate_methods": ["stringtie_quant"],
            "chosen_method": "stringtie_quant",
            "preferred_tools": ["stringtie_quant"],
            "discouraged_tools": [
                tool for tool in ["salmon_quant", "kallisto_quant", "bash_run"] if tool in skills
            ],
            "parameter_profile": [
                {
                    "tool_name": "stringtie_quant",
                    "settings": {"threads": 4, "estimate_reference_only": True},
                    "rationale": "Use the alignment-based StringTie wrapper with reference-guided abundance estimation for an existing BAM input.",
                },
            ],
            "acceptance_checks": [
                "assembled or quantified GTF output exists",
                "gene abundance TSV is produced when requested",
                "the plan preserves the requested BAM and annotation GTF instead of substituting unrelated references",
            ],
            "rerun_triggers": [
                "the plan replaces stringtie_quant with Salmon or kallisto despite the explicit BAM-plus-GTF request",
                "the annotation GTF path is swapped to an unrelated workspace reference during normalization or repair",
            ],
            "source_provenance": ["Bio-Harness StringTie wrapper"],
            "open_risks": [],
            "plan_skeleton": [
                (
                    "stringtie_quant",
                    "Quantify expression directly from the provided aligned BAM and annotation GTF with StringTie",
                    {"threads": 4, "estimate_reference_only": True},
                ),
            ],
        }

    return None
