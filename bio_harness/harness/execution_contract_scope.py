"""Execution-contract scoping helpers for plan validation.

These helpers keep the request contract aligned with the normalized execution
contract so direct-wrapper plans are judged against compatible tools and
capabilities instead of incidental sibling mentions from the raw prompt.
They also apply active path-graph or analysis-spec tool preferences so raw
prompt mentions do not override an explicit discouraged-tool policy later in
validation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.harness.contract_prompt_intent import tool_hint_aliases
from bio_harness.harness.stream_utils import _normalize_contract_hint

_TOOL_PREFERENCE_FAMILY_HINTS: dict[str, set[str]] = {
    "gatk": {
        "gatk",
        "gatk_haplotypecaller",
        "gatk_mutect2",
        "gatk_mutect2_call",
        "haplotypecaller",
        "mutect2",
    },
}


def scoped_capabilities_for_execution_contract(
    *,
    analysis_family: str,
    input_mode: str,
    capabilities: list[str],
) -> list[str]:
    """Filter raw contract capabilities against one execution contract.

    Args:
        analysis_family: Canonical analysis family.
        input_mode: Stable execution input-mode token.
        capabilities: Raw contract capability hints.

    Returns:
        Capability hints that remain relevant for the resolved execution mode.
    """

    caps = [str(cap).strip() for cap in capabilities if str(cap).strip()]
    if not caps:
        return []
    family = str(analysis_family or "").strip()
    mode = str(input_mode or "").strip()

    if family == "transcript_quantification":
        allowed = {"annotation", "quantification", "reference_inputs"}
        return [cap for cap in caps if cap in allowed]
    if family == "single_cell_rna_seq" and mode in {"processed_single_cell", "count_matrix"}:
        allowed = {"single_cell_analysis"}
        return [cap for cap in caps if cap in allowed]
    if family == "spatial_transcriptomics" and mode == "processed_single_cell":
        allowed = {"spatial_transcriptomics", "single_cell_analysis"}
        return [cap for cap in caps if cap in allowed]
    if family == "structural_variant_calling" and mode == "raw_fastq":
        allowed = {"alignment", "reference_inputs", "structural_variant_calling"}
        return [cap for cap in caps if cap in allowed]
    if family == "long_read_assembly" and mode == "raw_fastq":
        allowed = {"genome_assembly"}
        return [cap for cap in caps if cap in allowed]
    if family == "long_read_rna" and mode == "raw_fastq":
        allowed = {"alignment", "reference_inputs"}
        return [cap for cap in caps if cap in allowed]
    if family == "metabolomics" and mode == "count_matrix":
        allowed = {"metabolomics", "differential_analysis", "group_comparison"}
        return [cap for cap in caps if cap in allowed]
    if family == "proteomics" and mode == "count_matrix":
        allowed = {"proteomics", "differential_analysis", "group_comparison"}
        return [cap for cap in caps if cap in allowed]
    if family == "rna_seq_differential_expression" and mode == "count_matrix":
        allowed = {"differential_analysis", "group_comparison"}
        return [cap for cap in caps if cap in allowed]

    if mode == "aligned_bam":
        return [cap for cap in caps if cap != "alignment"]
    if mode in {"count_matrix", "processed_single_cell"}:
        return [
            cap
            for cap in caps
            if cap not in {"alignment", "quantification", "reference_inputs"}
        ]
    return caps


def is_compatible_tool_hint(hint: str, compatible_tools: set[str]) -> bool:
    """Return whether a tool hint matches one of the compatible wrappers."""

    raw_hint = str(hint or "").strip()
    token = _normalize_contract_hint(raw_hint)
    if not token:
        token = raw_hint.lower().strip("`\"'()[]{}<>:;,")
        if "/" in token or "\\" in token:
            token = Path(token).name.lower()
        if re.search(r"[^a-z0-9_.-]", token):
            return False
    if not token:
        return False
    token_l = token.lower()
    if token_l in compatible_tools:
        return True
    return any(
        tool.startswith(token_l) or token_l.startswith(tool)
        for tool in compatible_tools
    )


def _expand_preference_hint_tokens(values: Any) -> set[str]:
    """Normalize preference tokens into suppressible tool-hint aliases."""

    raw_values = values if isinstance(values, list) else []
    alias_map = tool_hint_aliases()
    expanded: set[str] = set()
    for item in raw_values:
        raw = str(item).strip()
        if not raw:
            continue
        token = _normalize_contract_hint(raw) or raw.lower().strip("`\"'()[]{}<>:;,")
        if not token:
            continue
        expanded.add(token)
        normalized = str(alias_map.get(token, token) or "").strip().lower()
        if normalized:
            expanded.add(normalized)
        family_hints = _TOOL_PREFERENCE_FAMILY_HINTS.get(token, set())
        expanded.update(str(hint).strip().lower() for hint in family_hints if str(hint).strip())
    return {token for token in expanded if token}


def _filter_preference_suppressed_hints(values: Any, suppressed_tokens: set[str]) -> list[str]:
    """Drop contract hints that conflict with active discouraged-tool policy."""

    raw_values = values if isinstance(values, list) else []
    if not suppressed_tokens:
        return [str(item).strip() for item in raw_values if str(item).strip()]

    kept: list[str] = []
    for item in raw_values:
        raw = str(item).strip()
        if not raw:
            continue
        token = _normalize_contract_hint(raw) or raw.lower().strip("`\"'()[]{}<>:;,")
        if not token:
            kept.append(raw)
            continue
        token_l = token.lower()
        blocked = any(
            suppressed == token_l
            or suppressed in token_l
            or token_l in suppressed
            for suppressed in suppressed_tokens
        )
        if not blocked:
            kept.append(raw)
    return kept


def scope_contract_to_execution_mode(
    contract: dict[str, Any],
    analysis_spec: dict[str, Any] | None,
    *,
    preference_profile: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], set[str]]:
    """Narrow request-contract hints to the resolved execution boundary.

    Args:
        contract: Raw inferred request contract.
        analysis_spec: Normalized analysis spec carrying ``execution_contract``
            and explicit execution intent.

    Args:
        contract: Raw inferred request contract.
        analysis_spec: Normalized analysis spec carrying ``execution_contract``
            and explicit execution intent.
        preference_profile: Active path-graph and analysis-spec preferences.

    Returns:
        A tuple of ``(scoped_contract, compatible_tool_set)``.
    """

    scoped = dict(contract) if isinstance(contract, dict) else {}
    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    prefs = preference_profile if isinstance(preference_profile, dict) else {}
    suppressed_hint_tokens = _expand_preference_hint_tokens(prefs.get("discouraged_tools", []))
    suppressed_hint_tokens.update(
        _expand_preference_hint_tokens(prefs.get("tool_blacklist", []))
    )
    if suppressed_hint_tokens:
        scoped["explicit_tool_hints"] = _filter_preference_suppressed_hints(
            scoped.get("explicit_tool_hints", []),
            suppressed_hint_tokens,
        )
        scoped["required_tool_hints"] = _filter_preference_suppressed_hints(
            scoped.get("required_tool_hints", []),
            suppressed_hint_tokens,
        )
    execution_contract = (
        spec.get("execution_contract", {})
        if isinstance(spec.get("execution_contract", {}), dict)
        else {}
    )
    execution_mode = str(execution_contract.get("execution_mode", "") or "").strip()
    compatible_tools = {
        str(tool).strip().lower()
        for tool in (execution_contract.get("compatible_tools", []) or [])
        if str(tool).strip()
    }
    explicit_intent = (
        spec.get("explicit_execution_intent", {})
        if isinstance(spec.get("explicit_execution_intent", {}), dict)
        else {}
    )
    compatible_tools.update(
        str(tool).strip().lower()
        for tool in (explicit_intent.get("locked_tools", []) or [])
        if str(tool).strip()
    )
    if execution_mode != "direct_wrapper" or not compatible_tools:
        return scoped, set()

    def _filter_hints(values: Any) -> list[str]:
        raw = values if isinstance(values, list) else []
        return [
            str(item).strip()
            for item in raw
            if str(item).strip() and is_compatible_tool_hint(str(item), compatible_tools)
        ]

    scoped["explicit_tool_hints"] = _filter_hints(scoped.get("explicit_tool_hints", []))
    scoped["required_tool_hints"] = _filter_hints(scoped.get("required_tool_hints", []))

    input_mode = str(execution_contract.get("input_mode", "") or "").strip()
    analysis_family = str(execution_contract.get("analysis_family", "") or "").strip()
    raw_capabilities = [
        str(cap).strip()
        for cap in (scoped.get("must_include_capabilities", []) or [])
        if str(cap).strip()
    ]
    scoped["must_include_capabilities"] = scoped_capabilities_for_execution_contract(
        analysis_family=analysis_family,
        input_mode=input_mode,
        capabilities=raw_capabilities,
    )
    return scoped, compatible_tools


__all__ = [
    "is_compatible_tool_hint",
    "scope_contract_to_execution_mode",
    "scoped_capabilities_for_execution_contract",
]
