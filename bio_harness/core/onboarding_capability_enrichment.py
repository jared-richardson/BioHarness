"""Deterministic metadata enrichment for onboarded skills.

This module fills in weak or missing onboarding metadata without replacing the
scientific plan itself. It focuses on stable fields that improve later routing
and retrieval: capability ids, usage guidance, lightweight input/output types,
and analysis-category hints.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from bio_harness.core.capability_catalog import infer_capabilities_from_text, normalize_capability_id

_WEAK_CAPABILITY_IDS = frozenset({"analysis", "custom_capability", "custom"})
_INPUT_HINTS = (
    ("fastq", ("fastq", "fq", "reads_1", "reads_2", "read1", "read2")),
    ("bam", ("bam", "cram", "input_bam", "input_bams", "aligned_bam")),
    ("vcf", ("vcf", "bcf", "variants")),
    ("gtf", ("gtf", "annotation_gtf")),
    ("gff", ("gff", "gff3", "annotation_gff")),
    ("fasta_reference", ("fasta", "fa", "fna", "reference_genome", "reference_fasta", "genome")),
    ("h5ad", ("h5ad",)),
    ("tsv", ("tsv", "counts_matrix", "metadata_table", "gene_counts")),
    ("csv", ("csv",)),
)
_OUTPUT_HINTS = (
    ("bam", (".bam", ".cram")),
    ("vcf", (".vcf", ".bcf", ".vcf.gz")),
    ("gtf", (".gtf", ".gtf.gz")),
    ("gff", (".gff", ".gff3", ".gff.gz")),
    ("tsv", (".tsv", ".tsv.gz")),
    ("csv", (".csv", ".csv.gz")),
    ("json", (".json",)),
    ("h5ad", (".h5ad",)),
    ("html", (".html",)),
)


def enrich_onboarding_metadata(
    draft: Mapping[str, Any],
    *,
    manual_summary: Mapping[str, Any] | None = None,
    capability_catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Return deterministic metadata enrichment for one onboarding draft.

    Args:
        draft: Raw onboarding draft or normalized skill metadata.
        manual_summary: Optional structured documentation summary.
        capability_catalog: Loaded capability catalog used for deterministic
            capability inference.

    Returns:
        Dictionary of enriched metadata fields that can be merged into the
        onboarding draft before persistence.
    """

    manual_data = manual_summary if isinstance(manual_summary, Mapping) else {}
    explicit_caps = _normalize_capabilities(draft.get("capabilities", []))
    inferred_caps = _infer_capabilities(draft, manual_data, capability_catalog)
    capabilities = _choose_capabilities(explicit_caps, inferred_caps)

    return {
        "capabilities": capabilities,
        "when_to_use": _choose_text(
            draft.get("when_to_use"),
            manual_data.get("when_to_use"),
            draft.get("description"),
        ),
        "when_not_to_use": _choose_text(
            draft.get("when_not_to_use"),
            manual_data.get("when_not_to_use"),
        ),
        "input_types": _infer_input_types(draft),
        "output_types": _infer_output_types(draft, manual_data),
        "analysis_categories": _infer_analysis_categories(
            draft,
            manual_data,
            capabilities=capabilities,
        ),
    }


def _normalize_capabilities(values: Any) -> list[str]:
    """Return normalized capability ids from an arbitrary sequence."""

    if not isinstance(values, list):
        return []
    return _dedupe(
        normalize_capability_id(str(value))
        for value in values
        if normalize_capability_id(str(value))
    )


def _choose_capabilities(explicit_caps: list[str], inferred_caps: list[str]) -> list[str]:
    """Prefer explicit capabilities unless they are missing or generic."""

    strong_explicit = [cap for cap in explicit_caps if cap not in _WEAK_CAPABILITY_IDS]
    if strong_explicit:
        return strong_explicit
    merged = strong_explicit + inferred_caps
    if merged:
        return _dedupe(merged)
    return explicit_caps or ["custom_capability"]


def _infer_capabilities(
    draft: Mapping[str, Any],
    manual_summary: Mapping[str, Any],
    capability_catalog: Mapping[str, Any],
) -> list[str]:
    """Infer capabilities from descriptive text and trusted documentation."""

    haystack_parts = [
        draft.get("skill_name", ""),
        draft.get("name", ""),
        draft.get("description", ""),
        draft.get("usage_guide", ""),
        draft.get("command_template", ""),
        " ".join(str(value) for value in draft.get("tools_required", []) or []),
        manual_summary.get("when_to_use", ""),
        manual_summary.get("when_not_to_use", ""),
        " ".join(str(value) for value in manual_summary.get("canonical_outputs", []) or []),
        " ".join(str(value) for value in manual_summary.get("example_invocations", []) or []),
    ]
    parameters = draft.get("parameters", {})
    if isinstance(parameters, Mapping):
        for name, spec in parameters.items():
            haystack_parts.append(str(name))
            if isinstance(spec, Mapping):
                haystack_parts.append(str(spec.get("description", "")))
                haystack_parts.append(str(spec.get("file_role", "")))

    for entry in manual_summary.get("common_errors", []) or []:
        if not isinstance(entry, Mapping):
            continue
        haystack_parts.append(str(entry.get("pattern", "")))
        haystack_parts.append(str(entry.get("cause", "")))

    haystack = " ".join(part for part in haystack_parts if str(part).strip())
    inferred = infer_capabilities_from_text(haystack, dict(capability_catalog))
    haystack_l = haystack.lower()
    if "single_cell_analysis" in inferred and not any(
        token in haystack_l
        for token in ("single-cell", "single cell", "scanpy", "seurat", "cellranger", "scrna", "h5ad")
    ):
        inferred = [cap for cap in inferred if cap != "single_cell_analysis"]
    return inferred


