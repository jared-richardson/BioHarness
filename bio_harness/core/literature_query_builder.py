"""Deterministic literature-query planning for biomedical research mode.

This module keeps search query generation benchmark-safe and reproducible. It
does not depend on an LLM and does not perform network access. Instead it
expands one research question into a small family of method-aware search
queries using assay, tool, and intent cues.
"""

from __future__ import annotations

import re

_ABBREVIATION_EXPANSIONS = {
    "de": "differential expression",
    "rnaseq": "RNA-seq",
    "wgs": "whole genome sequencing",
    "atacseq": "ATAC-seq",
}

_ASSAY_PROFILES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "atac_seq",
        ("atac", "chromatin accessibility", "macs2", "tn5"),
        ("ATAC-seq", "chromatin accessibility", "MACS2", "Tn5 shifting", "peak calling"),
    ),
    (
        "direct_rna",
        ("direct rna", "nanopore", "ont", "minimap2"),
        ("Oxford Nanopore", "direct RNA sequencing", "minimap2", "splice alignment", "alignment preset"),
    ),
    (
        "rna_seq_de",
        ("rna-seq", "differential expression", "deseq2", "edger"),
        ("RNA-seq", "differential expression", "DESeq2", "edgeR", "small sample"),
    ),
)

_TOOL_SYNONYMS = {
    "deseq2": ("DESeq2", "negative binomial"),
    "edger": ("edgeR", "empirical bayes"),
    "macs2": ("MACS2", "peak calling"),
    "minimap2": ("minimap2", "alignment preset", "splice alignment"),
    "salmon": ("Salmon", "quantification"),
    "kallisto": ("kallisto", "pseudoalignment"),
}

_INTENT_HINTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("preprocessing", ("preprocess", "preprocessing"), ("preprocessing", "published methods")),
    ("parameter", ("parameter", "preset", "value", "setting"), ("recommended parameter", "published methods")),
    ("protocol", ("standard", "best practice", "published methods", "commonly used"), ("published methods", "recommended workflow")),
    ("alignment", ("align", "alignment"), ("alignment", "recommended preset")),
)


def build_literature_search_queries(
    question: str,
    analysis_type: str,
    tools: tuple[str, ...],
    *,
    max_queries: int = 4,
) -> tuple[str, ...]:
    """Build deterministic method-aware search queries.

    Args:
        question: Research question text.
        analysis_type: Analysis-family label.
        tools: Tool names referenced by the run or request.
        max_queries: Maximum number of unique queries to emit.

    Returns:
        Ordered tuple of deterministic search queries.
    """

    normalized_question = _normalize_question(question)
    profile_terms = _profile_terms(normalized_question, analysis_type)
    tool_terms = _tool_terms(tools, normalized_question)
    intent_terms = _intent_terms(normalized_question)
    question_terms = _question_keywords(normalized_question)

    primary_terms = _unique_terms(profile_terms[:3] + tool_terms[:3] + intent_terms[:2] + question_terms[:2])
    secondary_terms = _unique_terms(profile_terms + tool_terms[:2] + question_terms[:3])
    broad_terms = _unique_terms(profile_terms[:2] + intent_terms + question_terms[:4])
    tool_focused_terms = _unique_terms(tool_terms + profile_terms[:2] + intent_terms[:1])
    raw_tool_cluster = _unique_terms(tuple(str(tool).strip().lower() for tool in tools if str(tool).strip()))

    queries = [
        _render_query(primary_terms),
        _render_query(secondary_terms),
        _render_query(_unique_terms(raw_tool_cluster + question_terms[:2])) if raw_tool_cluster else "",
        _render_query(_unique_terms(broad_terms + ("bioinformatics",))),
        _render_query(tool_focused_terms),
        _render_query((_quoted_phrase(normalized_question),) if normalized_question else ()),
    ]
    deduped: list[str] = []
    seen: set[str] = set()
    for query_text in queries:
        token = " ".join(query_text.split()).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
        if len(deduped) >= max(1, int(max_queries)):
            break
    return tuple(deduped)


def _normalize_question(question: str) -> str:
    """Normalize one free-text research question."""

    text = str(question or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    lowered = re.sub(r"^\s*research:\s*", "", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    for short, expanded in _ABBREVIATION_EXPANSIONS.items():
        lowered = re.sub(rf"\b{re.escape(short)}\b", expanded.lower(), lowered)
    return lowered


def _profile_terms(question: str, analysis_type: str) -> tuple[str, ...]:
    """Infer assay-family terms from the question and analysis type."""

    haystack = f"{analysis_type or ''} {question}".lower()
    for _label, triggers, terms in _ASSAY_PROFILES:
        if any(trigger in haystack for trigger in triggers):
            return terms
    fallback = []
    normalized_analysis = str(analysis_type or "").replace("_", " ").strip()
    if normalized_analysis:
        fallback.append(normalized_analysis)
    if "bioinformatics" not in fallback:
        fallback.append("bioinformatics")
    return tuple(fallback)


def _tool_terms(tools: tuple[str, ...], question: str) -> tuple[str, ...]:
    """Expand tool mentions into method-aware query terms."""

    haystack = question.lower()
    terms: list[str] = []
    for raw_tool in tools:
        tool = str(raw_tool or "").strip()
        if not tool:
            continue
        tool_key = tool.lower()
        expansions = _TOOL_SYNONYMS.get(tool_key, ())
        terms.extend([tool] + [item for item in expansions if item])
    for tool_key, expansions in _TOOL_SYNONYMS.items():
        if tool_key in haystack:
            terms.extend(expansions)
    return _unique_terms(tuple(terms))


def _intent_terms(question: str) -> tuple[str, ...]:
    """Extract intent terms from one research question."""

    matched: list[str] = []
    for _label, triggers, terms in _INTENT_HINTS:
        if any(trigger in question for trigger in triggers):
            matched.extend(terms)
    return _unique_terms(tuple(matched))


def _question_keywords(question: str) -> tuple[str, ...]:
    """Extract bounded high-signal keywords from one normalized question."""

    if not question:
        return ()
    tokens = re.findall(r"[a-z0-9][a-z0-9\-]+", question)
    keep = []
    for token in tokens:
        if token in {"what", "which", "should", "would", "best", "based", "published", "methods", "standard"}:
            continue
        keep.append(token)
        if len(keep) >= 6:
            break
    return tuple(keep)


def _unique_terms(terms: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Return unique non-empty terms while preserving order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for raw in terms:
        text = str(raw or "").strip()
        if not text:
            continue
        token = text.lower()
        if token in seen:
            continue
        seen.add(token)
        ordered.append(text)
    return tuple(ordered)


def _render_query(terms: tuple[str, ...]) -> str:
    """Render one deterministic search query from ordered terms."""

    rendered = []
    for term in terms:
        if " " in term or "-" in term:
            rendered.append(_quoted_phrase(term))
        else:
            rendered.append(term)
    return " ".join(rendered).strip()


def _quoted_phrase(text: str) -> str:
    """Return one phrase wrapped in quotes for higher-signal retrieval."""

    token = str(text or "").strip().strip('"')
    return f"\"{token}\"" if token else ""
