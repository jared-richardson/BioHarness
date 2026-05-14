"""Stable facade for reference-path extraction, discovery, and repair helpers."""

from __future__ import annotations

from bio_harness.harness.contract_reference_detection import (
    _explicit_requested_reference_paths,
    _extract_reference_paths_from_plan,
    _looks_like_fasta_path,
    _looks_like_task_local_generated_reference,
    _looks_like_transcriptome_fasta_path,
    _path_has_request_context_marker,
    _pick_reference_paths_from_text,
    _planned_converted_gtf_path,
    _preserve_current_reference_path,
)
from bio_harness.harness.contract_reference_fallback import (
    _find_alias_reference,
    _find_reference_candidate,
    _find_reference_candidate_in_roots,
    _repair_missing_references_in_plan,
    _resolve_reference_paths,
    _resolve_reference_paths_for_template_fallback,
)
from bio_harness.harness.contract_reference_indexing import (
    _find_prebuilt_quant_index,
    _stable_index_base_for_tool,
    _stable_quant_index_path_for_tool,
)
from bio_harness.harness.contract_reference_workspace import (
    _find_workspace_reference,
    _repair_requested_references_and_index_bases_in_plan,
    _workspace_reference_alias_candidates,
    _workspace_search_roots,
)

__all__ = [
    "_explicit_requested_reference_paths",
    "_extract_reference_paths_from_plan",
    "_find_alias_reference",
    "_find_prebuilt_quant_index",
    "_find_reference_candidate",
    "_find_reference_candidate_in_roots",
    "_find_workspace_reference",
    "_looks_like_fasta_path",
    "_looks_like_task_local_generated_reference",
    "_looks_like_transcriptome_fasta_path",
    "_path_has_request_context_marker",
    "_pick_reference_paths_from_text",
    "_planned_converted_gtf_path",
    "_preserve_current_reference_path",
    "_repair_missing_references_in_plan",
    "_repair_requested_references_and_index_bases_in_plan",
    "_resolve_reference_paths",
    "_resolve_reference_paths_for_template_fallback",
    "_stable_index_base_for_tool",
    "_stable_quant_index_path_for_tool",
    "_workspace_reference_alias_candidates",
    "_workspace_search_roots",
]
