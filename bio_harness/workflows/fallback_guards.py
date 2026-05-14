"""Semantic guards for fallback-catalog selection."""

from __future__ import annotations

from typing import Any

_ANALYSIS_TYPE_PIPELINE_PREFIXES: dict[str, tuple[str, ...]] = {
    "bacterial_evolution_variant_calling": ("germline_variant_",),
    "germline_variant_calling": ("germline_variant_",),
    "long_read_assembly": ("long_read_assembly_", "lr_assembly_"),
    "long_read_rna": ("lr_rna_",),
    "metabolomics": (),
    "proteomics": (),
    "metagenomics_classification": ("metagenomics_",),
    "multi_model_dge_pathway": ("differential_expression_",),
    "phylogenetics": ("phylogenetics_",),
    "rna_seq_differential_expression": ("differential_expression_",),
    "somatic_variant_calling": ("somatic_variant_",),
    "transcript_quantification": (),
    "variant_annotation": ("variant_annotation_",),
    "viral_metagenomics": ("metagenomics_",),
}
_ANALYSIS_TYPE_SEMANTIC_CAPABILITIES: dict[str, frozenset[str]] = {
    "single_cell_rna_seq": frozenset({"single_cell_analysis"}),
}
# Some analysis types have dedicated fallback families identified by pipeline
# prefix. Others, such as single-cell workflows, currently have no dedicated
# catalog family; for those we fall back to capability compatibility instead of
# a hardcoded allow-all escape hatch.


def requested_analysis_type(
    contract: dict[str, Any],
    preference_profile: dict[str, Any] | None = None,
) -> str:
    """Return the normalized requested analysis type when available."""

    if isinstance(preference_profile, dict):
        value = str(preference_profile.get("analysis_type", "") or "").strip()
        if value:
            return value
    return str(contract.get("analysis_type", "") or "").strip()


def pipeline_matches_analysis_type(pipeline_id: str, analysis_type: str) -> bool:
    """Return whether *pipeline_id* stays within the requested task class."""

    normalized_type = str(analysis_type or "").strip().lower()
    if not normalized_type:
        return True
    prefixes = _ANALYSIS_TYPE_PIPELINE_PREFIXES.get(normalized_type)
    if prefixes is None:
        return True
    if not prefixes:
        return False
    pipeline_key = str(pipeline_id or "").strip().lower()
    return any(pipeline_key.startswith(prefix) for prefix in prefixes)


def template_matches_analysis_type(
    template: dict[str, Any],
    *,
    analysis_type: str,
    requested_capabilities: set[str] | None = None,
) -> bool:
    """Return whether one fallback template is compatible with the request.

    Args:
        template: Fallback catalog template row.
        analysis_type: Requested canonical analysis type.
        requested_capabilities: Normalized capabilities requested by the
            contract.

    Returns:
        ``True`` when the template stays within the requested task class or
        satisfies the semantic capability profile for analysis types that do
        not yet have a dedicated fallback family.
    """

    normalized_type = str(analysis_type or "").strip().lower()
    if not normalized_type:
        return True
    pipeline_id = str(template.get("pipeline_id", "") or "").strip()
    if normalized_type in _ANALYSIS_TYPE_PIPELINE_PREFIXES:
        if pipeline_matches_analysis_type(pipeline_id, normalized_type):
            return True
        return False

    semantic_caps = _ANALYSIS_TYPE_SEMANTIC_CAPABILITIES.get(normalized_type)
    if not semantic_caps:
        return True
    active_caps = set(requested_capabilities or set()).intersection(semantic_caps)
    if not active_caps:
        return True
    template_caps = {
        str(item).strip()
        for item in (
            template.get("contract_capabilities", [])
            if isinstance(template.get("contract_capabilities", []), list)
            else []
        )
        if str(item).strip()
    }
    return bool(template_caps.intersection(active_caps))
