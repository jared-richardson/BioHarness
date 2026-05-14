from __future__ import annotations

# ruff: noqa: F403,F405
from bio_harness.core.analysis_spec_support import *

def _apply_blind_rna_seq_alignment_preferences(
    payload: Dict[str, Any],
    *,
    available: set[str],
) -> None:
    """Align RNA-seq DE guidance with grounded GFF alignment constraints.

    Protocol grounding may remove ``star_align`` for GFF-only tasks and replace
    it with ``subread_align``. Keep the analysis brief consistent with that
    grounding so the LLM is seeded with the same executable scaffold instead of
    drifting back to STAR plus an invented genome index.
    """
    if str(payload.get("analysis_type", "") or "").strip() != "rna_seq_differential_expression":
        return

    grounding = payload.get("protocol_grounding", {})
    if not isinstance(grounding, dict):
        return

    required_tools = {
        str(tool).strip()
        for tool in (grounding.get("required_tools", []) or [])
        if str(tool).strip()
    }
    compatible_tools = {
        str(tool).strip()
        for tool in (grounding.get("compatible_tools", []) or [])
        if str(tool).strip()
    }
    has_gff_reference = _payload_has_gff_reference(payload)
    subread_available = (
        "subread_align" in required_tools
        or "subread_align" in compatible_tools
        or "subread_align" in available
    )
    if not subread_available:
        return
    if "subread_align" not in required_tools and not has_gff_reference:
        return

    preferred = [tool for tool in ["subread_align", "featurecounts_run", "deseq2_run"] if not available or tool in available]
    if preferred:
        payload["preferred_tools"] = preferred

    discouraged = list(payload.get("discouraged_tools", []) or [])
    for tool_name in ("star_align", "star_2pass_align"):
        if not available or tool_name in available:
            discouraged.append(tool_name)
    payload["discouraged_tools"] = _dedupe([str(tool).strip() for tool in discouraged if str(tool).strip()])

    if {"featurecounts_run", "deseq2_run"}.issubset(set(preferred)):
        payload["chosen_method"] = "featurecounts_run + deseq2_run"
    elif preferred:
        payload["chosen_method"] = preferred[0]

    payload["context_facts"] = _dedupe(
        list(payload.get("context_facts", []) or [])
        + [
            "GFF annotation is available without a staged STAR index, so the alignment path should stay compatible with featureCounts over GFF features.",
            "Do not invent a genome_index directory or switch back to STAR unless the plan explicitly builds it first.",
        ]
    )
    payload["plan_skeleton"] = [
        ("subread_align", "Align each paired-end RNA-seq sample against the reference genome with a GFF-compatible aligner", {"threads": 8}),
        ("featurecounts_run", "Count reads per gene from the aligned BAM files", {"count_read_pairs": True}),
        ("deseq2_run", "Run differential expression analysis for the planktonic versus biofilm contrast", {}),
    ]


def _payload_has_gff_reference(payload: Dict[str, Any]) -> bool:
    """Return whether discovered inputs include a GFF/GFF3 annotation file."""

    for item in payload.get("discovered_data_files", []) or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "") or item.get("name", "") or "").strip().lower()
        if path.endswith((".gff", ".gff3", ".gff.gz", ".gff3.gz")):
            return True
    return False


def _grounded_chosen_method_hint(
    *,
    analysis_type: str,
    chosen_method: str,
    preferred_tools: List[str],
    protocol_grounding: Dict[str, Any],
    available: set[str],
) -> str:
    grounded = protocol_grounding if isinstance(protocol_grounding, dict) else {}
    required_tools = [str(x).strip() for x in (grounded.get("required_tools", []) or []) if str(x).strip()]
    preferred_grounded = [str(x).strip() for x in (grounded.get("preferred_tools", []) or []) if str(x).strip()]
    candidate_pool = _dedupe(required_tools + preferred_grounded + list(preferred_tools or []))
    if available:
        candidate_pool = [tool for tool in candidate_pool if tool in available]

    if analysis_type == "bacterial_evolution_variant_calling":
        caller_tools = [tool for tool in candidate_pool if tool in CALLER_LIKE_TOOLS]
        if caller_tools:
            if chosen_method not in caller_tools:
                return caller_tools[0]
            return chosen_method
    return chosen_method