def _infer_input_types(draft: Mapping[str, Any]) -> list[str]:
    """Infer lightweight input-type tags from parameter names and file roles."""

    inferred: list[str] = []
    parameters = draft.get("parameters", {})
    if not isinstance(parameters, Mapping):
        return inferred
    for name, spec in parameters.items():
        param_name = str(name).strip().lower()
        if not param_name:
            continue
        if not isinstance(spec, Mapping):
            spec = {}
        if str(spec.get("type", "")).strip().lower() != "path":
            continue
        if any(token in param_name for token in ("output", "outdir", "prefix")):
            continue
        role = str(spec.get("file_role", "")).strip().lower()
        desc = str(spec.get("description", "")).strip().lower()
        text = " ".join(part for part in (param_name, role, desc) if part)
        for input_type, hints in _INPUT_HINTS:
            if any(hint in text for hint in hints):
                inferred.append(input_type)
                break
    return _dedupe(inferred)


def _infer_output_types(
    draft: Mapping[str, Any],
    manual_summary: Mapping[str, Any],
) -> list[str]:
    """Infer output-type tags from existing metadata and canonical outputs."""

    outputs: list[str] = []
    raw_output_types = draft.get("output_types", [])
    if isinstance(raw_output_types, list):
        outputs.extend(str(value).strip().lower() for value in raw_output_types if str(value).strip())

    output_names: list[str] = []
    parameters = draft.get("parameters", {})
    if isinstance(parameters, Mapping):
        for name, spec in parameters.items():
            param_name = str(name).strip().lower()
            if not param_name or not isinstance(spec, Mapping):
                continue
            if str(spec.get("type", "")).strip().lower() != "path":
                continue
            if "output" in param_name or "outdir" in param_name or "prefix" in param_name:
                output_names.append(param_name)
                output_names.append(str(spec.get("description", "")).strip().lower())

    manual_outputs = [str(value).strip().lower() for value in manual_summary.get("canonical_outputs", []) or []]
    for value in output_names + manual_outputs:
        if not value:
            continue
        for output_type, suffixes in _OUTPUT_HINTS:
            if any(value.endswith(suffix) for suffix in suffixes):
                outputs.append(output_type)
                break
        if "bam" in value or "cram" in value:
            outputs.append("bam")
        elif "vcf" in value or "bcf" in value:
            outputs.append("vcf")
        elif "gtf" in value:
            outputs.append("gtf")
        elif "gff" in value:
            outputs.append("gff")
        elif "tsv" in value:
            outputs.append("tsv")
        elif "csv" in value:
            outputs.append("csv")
        if value.endswith("_dir") or value.endswith("outdir") or value.endswith("output_dir"):
            outputs.append("directory")
    return _dedupe(outputs)


def _infer_analysis_categories(
    draft: Mapping[str, Any],
    manual_summary: Mapping[str, Any],
    *,
    capabilities: list[str],
) -> list[str]:
    """Infer coarse analysis-category tags from capabilities and text."""

    explicit = draft.get("analysis_categories", [])
    if isinstance(explicit, list) and any(str(value).strip() for value in explicit):
        return _dedupe(str(value).strip().lower() for value in explicit if str(value).strip())

    text = " ".join(
        str(part).strip().lower()
        for part in (
            draft.get("description", ""),
            draft.get("usage_guide", ""),
            draft.get("when_to_use", ""),
            manual_summary.get("when_to_use", ""),
            draft.get("command_template", ""),
            " ".join(str(value) for value in draft.get("tools_required", []) or []),
            " ".join(str(value) for value in manual_summary.get("canonical_outputs", []) or []),
        )
        if str(part).strip()
    )
    categories: list[str] = []
    if "single_cell_analysis" in capabilities:
        categories.append("single_cell_analysis")
    if "differential_analysis" in capabilities and any(token in text for token in ("deseq", "edger", "limma", "count matrix", "metadata")):
        categories.append("rna_seq_differential_expression")
    if "quantification" in capabilities and any(token in text for token in ("transcript", "salmon", "kallisto", "stringtie", "abundance")):
        categories.append("transcript_quantification")
    if "alignment" in capabilities and any(token in text for token in ("align", "mapping", "star", "hisat2", "bwa", "bowtie")):
        categories.append("alignment")
    if "variant_calling" in capabilities:
        categories.append("somatic_variant_calling" if "somatic" in text or "mutect" in text else "germline_variant_calling")
    if not categories:
        categories.extend(capabilities)
    return _dedupe(categories)


def _choose_text(*values: Any) -> str:
    """Return the first non-empty text value."""

    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _dedupe(values: Any) -> list[str]:
    """Return stable deduplicated non-empty strings."""

    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "").strip().lower())
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
