"""Backend-specific deterministic query profiles for literature retrieval.

This module expands one research request into backend-specific query families.
The output is deterministic and assay-aware so benchmark behavior remains
reproducible while retrieval quality improves over backend-agnostic queries.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from bio_harness.core.literature_query_builder import build_literature_search_queries

_INTENT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("parameter", ("parameter", "preset", "setting", "value", "recommended")),
    ("caveat", ("caveat", "caveats", "downstream", "interpretation", "warning")),
    ("troubleshooting", ("error", "resolve", "failure", "fix", "troubleshooting")),
    ("protocol", ("protocol", "workflow", "steps", "preprocessing", "peak-calling", "published methods")),
)
_ASSAY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("atac_seq", ("atac", "macs2", "tn5", "chromatin accessibility")),
    ("direct_rna", ("direct rna", "nanopore", "ont", "minimap2")),
    ("rna_seq_de", ("rna-seq", "deseq2", "edger", "differential expression")),
)


@dataclass(frozen=True)
class BackendQueryPlan:
    """Structured deterministic query plan for literature backends.

    Attributes:
        assay_profile: Inferred assay-family profile label.
        intent_profile: Inferred query-intent profile label.
        pubmed_queries: Ordered PubMed query variants.
        web_queries: Ordered trusted-web query variants.
        web_domains: Ordered trusted-web domain priorities.
        citation_queries: Ordered citation-enrichment query variants.
    """

    assay_profile: str
    intent_profile: str
    pubmed_queries: tuple[str, ...]
    web_queries: tuple[str, ...]
    web_domains: tuple[str, ...]
    citation_queries: tuple[str, ...]


def build_backend_query_plan(
    question: str,
    analysis_type: str,
    tools: tuple[str, ...],
    *,
    max_queries: int = 4,
) -> BackendQueryPlan:
    """Build one deterministic backend-specific query plan.

    Args:
        question: Research question text.
        analysis_type: Analysis family label.
        tools: Tool names referenced by the request or run.
        max_queries: Maximum number of queries to emit per backend.

    Returns:
        Deterministic backend query plan.
    """

    base_queries = build_literature_search_queries(
        question,
        analysis_type,
        tools,
        max_queries=max_queries,
    )
    assay_profile = _infer_assay_profile(question, analysis_type, tools)
    intent_profile = _infer_intent_profile(question)
    pubmed_queries = _pubmed_queries(base_queries, intent_profile=intent_profile)
    web_queries = _web_queries(base_queries, assay_profile=assay_profile, intent_profile=intent_profile)
    web_domains = _web_domains(assay_profile=assay_profile, intent_profile=intent_profile)
    citation_queries = _citation_queries(base_queries, tools=tools)
    return BackendQueryPlan(
        assay_profile=assay_profile,
        intent_profile=intent_profile,
        pubmed_queries=pubmed_queries,
        web_queries=web_queries,
        web_domains=web_domains,
        citation_queries=citation_queries,
    )


def _infer_assay_profile(question: str, analysis_type: str, tools: tuple[str, ...]) -> str:
    """Infer one stable assay-profile label from the request context."""

    haystack = " ".join((str(question or ""), str(analysis_type or ""), " ".join(str(tool or "") for tool in tools))).lower()
    for label, triggers in _ASSAY_TERMS:
        if any(trigger in haystack for trigger in triggers):
            return label
    normalized = str(analysis_type or "").strip().lower().replace(" ", "_")
    return normalized or "generic"


def _infer_intent_profile(question: str) -> str:
    """Infer one stable query-intent label from the research question."""

    lowered = str(question or "").lower()
    for label, triggers in _INTENT_HINTS:
        if any(trigger in lowered for trigger in triggers):
            return label
    return "protocol"


def _pubmed_queries(base_queries: tuple[str, ...], *, intent_profile: str) -> tuple[str, ...]:
    """Render PubMed-oriented query variants."""

    suffix = {
        "parameter": '"recommended preset" "published methods"',
        "caveat": '"published methods" caveats',
        "troubleshooting": '"published methods" troubleshooting',
        "protocol": '"published methods"',
    }.get(intent_profile, '"published methods"')
    queries = [_bounded_query(_append_terms(_bounded_query(query, max_fragments=6), suffix), max_fragments=10) for query in base_queries]
    return _dedupe_queries(queries)


def _web_queries(
    base_queries: tuple[str, ...],
    *,
    assay_profile: str,
    intent_profile: str,
) -> tuple[str, ...]:
    """Render trusted-web query variants."""

    targeted = _targeted_web_queries(assay_profile=assay_profile, intent_profile=intent_profile)
    fallback = _fallback_web_queries(base_queries, assay_profile=assay_profile, intent_profile=intent_profile)
    return _dedupe_queries(targeted + fallback)[:4]


def _targeted_web_queries(*, assay_profile: str, intent_profile: str) -> list[str]:
    """Return deterministic assay-aware trusted-web query templates."""

    if assay_profile == "atac_seq":
        if intent_profile == "protocol":
            return [
                "ATAC-seq MACS2 Tn5 shifting protocol",
                "ATAC-seq peak calling preprocessing workflow",
                "ATACseqQC Bioconductor ATAC-seq preprocessing",
                "ArchR ATAC-seq peak calling workflow",
            ]
        return [
            "ATAC-seq MACS2 documentation",
            "ATACseqQC Bioconductor ATAC-seq",
            "ATAC-seq preprocessing workflow",
        ]
    if assay_profile == "direct_rna":
        if intent_profile == "parameter":
            return [
                "Oxford Nanopore direct RNA minimap2 preset documentation",
                "minimap2 direct RNA splice preset Oxford Nanopore",
                "Oxford Nanopore direct RNA alignment protocol",
                "minimap2 direct RNA documentation",
            ]
        return [
            "Oxford Nanopore direct RNA protocol documentation",
            "minimap2 direct RNA alignment workflow",
            "direct RNA splice alignment documentation",
        ]
    if assay_profile == "rna_seq_de":
        if intent_profile == "parameter":
            return [
                "DESeq2 Bioconductor vignette differential expression",
                "DESeq2 design formula documentation",
                "RNA-seq differential expression DESeq2 workflow",
            ]
        return [
            "DESeq2 Bioconductor workflow differential expression",
            "edgeR Bioconductor workflow differential expression",
            "RNA-seq differential expression vignette",
        ]
    return []


def _fallback_web_queries(
    base_queries: tuple[str, ...],
    *,
    assay_profile: str,
    intent_profile: str,
) -> list[str]:
    """Render deterministic fallback trusted-web query variants."""

    assay_suffix = {
        "atac_seq": "protocol workflow documentation",
        "direct_rna": "documentation alignment protocol",
        "rna_seq_de": "workflow documentation vignette",
    }.get(assay_profile, "protocol documentation")
    intent_suffix = {
        "parameter": "recommended preset documentation",
        "caveat": "best practices caveats review",
        "troubleshooting": "documentation troubleshooting",
        "protocol": "protocol workflow",
    }.get(intent_profile, "protocol")
    return [
        _bounded_query(
            _append_terms(_bounded_query(query, max_fragments=4), assay_suffix, intent_suffix),
            max_fragments=8,
        )
        for query in base_queries
    ]


def _web_domains(*, assay_profile: str, intent_profile: str) -> tuple[str, ...]:
    """Return ordered trusted-web domains for one assay/intention profile."""

    del intent_profile
    domains = {
        "atac_seq": (
            "bioconductor.org",
            "pmc.ncbi.nlm.nih.gov",
            "nature.com",
            "academic.oup.com",
            "ncbi.nlm.nih.gov",
            "pubmed.ncbi.nlm.nih.gov",
        ),
        "direct_rna": (
            "academic.oup.com",
            "pmc.ncbi.nlm.nih.gov",
            "nature.com",
            "ncbi.nlm.nih.gov",
            "pubmed.ncbi.nlm.nih.gov",
        ),
        "rna_seq_de": (
            "bioconductor.org",
            "pmc.ncbi.nlm.nih.gov",
            "ncbi.nlm.nih.gov",
            "pubmed.ncbi.nlm.nih.gov",
            "academic.oup.com",
        ),
    }.get(
        assay_profile,
        (
            "pmc.ncbi.nlm.nih.gov",
            "ncbi.nlm.nih.gov",
            "pubmed.ncbi.nlm.nih.gov",
            "nature.com",
            "academic.oup.com",
        ),
    )
    return tuple(domains)


def _citation_queries(base_queries: tuple[str, ...], *, tools: tuple[str, ...]) -> tuple[str, ...]:
    """Render shorter citation-enrichment query variants."""

    queries: list[str] = []
    for query in base_queries:
        compact = _bounded_query(query, max_fragments=6)
        if compact:
            queries.append(compact)
    if tools:
        queries.append(_bounded_query(" ".join(str(tool).strip() for tool in tools if str(tool).strip()).strip(), max_fragments=4))
    return _dedupe_queries(queries)


def _append_terms(query: str, *suffixes: str) -> str:
    """Append one or more bounded suffix fragments to one query string."""

    fragments = [str(query or "").strip()]
    for suffix in suffixes:
        text = str(suffix or "").strip()
        if text:
            fragments.append(text)
    return " ".join(fragment for fragment in fragments if fragment).strip()


def _dedupe_queries(queries: list[str]) -> tuple[str, ...]:
    """Return unique non-empty queries while preserving order."""

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in queries:
        query = " ".join(str(raw or "").split()).strip()
        if not query:
            continue
        token = query.lower()
        if token in seen:
            continue
        seen.add(token)
        ordered.append(query)
    return tuple(ordered)


def _bounded_query(query: str, *, max_fragments: int) -> str:
    """Return one query truncated to a bounded number of high-signal fragments."""

    fragments = re.findall(r'"[^"]+"|[A-Za-z0-9][A-Za-z0-9\-]+', str(query or ""))
    ordered: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        text = fragment.strip()
        if not text:
            continue
        token = text.lower()
        if token in seen:
            continue
        seen.add(token)
        ordered.append(text)
        if len(ordered) >= max(1, int(max_fragments)):
            break
    return " ".join(ordered).strip()
