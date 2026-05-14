"""Deterministic source-quality scoring for literature retrieval.

This module scores normalized literature hits without using an LLM. The goal is
to make evidence composition and sufficiency decisions auditable and stable
across benchmark runs.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import urlparse

_TRUSTED_PROTOCOL_HOSTS = (
    "bioconductor.org",
    "bioinformatics.org",
    "ensembl.org",
    "uniprot.org",
)
_COMMUNITY_SUPPORT_HOSTS = ("support.bioconductor.org",)
_SOURCE_REPOSITORY_HOSTS = ("code.bioconductor.org",)
_JOURNAL_HOSTS = (
    "nature.com",
    "science.org",
    "cell.com",
    "academic.oup.com",
    "genomebiology.biomedcentral.com",
    "genome.cshlp.org",
)
_PREPRINT_HOSTS = ("biorxiv.org", "medrxiv.org")
_ACTIONABLE_METHOD_TERMS = (
    "recommended",
    "workflow",
    "protocol",
    "preprocessing",
    "alignment",
    "preset",
    "peak calling",
    "differential",
    "quality control",
    "best practice",
)


@dataclass(frozen=True)
class EvidenceSummary:
    """Run-level evidence composition summary.

    Attributes:
        evidence_sufficiency: Deterministic evidence classification.
        failure_reasons: Stable list of failure reasons when evidence is not
            clearly sufficient.
        primary_literature_count: Count of literature-like sources.
        trusted_web_count: Count of trusted non-literature web sources.
        unique_source_count: Count of unique ranked hits.
        backend_diversity_count: Count of unique hit sources across backends.
        method_specific_hit_count: Count of hits with strong method specificity.
    """

    evidence_sufficiency: str
    failure_reasons: tuple[str, ...]
    primary_literature_count: int
    trusted_web_count: int
    unique_source_count: int
    backend_diversity_count: int
    method_specific_hit_count: int


def classify_source_class(source: str, url: str) -> str:
    """Classify one normalized hit into a deterministic source class.

    Args:
        source: Normalized backend source label.
        url: Source URL.

    Returns:
        Stable source-class label.
    """

    host = (urlparse(str(url or "")).hostname or "").lower()
    if str(source or "").lower() == "pubmed" or host.endswith("pubmed.ncbi.nlm.nih.gov"):
        return "pubmed_article"
    if host.endswith("pmc.ncbi.nlm.nih.gov"):
        return "pmc_article"
    if any(host == domain or host.endswith(f".{domain}") for domain in _COMMUNITY_SUPPORT_HOSTS):
        return "community_support_doc"
    if any(host == domain or host.endswith(f".{domain}") for domain in _SOURCE_REPOSITORY_HOSTS):
        return "source_repository_doc"
    if any(host == domain or host.endswith(f".{domain}") for domain in _TRUSTED_PROTOCOL_HOSTS):
        return "trusted_protocol_doc"
    if any(host == domain or host.endswith(f".{domain}") for domain in _PREPRINT_HOSTS):
        return "preprint_article"
    if any(host == domain or host.endswith(f".{domain}") for domain in _JOURNAL_HOSTS):
        return "journal_article"
    if str(source or "").lower() == "semantic_scholar":
        return "citation_index"
    return "other_web"


def source_quality_score(source_class: str, citation_count: int) -> float:
    """Return one deterministic source-quality score.

    Args:
        source_class: Stable source-class label.
        citation_count: Citation count when available.

    Returns:
        Source-quality score in ``[0.0, 1.0]``.
    """

    base = {
        "pubmed_article": 0.95,
        "pmc_article": 0.9,
        "journal_article": 0.82,
        "trusted_protocol_doc": 0.78,
        "source_repository_doc": 0.58,
        "community_support_doc": 0.44,
        "preprint_article": 0.62,
        "citation_index": 0.3,
        "other_web": 0.4,
    }.get(source_class, 0.35)
    citation_bonus = 0.0
    if citation_count > 0:
        citation_bonus = min(0.08, (len(str(int(citation_count))) - 1) * 0.02)
    return round(min(1.0, base + citation_bonus), 3)


def method_specificity_score(
    title: str,
    abstract: str,
    *,
    question: str,
    analysis_type: str,
    tools_in_use: tuple[str, ...],
) -> float:
    """Return one deterministic method-specificity score for a hit.

    Args:
        title: Hit title.
        abstract: Hit abstract or snippet text.
        question: Research question text.
        analysis_type: Analysis family label.
        tools_in_use: Tool names referenced by the run or request.

    Returns:
        Method-specificity score in ``[0.0, 1.0]``.
    """

    text = f"{title} {abstract}".lower()
    query_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9\\-]+", f"{question} {analysis_type}".lower())
        if len(token) > 2
    }
    if not text or not query_tokens:
        return 0.0
    overlap = sum(1 for token in query_tokens if token in text)
    overlap_score = min(0.45, overlap / max(len(query_tokens), 1))
    tool_matches = 0
    for tool in tools_in_use:
        tool_name = str(tool or "").strip().lower()
        if tool_name and tool_name in text:
            tool_matches += 1
    tool_score = min(0.25, 0.12 * tool_matches)
    actionable_score = 0.0
    for marker in _ACTIONABLE_METHOD_TERMS:
        if marker in text:
            actionable_score += 0.08
    return round(min(1.0, 0.12 + overlap_score + tool_score + min(0.3, actionable_score)), 3)


def summarize_evidence(
    hits: list[Any],
    backend_statuses: tuple[Any, ...],
) -> EvidenceSummary:
    """Summarize evidence composition and classify sufficiency.

    Args:
        hits: Ranked literature hits.
        backend_statuses: Final backend status records.

    Returns:
        Evidence composition summary.
    """

    primary_literature_count = 0
    trusted_web_count = 0
    backend_names: set[str] = set()
    method_specific_hit_count = 0
    for hit in hits:
        source_class = str(getattr(hit, "source_class", "") or "")
        if source_class in {"pubmed_article", "pmc_article", "journal_article", "preprint_article"}:
            primary_literature_count += 1
        elif source_class == "trusted_protocol_doc":
            trusted_web_count += 1
        source_name = str(getattr(hit, "source", "") or "")
        if source_name:
            backend_names.add(source_name)
        if float(getattr(hit, "method_specificity_score", 0.0) or 0.0) >= 0.45:
            method_specific_hit_count += 1

    unique_source_count = len(hits)
    backend_diversity_count = len(backend_names)
    degraded_backends = [
        status
        for status in backend_statuses
        if str(getattr(status, "status", "") or "") in {"timeout", "error"}
    ]
    failure_reasons: list[str] = []
    if primary_literature_count <= 0:
        failure_reasons.append("no_primary_literature")
    if primary_literature_count <= 0 and trusted_web_count > 0:
        failure_reasons.append("only_low_trust_web_hits")
    if method_specific_hit_count <= 0:
        failure_reasons.append("method_mentions_too_generic")
    if unique_source_count < 2 and primary_literature_count <= 0:
        failure_reasons.append("insufficient_cross_source_support")
    if degraded_backends and primary_literature_count <= 0:
        failure_reasons.append("backend_degradation_prevented_retrieval")

    if primary_literature_count >= 1 and method_specific_hit_count >= 1:
        evidence_sufficiency = "sufficient"
    elif primary_literature_count >= 1:
        evidence_sufficiency = "partial"
    elif degraded_backends:
        evidence_sufficiency = "backend_degraded"
    elif trusted_web_count >= 2 and method_specific_hit_count >= 1:
        evidence_sufficiency = "partial"
    else:
        evidence_sufficiency = "insufficient"

    return EvidenceSummary(
        evidence_sufficiency=evidence_sufficiency,
        failure_reasons=tuple(failure_reasons),
        primary_literature_count=primary_literature_count,
        trusted_web_count=trusted_web_count,
        unique_source_count=unique_source_count,
        backend_diversity_count=backend_diversity_count,
        method_specific_hit_count=method_specific_hit_count,
    )
