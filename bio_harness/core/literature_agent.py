"""Domain-locked literature research helpers for bioinformatics decisions."""

from __future__ import annotations

import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any
from urllib.parse import urlparse

from bio_harness.core.literature_backend_policy import (
    classify_backend_tier,
    decide_backend_usage,
    merge_backend_health_memory,
    snapshot_backend_status,
)
from bio_harness.core.literature_cache import LiteratureCache
from bio_harness.core.literature_query_builder import build_literature_search_queries
from bio_harness.core.literature_query_profiles import BackendQueryPlan, build_backend_query_plan
from bio_harness.core.literature_source_scoring import (
    classify_source_class,
    method_specificity_score,
    source_quality_score,
    summarize_evidence,
)

ALLOWED_DOMAINS = (
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "doi.org",
    "academic.oup.com",
    "genome.cshlp.org",
    "genomebiology.biomedcentral.com",
    "nature.com",
    "science.org",
    "cell.com",
    "biorxiv.org",
    "medrxiv.org",
    "bioconductor.org",
    "bioinformatics.org",
    "ensembl.org",
    "uniprot.org",
)

_SOURCE_SCORE = {"pubmed": 3.0, "semantic_scholar": 2.0, "web": 1.0}
_METHOD_STOPWORDS = {
    "The",
    "And",
    "For",
    "With",
    "This",
    "That",
    "Our",
    "RNA",
    "Seq",
    "Rna",
    "Analysis",
    "Study",
    "Methods",
    "Method",
    "Data",
    "Results",
    "Using",
}
_KNOWN_METHOD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DESeq2", re.compile(r"\bdeseq2\b", re.IGNORECASE)),
    ("edgeR", re.compile(r"\bedger\b", re.IGNORECASE)),
    ("STAR", re.compile(r"\bstar\b", re.IGNORECASE)),
    ("HISAT2", re.compile(r"\bhisat2\b", re.IGNORECASE)),
    ("Salmon", re.compile(r"\bsalmon\b", re.IGNORECASE)),
    ("Kallisto", re.compile(r"\bkallisto\b", re.IGNORECASE)),
    ("ModelFinder", re.compile(r"\bmodelfinder\b", re.IGNORECASE)),
    ("IQ-TREE", re.compile(r"\biq-?tree\b", re.IGNORECASE)),
    ("RAxML", re.compile(r"\braxml\b", re.IGNORECASE)),
    ("GTR", re.compile(r"\bgtr\b", re.IGNORECASE)),
    ("fastp", re.compile(r"\bfastp\b", re.IGNORECASE)),
    ("Trim Galore", re.compile(r"\btrim[ -]?galore\b", re.IGNORECASE)),
    ("ComBat", re.compile(r"\bcombat\b", re.IGNORECASE)),
    ("SVA", re.compile(r"\bsva\b", re.IGNORECASE)),
    ("Medaka", re.compile(r"\bmedaka\b", re.IGNORECASE)),
    ("Canu", re.compile(r"\bcanu\b", re.IGNORECASE)),
    ("NECAT", re.compile(r"\bnecat\b", re.IGNORECASE)),
    ("Illumina polishing", re.compile(r"\billumina\b.*\bpolish", re.IGNORECASE)),
    ("Dorado", re.compile(r"\bdorado\b", re.IGNORECASE)),
    ("Pilon", re.compile(r"\bpilon\b", re.IGNORECASE)),
    ("Nanopolish", re.compile(r"\bnanopolish\b", re.IGNORECASE)),
    ("Unicycler", re.compile(r"\bunicycler\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class LiteratureHit:
    """One literature search result."""

    title: str
    abstract: str
    source: str
    url: str
    year: int | None
    citation_count: int
    relevance_score: float
    source_quality_score: float = 0.0
    method_specificity_score: float = 0.0
    source_class: str = ""


@dataclass(frozen=True)
class ResearchQuery:
    """Structured research query for the literature agent."""

    question: str
    analysis_type: str
    tools_in_use: tuple[str, ...] = ()
    organism: str = ""
    max_results: int = 10


@dataclass(frozen=True)
class ResearchReport:
    """Structured output from one literature research session."""

    query: ResearchQuery
    hits: tuple[LiteratureHit, ...] = ()
    synthesis: str = ""
    recommendations: tuple[str, ...] = ()
    parameter_suggestions: tuple[tuple[str, str, str], ...] = ()
    confidence: float = 0.0
    sources_consulted: int = 0
    evidence_sufficiency: str = "insufficient"
    evidence_failure_reasons: tuple[str, ...] = ()
    primary_literature_count: int = 0
    trusted_web_count: int = 0
    unique_source_count: int = 0
    backend_diversity_count: int = 0
    backend_statuses: tuple["ResearchBackendStatus", ...] = ()
    backend_health_summary: tuple["ResearchBackendHealthSummary", ...] = ()


@dataclass(frozen=True)
class ResearchBackendStatus:
    """Aggregate status for one search backend over one research session."""

    backend: str
    status: str
    queries_attempted: int = 0
    queries_succeeded: int = 0
    hit_count: int = 0
    timeout_count: int = 0
    error_count: int = 0
    total_duration_seconds: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class ResearchBackendHealthSummary:
    """One backend health summary row for a completed research session."""

    backend: str
    tier: str
    reason: str = ""


@dataclass(frozen=True)
class _TimedCallResult:
    """Bounded result wrapper for one backend invocation."""

    status: str
    payload: Any = None
    detail: str = ""
    duration_seconds: float = 0.0


_BACKEND_TIMEOUT_SECONDS = {
    "pubmed": 20.0,
    "semantic_scholar": 12.0,
    "web": 8.0,
    "synthesis": 20.0,
}
_RESEARCH_TOTAL_TIMEOUT_SECONDS = 45.0
_PROTOCOL_EVIDENCE_MARKERS = (
    "workflow",
    "protocol",
    "preprocessing",
    "recommended",
    "standard",
    "steps",
    "best practice",
    "peak calling",
    "quality control",
    "how to",
)


def _hit_identity(hit: LiteratureHit) -> tuple[str, str]:
    """Return a stable identity key for one normalized hit."""

    title_key = re.sub(r"\W+", "", hit.title.lower())
    return (title_key, hit.url.strip().lower())


def _should_preserve_protocol_evidence(query: ResearchQuery) -> bool:
    """Return whether one research question benefits from protocol docs."""

    question_text = f"{query.question} {' '.join(query.tools_in_use)}".lower()
    if any(marker in question_text for marker in _PROTOCOL_EVIDENCE_MARKERS):
        return True
    return bool(query.tools_in_use)


def _compose_evidence_hits(hits: list[LiteratureHit], query: ResearchQuery) -> list[LiteratureHit]:
    """Select a bounded evidence set while preserving protocol coverage.

    The final report should remain primarily ranked by literature relevance, but
    workflow-oriented questions benefit from at least one canonical protocol or
    package-document hit when such evidence is available. Without this step, a
    strong PubMed block can fill the ``max_results`` cap and erase all
    supplemental trusted-web evidence from the accepted set.

    Args:
        hits: Ranked normalized hits.
        query: Structured research query.

    Returns:
        Bounded final evidence set.
    """

    deduped = _deduplicate_hits(hits)
    if len(deduped) <= query.max_results:
        return deduped
    if query.max_results < 4 or not _should_preserve_protocol_evidence(query):
        return deduped[: query.max_results]

    protocol_hits = [hit for hit in deduped if hit.source_class == "trusted_protocol_doc"]
    if not protocol_hits:
        return deduped[: query.max_results]

    reserve_count = min(1, len(protocol_hits), max(0, query.max_results - 1))
    reserved = protocol_hits[:reserve_count]
    reserved_keys = {_hit_identity(hit) for hit in reserved}
    primary_selection: list[LiteratureHit] = []
    target_primary_count = max(0, query.max_results - reserve_count)
    for hit in deduped:
        if _hit_identity(hit) in reserved_keys:
            continue
        primary_selection.append(hit)
        if len(primary_selection) >= target_primary_count:
            break

    chosen = primary_selection + reserved
    order = {_hit_identity(hit): index for index, hit in enumerate(deduped)}
    return sorted(chosen, key=lambda hit: order.get(_hit_identity(hit), len(deduped)))


class LiteratureAgent:
    """PubMed-grounded research agent for bioinformatics decisions."""

    def __init__(
        self,
        librarian: Any | None = None,
        biollm: Any | None = None,
        allowed_domains: tuple[str, ...] = ALLOWED_DOMAINS,
        backend_timeout_seconds: dict[str, float] | None = None,
        total_timeout_seconds: float = _RESEARCH_TOTAL_TIMEOUT_SECONDS,
        literature_cache: LiteratureCache | None = None,
        use_backend_health_memory: bool = True,
    ) -> None:
        """Initialize one literature agent instance.

        Args:
            librarian: Optional librarian backend helper.
            biollm: Optional summarization model wrapper.
            allowed_domains: Allowed trusted domains for web-backed hits.
            backend_timeout_seconds: Optional per-backend timeout overrides.
            total_timeout_seconds: Overall wall-clock budget for one research session.
            literature_cache: Optional shared literature cache instance.
            use_backend_health_memory: Whether persisted backend health should
                influence in-run backend eligibility decisions.
        """

        self._librarian = librarian
        self._biollm = biollm
        self._allowed_domains = tuple(allowed_domains)
        self._cache: dict[str, ResearchReport] = {}
        self._backend_health: dict[str, dict[str, int]] = {}
        shared_cache = literature_cache
        if shared_cache is None and librarian is not None and hasattr(librarian, "literature_cache"):
            try:
                shared_cache = getattr(librarian, "literature_cache")
            except Exception:
                shared_cache = None
        if isinstance(shared_cache, LiteratureCache):
            self._literature_cache = shared_cache
        elif librarian is not None:
            self._literature_cache = LiteratureCache(
                response_cache_enabled=False,
                health_memory_enabled=False,
            )
        else:
            self._literature_cache = LiteratureCache()
        self._use_backend_health_memory = bool(use_backend_health_memory)
        self._backend_timeouts = dict(_BACKEND_TIMEOUT_SECONDS)
        if isinstance(backend_timeout_seconds, dict):
            for key, value in backend_timeout_seconds.items():
                try:
                    self._backend_timeouts[str(key)] = max(0.1, float(value))
                except (TypeError, ValueError):
                    continue
        self._total_timeout_seconds = max(1.0, float(total_timeout_seconds))

    def research(self, query: ResearchQuery) -> ResearchReport:
        """Execute a full literature research workflow."""

        cache_key = self._cache_key(query)
        if cache_key in self._cache:
            return self._cache[cache_key]

        query_plan = build_backend_query_plan(
            query.question,
            query.analysis_type,
            query.tools_in_use,
        )
        hits: list[LiteratureHit] = []
        backend_rollup = _initial_backend_rollup()
        timed_out = False
        started_at = time.monotonic()
        max_query_steps = max(
            len(query_plan.pubmed_queries),
            len(query_plan.web_queries),
            len(query_plan.citation_queries),
        )
        for query_index in range(max_query_steps):
            remaining = self._remaining_research_budget(started_at)
            if remaining <= 0:
                timed_out = True
                break
            policy_rollup = self._policy_rollup(backend_rollup)
            primary_hit_count = sum(1 for hit in hits if hit.source == "pubmed")
            pubmed_query = _query_for_backend(query_plan, "pubmed", query_index)
            pubmed_decision = decide_backend_usage(
                "pubmed",
                policy_rollup,
                current_hit_count=len(hits),
                primary_hit_count=primary_hit_count,
                query_available=bool(pubmed_query),
            )
            if pubmed_decision.eligible and pubmed_query:
                pubmed_hits, pubmed_status = self._run_backend_search(
                    "pubmed",
                    pubmed_query,
                    max_results=query.max_results,
                    timeout_seconds=min(self._backend_timeout("pubmed"), remaining),
                )
                hits.extend(pubmed_hits)
                _merge_backend_status(backend_rollup, pubmed_status)
                self._record_backend_health(pubmed_status)
            else:
                _merge_backend_status(
                    backend_rollup,
                    _skipped_backend_status("pubmed", detail=pubmed_decision.reason),
                )

            policy_rollup = self._policy_rollup(backend_rollup)
            primary_hit_count = sum(1 for hit in hits if hit.source == "pubmed")
            trusted_protocol_hit_count = sum(
                1 for hit in hits if hit.source_class == "trusted_protocol_doc"
            )
            web_query = _query_for_backend(query_plan, "web", query_index)
            web_decision = decide_backend_usage(
                "web",
                policy_rollup,
                current_hit_count=len(hits),
                primary_hit_count=primary_hit_count,
                query_available=bool(web_query),
                trusted_protocol_hit_count=trusted_protocol_hit_count,
                protocol_coverage_required=_should_preserve_protocol_evidence(query),
            )
            if web_decision.eligible and web_query:
                remaining = self._remaining_research_budget(started_at)
                if remaining <= 0:
                    timed_out = True
                    break
                web_hits, web_status = self._run_backend_search(
                    "web",
                    web_query,
                    max_results=query.max_results,
                    timeout_seconds=min(self._backend_timeout("web"), remaining),
                    allowed_domains=query_plan.web_domains,
                )
                hits.extend(web_hits)
                _merge_backend_status(backend_rollup, web_status)
                self._record_backend_health(web_status)
            else:
                _merge_backend_status(
                    backend_rollup,
                    _skipped_backend_status("web", detail=web_decision.reason),
                )

            policy_rollup = self._policy_rollup(backend_rollup)
            citation_query = _query_for_backend(query_plan, "semantic_scholar", query_index)
            citation_decision = decide_backend_usage(
                "semantic_scholar",
                policy_rollup,
                current_hit_count=len(hits),
                primary_hit_count=primary_hit_count,
                query_available=bool(citation_query),
            )
            if citation_decision.eligible and citation_query:
                remaining = self._remaining_research_budget(started_at)
                if remaining <= 0:
                    timed_out = True
                    break
                citation_hits, citation_status = self._run_backend_search(
                    "semantic_scholar",
                    citation_query,
                    max_results=query.max_results,
                    timeout_seconds=min(self._backend_timeout("semantic_scholar"), remaining),
                )
                hits.extend(citation_hits)
                _merge_backend_status(backend_rollup, citation_status)
                self._record_backend_health(citation_status)
            else:
                _merge_backend_status(
                    backend_rollup,
                    _skipped_backend_status("semantic_scholar", detail=citation_decision.reason),
                )

        ranked = _rank_hits(hits, query)
        deduped = _compose_evidence_hits(ranked, query)
        synthesis, recommendations = self._synthesize_findings_with_budget(
            deduped,
            query,
            started_at=started_at,
        )
        backend_statuses = _backend_statuses_from_rollup(backend_rollup)
        synthesis = _augment_synthesis_with_backend_status(
            synthesis,
            backend_statuses=backend_statuses,
            timed_out=timed_out,
        )
        recommendations = _augment_recommendations_with_backend_status(
            recommendations,
            backend_statuses=backend_statuses,
            hits_present=bool(deduped),
            timed_out=timed_out,
        )
        parameter_suggestions = _extract_parameter_suggestions(synthesis, query.tools_in_use)
        confidence = _compute_confidence(deduped, synthesis)
        evidence_summary = summarize_evidence(deduped, backend_statuses)
        backend_health_summary = _backend_health_summary_from_statuses(backend_statuses)
        report = ResearchReport(
            query=query,
            hits=tuple(deduped),
            synthesis=synthesis,
            recommendations=recommendations,
            parameter_suggestions=parameter_suggestions,
            confidence=confidence,
            sources_consulted=len(deduped),
            evidence_sufficiency=evidence_summary.evidence_sufficiency,
            evidence_failure_reasons=evidence_summary.failure_reasons,
            primary_literature_count=evidence_summary.primary_literature_count,
            trusted_web_count=evidence_summary.trusted_web_count,
            unique_source_count=evidence_summary.unique_source_count,
            backend_diversity_count=evidence_summary.backend_diversity_count,
            backend_statuses=backend_statuses,
            backend_health_summary=backend_health_summary,
        )
        self._cache[cache_key] = report
        return report

    def _record_backend_health(self, status: ResearchBackendStatus) -> None:
        """Update lightweight health tracking for flaky optional backends."""

        health = self._backend_health.setdefault(
            status.backend,
            {"timeout_count": 0, "error_count": 0},
        )
        health["timeout_count"] += int(status.timeout_count)
        health["error_count"] += int(status.error_count)
        self._literature_cache.record_backend_outcome(
            status.backend,
            status=status.status,
            detail=status.detail,
            hit_count=status.hit_count,
        )

    def _policy_rollup(
        self,
        backend_rollup: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return one backend rollup enriched with persisted health memory."""

        if not self._use_backend_health_memory:
            return backend_rollup
        return merge_backend_health_memory(
            backend_rollup,
            self._literature_cache.backend_health_context(),
        )

    def _backend_timeout(self, backend_name: str) -> float:
        """Return the configured timeout for one backend."""

        return max(0.1, float(self._backend_timeouts.get(backend_name, 10.0)))

    def _remaining_research_budget(self, started_at: float) -> float:
        """Return the remaining wall-clock budget for the research call."""

        return max(0.0, self._total_timeout_seconds - (time.monotonic() - started_at))

    def _synthesize_findings_with_budget(
        self,
        hits: list[LiteratureHit],
        query: ResearchQuery,
        *,
        started_at: float,
    ) -> tuple[str, tuple[str, ...]]:
        """Synthesize findings within the remaining overall research budget."""

        remaining = self._remaining_research_budget(started_at)
        if remaining <= 0:
            return "Research timed out before synthesis could complete.", ()
        result = _call_with_timeout(
            _synthesize_findings,
            args=(hits, query, self._biollm),
            timeout_seconds=min(self._backend_timeout("synthesis"), remaining),
        )
        if result.status != "ok":
            return (
                "Research evidence was collected, but synthesis timed out before recommendations were generated.",
                (),
            )
        payload = result.payload
        if (
            isinstance(payload, tuple)
            and len(payload) == 2
            and isinstance(payload[0], str)
            and isinstance(payload[1], tuple)
        ):
            return payload
        return "Research evidence was collected, but synthesis returned an invalid payload.", ()

    def _run_backend_search(
        self,
        backend_name: str,
        query: str,
        *,
        max_results: int,
        timeout_seconds: float,
        allowed_domains: tuple[str, ...] | None = None,
    ) -> tuple[list[LiteratureHit], ResearchBackendStatus]:
        """Run one backend search with bounded wall-clock execution."""

        if backend_name == "pubmed":
            call = self._search_pubmed
        elif backend_name == "semantic_scholar":
            call = self._search_citations
        elif backend_name == "web":
            call = self._search_web
        else:
            return [], ResearchBackendStatus(
                backend=backend_name,
                status="skipped",
                detail="unsupported_backend",
            )

        result = _call_with_timeout(
            call,
            args=(query,),
            kwargs={
                "max_results": max_results,
                **({"allowed_domains": allowed_domains} if backend_name == "web" else {}),
            },
            timeout_seconds=timeout_seconds,
        )
        hits = _coerce_literature_hits(result.payload) if result.status == "ok" else []
        status = result.status
        detail = result.detail
        diagnostic = self._backend_diagnostic(backend_name)
        if status == "ok" and not hits and diagnostic["status"] in {"rate_limited", "error"}:
            status = "error"
            detail = diagnostic["detail"] or diagnostic["status"]
        elif status == "ok" and not hits:
            status = "empty"
        return hits, ResearchBackendStatus(
            backend=backend_name,
            status=status,
            queries_attempted=1,
            queries_succeeded=1 if result.status == "ok" else 0,
            hit_count=len(hits),
            timeout_count=1 if status == "timeout" else 0,
            error_count=1 if status == "error" else 0,
            total_duration_seconds=result.duration_seconds,
            detail=detail,
        )

    def research_protocol_choice(
        self,
        analysis_type: str,
        options: list[str],
        context: str = "",
    ) -> ResearchReport:
        """Research which protocol or tool family is best for an analysis."""

        option_text = ", ".join(options) if options else "best practices"
        question = f"What is the recommended protocol choice for {analysis_type}: {option_text}? {context}".strip()
        return self.research(
            ResearchQuery(
                question=question,
                analysis_type=analysis_type,
                tools_in_use=tuple(options),
            )
        )

    def research_parameter_recommendation(
        self,
        tool_name: str,
        parameter_name: str,
        context: str = "",
    ) -> ResearchReport:
        """Research recommended values for a specific tool parameter."""

        question = f"What is the recommended value for {parameter_name} in {tool_name}? {context}".strip()
        return self.research(
            ResearchQuery(
                question=question,
                analysis_type="parameter_recommendation",
                tools_in_use=(tool_name,),
            )
        )

    def research_error_context(
        self,
        error_text: str,
        tool_name: str = "",
    ) -> ResearchReport:
        """Research literature and trusted references for error context."""

        question = f"What causes this {tool_name} error and how is it resolved? {error_text}".strip()
        tools = (tool_name,) if tool_name else ()
        return self.research(
            ResearchQuery(
                question=question,
                analysis_type="error_context",
                tools_in_use=tools,
            )
        )

    def research_method_validation(
        self,
        method_description: str,
        analysis_type: str = "",
    ) -> ResearchReport:
        """Research whether a method or approach is validated in literature."""

        question = f"Is this method supported in the literature: {method_description}".strip()
        return self.research(
            ResearchQuery(
                question=question,
                analysis_type=analysis_type or "method_validation",
            )
        )

    def _cache_key(self, query: ResearchQuery) -> str:
        return json.dumps(
            {
                "question": query.question.strip().lower(),
                "analysis_type": str(query.analysis_type or "").strip().lower(),
                "tools_in_use": [tool.lower() for tool in query.tools_in_use],
                "organism": str(query.organism or "").strip().lower(),
                "max_results": int(query.max_results),
            },
            sort_keys=True,
        )

    def _search_pubmed(self, query: str, *, max_results: int) -> list[LiteratureHit]:
        if self._librarian is not None and hasattr(self._librarian, "pubmed_search"):
            try:
                raw_hits = self._librarian.pubmed_search(query, max_results=max_results) or []
            except Exception:
                raw_hits = []
        elif self._librarian is not None and hasattr(self._librarian, "search"):
            try:
                raw_hits = self._librarian.search(query, max_results=max_results) or []
            except Exception:
                raw_hits = []
        else:
            raw_hits = _pubmed_search_http(query, max_results=max_results)
        hits: list[LiteratureHit] = []
        for raw in raw_hits:
            title = str(raw.get("title", "") or "").strip()
            if not title:
                continue
            pmid = str(raw.get("pmid", "") or "").strip()
            url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "https://pubmed.ncbi.nlm.nih.gov/"
            hits.append(
                LiteratureHit(
                    title=title,
                    abstract=str(raw.get("abstract", "") or "").strip(),
                    source="pubmed",
                    url=url,
                    year=_safe_int(raw.get("year")),
                    citation_count=_safe_int(raw.get("citation_count")),
                    relevance_score=0.0,
                )
            )
        return hits

    def _search_citations(self, query: str, *, max_results: int) -> list[LiteratureHit]:
        if self._librarian is None or not hasattr(self._librarian, "citation_search"):
            return []
        try:
            raw_hits = self._librarian.citation_search(query, max_results=max_results) or []
        except Exception:
            return []
        hits: list[LiteratureHit] = []
        for raw in raw_hits:
            url = str(raw.get("url", "") or "").strip()
            if url and not _host_allowed(url, self._allowed_domains):
                continue
            hits.append(
                LiteratureHit(
                    title=str(raw.get("title", "") or "").strip(),
                    abstract=str(raw.get("abstract", "") or "").strip(),
                    source="semantic_scholar",
                    url=url,
                    year=_safe_int(raw.get("year")),
                    citation_count=_safe_int(raw.get("citations") or raw.get("citation_count")),
                    relevance_score=0.0,
                )
            )
        return [hit for hit in hits if hit.title]

    def _search_web(
        self,
        query: str,
        *,
        max_results: int,
        allowed_domains: tuple[str, ...] | None = None,
    ) -> list[LiteratureHit]:
        if self._librarian is None or not hasattr(self._librarian, "web_search"):
            return []
        try:
            raw_hits = self._librarian.web_search(
                query,
                max_results=max_results,
                allowed_domains=list(allowed_domains or self._allowed_domains),
            ) or []
        except Exception:
            return []
        hits: list[LiteratureHit] = []
        for raw in raw_hits:
            url = str(raw.get("href", "") or raw.get("url", "") or "").strip()
            if not url or not _host_allowed(url, self._allowed_domains):
                continue
            hits.append(
                LiteratureHit(
                    title=str(raw.get("title", "") or "").strip(),
                    abstract=str(raw.get("body", "") or raw.get("abstract", "") or "").strip(),
                    source="web",
                    url=url,
                    year=_safe_int(raw.get("year")),
                    citation_count=_safe_int(raw.get("citation_count")),
                    relevance_score=0.0,
                )
            )
        return [hit for hit in hits if hit.title]

    def _backend_diagnostic(self, backend_name: str) -> dict[str, str]:
        """Return the most recent diagnostic payload for one backend."""

        if self._librarian is None or not hasattr(self._librarian, "last_backend_diagnostic"):
            return {"status": "", "detail": ""}
        try:
            payload = self._librarian.last_backend_diagnostic(backend_name)
        except Exception:
            return {"status": "", "detail": ""}
        if not isinstance(payload, dict):
            return {"status": "", "detail": ""}
        return {
            "status": str(payload.get("status", "") or ""),
            "detail": str(payload.get("detail", "") or ""),
        }


def _generate_search_queries(question: str, analysis_type: str, tools: tuple[str, ...]) -> list[str]:
    """Generate multiple literature search queries deterministically."""

    return list(build_literature_search_queries(question, analysis_type, tools))


def _query_for_backend(
    query_plan: BackendQueryPlan,
    backend: str,
    query_index: int,
) -> str:
    """Return the backend-specific query at one step index."""

    if backend == "pubmed":
        queries = query_plan.pubmed_queries
    elif backend == "web":
        queries = query_plan.web_queries
    elif backend == "semantic_scholar":
        queries = query_plan.citation_queries
    else:
        return ""
    if query_index < 0 or query_index >= len(queries):
        return ""
    return str(queries[query_index] or "").strip()


def _initial_backend_rollup() -> dict[str, dict[str, Any]]:
    """Return one mutable rollup container for backend telemetry."""

    return {
        backend: {
            "backend": backend,
            "status": "skipped",
            "queries_attempted": 0,
            "queries_succeeded": 0,
            "hit_count": 0,
            "timeout_count": 0,
            "error_count": 0,
            "total_duration_seconds": 0.0,
            "detail": "",
        }
        for backend in ("pubmed", "semantic_scholar", "web")
    }


def _skipped_backend_status(backend_name: str, *, detail: str) -> ResearchBackendStatus:
    """Build one skipped-backend status record."""

    return ResearchBackendStatus(
        backend=backend_name,
        status="skipped",
        detail=str(detail or "").strip(),
    )


def _merge_backend_status(
    rollup: dict[str, dict[str, Any]],
    status: ResearchBackendStatus,
) -> None:
    """Merge one backend status update into the session rollup."""

    target = rollup.setdefault(
        status.backend,
        {
            "backend": status.backend,
            "status": "skipped",
            "queries_attempted": 0,
            "queries_succeeded": 0,
            "hit_count": 0,
            "timeout_count": 0,
            "error_count": 0,
            "total_duration_seconds": 0.0,
            "detail": "",
        },
    )
    target["queries_attempted"] += int(status.queries_attempted)
    target["queries_succeeded"] += int(status.queries_succeeded)
    target["hit_count"] += int(status.hit_count)
    target["timeout_count"] += int(status.timeout_count)
    target["error_count"] += int(status.error_count)
    target["total_duration_seconds"] += float(status.total_duration_seconds)
    if status.detail and (
        status.status not in {"skipped"}
        or _rollup_status(target) == "skipped"
    ):
        target["detail"] = status.detail
    target["status"] = _rollup_status(target)


def _rollup_status(payload: dict[str, Any]) -> str:
    """Return the aggregate status label for one backend rollup row."""

    if int(payload.get("timeout_count", 0) or 0) > 0:
        return "timeout"
    if int(payload.get("error_count", 0) or 0) > 0:
        return "error"
    if int(payload.get("hit_count", 0) or 0) > 0:
        return "ok"
    if int(payload.get("queries_succeeded", 0) or 0) > 0:
        return "empty"
    if int(payload.get("queries_attempted", 0) or 0) > 0:
        return "error"
    return "skipped"


def _backend_statuses_from_rollup(
    rollup: dict[str, dict[str, Any]],
) -> tuple[ResearchBackendStatus, ...]:
    """Freeze backend telemetry into a stable tuple of dataclasses."""

    rows: list[ResearchBackendStatus] = []
    for backend in ("pubmed", "semantic_scholar", "web"):
        payload = rollup.get(backend, {})
        rows.append(
            ResearchBackendStatus(
                backend=backend,
                status=str(payload.get("status", "skipped") or "skipped"),
                queries_attempted=int(payload.get("queries_attempted", 0) or 0),
                queries_succeeded=int(payload.get("queries_succeeded", 0) or 0),
                hit_count=int(payload.get("hit_count", 0) or 0),
                timeout_count=int(payload.get("timeout_count", 0) or 0),
                error_count=int(payload.get("error_count", 0) or 0),
                total_duration_seconds=round(float(payload.get("total_duration_seconds", 0.0) or 0.0), 3),
                detail=str(payload.get("detail", "") or ""),
            )
        )
    return tuple(rows)


def _call_with_timeout(
    func: Any,
    *,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> _TimedCallResult:
    """Run one callable on a daemon thread with a hard caller-side timeout."""

    queue: Queue[tuple[str, Any]] = Queue(maxsize=1)
    start = time.monotonic()
    call_kwargs = dict(kwargs or {})

    def _runner() -> None:
        try:
            queue.put(("ok", func(*args, **call_kwargs)))
        except Exception as exc:  # pragma: no cover - narrow call sites are unit-tested
            queue.put(("error", exc))

    thread = threading.Thread(target=_runner, name="literature-backend", daemon=True)
    thread.start()
    try:
        status, payload = queue.get(timeout=max(0.1, float(timeout_seconds)))
    except Empty:
        return _TimedCallResult(
            status="timeout",
            detail=f"timed out after {float(timeout_seconds):.1f}s",
            duration_seconds=time.monotonic() - start,
        )
    if status == "error":
        return _TimedCallResult(
            status="error",
            detail=str(payload),
            duration_seconds=time.monotonic() - start,
        )
    return _TimedCallResult(
        status="ok",
        payload=payload,
        duration_seconds=time.monotonic() - start,
    )


def _coerce_literature_hits(payload: Any) -> list[LiteratureHit]:
    """Normalize one backend payload into a list of literature hits."""

    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, LiteratureHit)]


def _augment_synthesis_with_backend_status(
    synthesis: str,
    *,
    backend_statuses: tuple[ResearchBackendStatus, ...],
    timed_out: bool,
) -> str:
    """Append operational backend status to one synthesis when needed."""

    details = [
        f"{status.backend}={status.status}"
        for status in backend_statuses
        if status.status not in {"ok", "empty", "skipped"}
    ]
    if not details and not timed_out:
        return synthesis
    suffix_parts = []
    if timed_out:
        suffix_parts.append("overall research budget expired before all searches completed")
    if details:
        suffix_parts.append("backend status: " + ", ".join(details))
    suffix = " Operational note: " + "; ".join(suffix_parts) + "."
    return (synthesis or "").rstrip() + suffix


def _augment_recommendations_with_backend_status(
    recommendations: tuple[str, ...],
    *,
    backend_statuses: tuple[ResearchBackendStatus, ...],
    hits_present: bool,
    timed_out: bool,
) -> tuple[str, ...]:
    """Add one operational recommendation when backend failures reduced evidence."""

    if recommendations:
        return recommendations
    degraded = [
        status.backend
        for status in backend_statuses
        if status.status in {"timeout", "error"}
    ]
    if not degraded and not timed_out:
        return recommendations
    if hits_present:
        return (
            "Review backend availability before relying on low-coverage literature evidence.",
        )
    return (
        "No usable literature evidence was retrieved; verify PubMed and trusted-web backends before rerunning explicit research.",
    )


def _rank_hits(hits: list[LiteratureHit], query: ResearchQuery) -> list[LiteratureHit]:
    """Rank literature hits by relevance."""

    question_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", f"{query.question} {query.analysis_type}".lower())
        if len(token) > 2
    }
    current_year = time.gmtime().tm_year
    ranked: list[LiteratureHit] = []
    for hit in hits:
        text = f"{hit.title} {hit.abstract}".lower()
        overlap = sum(1 for token in question_tokens if token in text)
        recency_bonus = 0.0
        if hit.year is not None:
            age = max(0, current_year - hit.year)
            recency_bonus = max(0.0, 1.0 - (age / 10.0))
        citation_bonus = math.log1p(max(hit.citation_count, 0)) / 10.0
        source_class = classify_source_class(hit.source, hit.url)
        quality_score = source_quality_score(source_class, hit.citation_count)
        specificity_score = method_specificity_score(
            hit.title,
            hit.abstract,
            question=query.question,
            analysis_type=query.analysis_type,
            tools_in_use=query.tools_in_use,
        )
        source_bonus = quality_score * 0.18
        score = min(
            1.0,
            (overlap / max(len(question_tokens), 1))
            + recency_bonus * 0.2
            + citation_bonus
            + source_bonus
            + specificity_score * 0.25,
        )
        ranked.append(
            LiteratureHit(
                title=hit.title,
                abstract=hit.abstract,
                source=hit.source,
                url=hit.url,
                year=hit.year,
                citation_count=hit.citation_count,
                relevance_score=score,
                source_quality_score=quality_score,
                method_specificity_score=specificity_score,
                source_class=source_class,
            )
        )
    return sorted(
        ranked,
        key=lambda hit: (
            -hit.relevance_score,
            -(hit.year or 0),
            -hit.citation_count,
            hit.title.lower(),
        ),
    )


def _deduplicate_hits(hits: list[LiteratureHit]) -> list[LiteratureHit]:
    """Deduplicate literature hits by normalized title or URL."""

    deduped: list[LiteratureHit] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    for hit in hits:
        title_key = re.sub(r"\W+", "", hit.title.lower())
        url_key = hit.url.strip().lower()
        if (title_key and title_key in seen_titles) or (url_key and url_key in seen_urls):
            continue
        if title_key:
            seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        deduped.append(hit)
    return deduped


def _synthesize_findings(
    hits: list[LiteratureHit],
    query: ResearchQuery,
    biollm: Any,
) -> tuple[str, tuple[str, ...]]:
    """Synthesize findings into summary text and recommendations."""

    if not hits:
        return ("No literature hits were found for this question.", ())
    if biollm is not None:
        prompt = (
            "Summarize the literature findings in 3-5 sentences and end with short recommendation bullets.\n"
            f"Question: {query.question}\n"
            f"Analysis type: {query.analysis_type}\n"
            f"Tools: {', '.join(query.tools_in_use) or 'none'}"
        )
        input_text = "\n\n".join(
            f"Title: {hit.title}\nYear: {hit.year}\nCitations: {hit.citation_count}\nAbstract: {hit.abstract}"
            for hit in hits[:5]
        )
        try:
            synthesis = str(biollm.summarize_text(input_text, prompt)).strip()
        except Exception:
            synthesis = ""
        else:
            recommendations = tuple(
                line.strip("- ").strip()
                for line in synthesis.splitlines()
                if line.strip().startswith(("-", "*"))
            )
            return synthesis, recommendations

    top_hit = hits[0]
    support_sentences = _select_support_sentences(hits, query, limit=3)
    synthesis_parts = [
        (
            f"Found {len(hits)} literature hit(s). Top evidence includes {top_hit.title}"
            + (f" ({top_hit.year})" if top_hit.year else "")
            + f" from {top_hit.source}."
        )
    ]
    method_names = _mentionable_methods(hits)
    if method_names:
        synthesis_parts.append(
            f"Key methods discussed across the ranked evidence include {', '.join(method_names[:4])}."
        )
    if support_sentences:
        synthesis_parts.extend(support_sentences)
    conclusion = _question_sensitive_conclusion(query.question, support_sentences)
    if conclusion:
        synthesis_parts.append(conclusion)
    synthesis = " ".join(part.strip() for part in synthesis_parts if part.strip())
    recommendations = tuple(
        sentence.rstrip(".") + "." for sentence in support_sentences[:3]
    ) or tuple(f"Review: {hit.title}" for hit in hits[:3])
    return synthesis, recommendations


def _extract_parameter_suggestions(
    synthesis: str,
    tool_names: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    """Extract simple parameter suggestions from synthesis text."""

    suggestions: list[tuple[str, str, str]] = []
    text = str(synthesis or "")
    for tool_name in tool_names:
        pattern = re.compile(
            rf"{re.escape(tool_name)}.*?([A-Za-z0-9_./-]+)\s*=\s*([A-Za-z0-9_.%+-]+)",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            suggestions.append((tool_name, match.group(1), match.group(2)))
    return tuple(suggestions)


def _compute_confidence(hits: list[LiteratureHit], synthesis: str) -> float:
    """Compute a coarse confidence score from evidence quality."""

    if not hits:
        return 0.0
    top_hits = hits[:3]
    avg_relevance = sum(max(hit.relevance_score, 0.0) for hit in top_hits) / len(top_hits)
    avg_citations = sum(max(hit.citation_count, 0) for hit in top_hits) / len(top_hits)
    avg_quality = sum(max(hit.source_quality_score, 0.0) for hit in top_hits) / len(top_hits)
    avg_specificity = sum(max(hit.method_specificity_score, 0.0) for hit in top_hits) / len(top_hits)
    recency_bonus = sum(1 for hit in top_hits if hit.year and hit.year >= (time.gmtime().tm_year - 3)) / len(top_hits)
    base = min(1.0, 0.1 + (len(hits) / 15.0)) * max(0.15, avg_relevance)
    citation_bonus = min(0.25, math.log1p(avg_citations) / 24.0) * max(0.2, avg_relevance)
    synthesis_bonus = (0.08 if synthesis.strip() else 0.0) * max(0.2, avg_relevance)
    quality_bonus = avg_quality * 0.18 + avg_specificity * 0.18
    return round(min(1.0, base + citation_bonus + recency_bonus * 0.16 + synthesis_bonus + quality_bonus), 3)


def _backend_health_summary_from_statuses(
    backend_statuses: tuple[ResearchBackendStatus, ...],
) -> tuple[ResearchBackendHealthSummary, ...]:
    """Summarize final backend health tiers from backend status rows."""

    backend_rollup = {
        status.backend: {
            "queries_attempted": status.queries_attempted,
            "hit_count": status.hit_count,
            "timeout_count": status.timeout_count,
            "error_count": status.error_count,
            "detail": status.detail,
        }
        for status in backend_statuses
    }
    rows: list[ResearchBackendHealthSummary] = []
    for status in backend_statuses:
        if status.status == "skipped":
            if status.detail in {
                "primary_literature_present",
                "primary_hits_sufficient",
                "evidence_already_sufficient",
                "query_plan_exhausted",
            }:
                tier = "healthy"
            else:
                tier = "suppressed"
        else:
            snapshot = snapshot_backend_status(status.backend, backend_rollup)
            tier = classify_backend_tier(
                snapshot,
                optional_backend=status.backend in {"web", "semantic_scholar"},
            )
        rows.append(
            ResearchBackendHealthSummary(
                backend=status.backend,
                tier=tier,
                reason=str(status.detail or ""),
            )
        )
    return tuple(rows)


def _select_support_sentences(
    hits: list[LiteratureHit],
    query: ResearchQuery,
    *,
    limit: int,
) -> tuple[str, ...]:
    """Select the most query-relevant evidence sentences from ranked hits."""

    query_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", f"{query.question} {query.analysis_type}".lower())
        if len(token) > 2
    }
    scored: list[tuple[float, str]] = []
    for hit in hits[:5]:
        text = hit.abstract or hit.title
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            cleaned = sentence.strip()
            if len(cleaned) < 30:
                continue
            lowered = cleaned.lower()
            overlap = sum(1 for token in query_tokens if token in lowered)
            if overlap == 0:
                continue
            score = overlap + hit.relevance_score
            scored.append((score, cleaned))
    selected: list[str] = []
    seen: set[str] = set()
    for _score, sentence in sorted(scored, key=lambda item: (-item[0], item[1].lower())):
        key = re.sub(r"\W+", "", sentence.lower())
        if key in seen:
            continue
        seen.add(key)
        selected.append(sentence.rstrip(".") + ".")
        if len(selected) >= limit:
            break
    return tuple(selected)


def _mentionable_methods(hits: list[LiteratureHit]) -> tuple[str, ...]:
    """Extract likely tool or method names from the top ranked hits."""

    named_methods: list[str] = []
    combined_text = "\n".join(f"{hit.title}\n{hit.abstract}" for hit in hits[:5])
    for canonical_name, pattern in _KNOWN_METHOD_PATTERNS:
        if pattern.search(combined_text):
            named_methods.append(canonical_name)
    if named_methods:
        return tuple(named_methods[:6])

    counts: dict[str, int] = {}
    for hit in hits[:5]:
        text = f"{hit.title} {hit.abstract}"
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9+.-]{2,}\b", text):
            if token in _METHOD_STOPWORDS:
                continue
            if token.lower() in {"study", "results", "data", "analysis", "methods"}:
                continue
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
    return tuple(token for token, _count in ranked[:6])


def _question_sensitive_conclusion(
    question: str,
    support_sentences: tuple[str, ...],
) -> str:
    """Add a short deterministic conclusion for high-signal question forms."""

    lowered_question = str(question or "").lower()
    joined_support = " ".join(support_sentences).lower()
    if "always" in lowered_question and "necessary" in lowered_question:
        if "unnecessary" in joined_support or "more important" in joined_support or "soft-clipping" in joined_support:
            return "Overall, the evidence suggests this is not always necessary and depends on the aligner and library context."
    return ""


def _pubmed_search_http(
    query: str,
    max_results: int = 10,
    email: str = "bio_harness@localhost",
) -> list[dict[str, str]]:
    """Search PubMed via E-utilities HTTP without Biopython."""

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    try:
        search_params = urllib.parse.urlencode(
            {
                "db": "pubmed",
                "term": query,
                "retmax": str(max_results),
                "retmode": "json",
                "email": email,
            }
        )
        with urllib.request.urlopen(f"{base}/esearch.fcgi?{search_params}", timeout=15) as response:
            search_payload = json.loads(response.read())
        pmids = search_payload.get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []
        fetch_params = urllib.parse.urlencode(
            {
                "db": "pubmed",
                "id": ",".join(pmids),
                "rettype": "abstract",
                "retmode": "xml",
                "email": email,
            }
        )
        with urllib.request.urlopen(f"{base}/efetch.fcgi?{fetch_params}", timeout=15) as response:
            xml_payload = response.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError:
        return []

    results: list[dict[str, str]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _xml_text(article.find(".//PMID"))
        title = _xml_text(article.find(".//ArticleTitle"))
        abstract = " ".join(
            text.strip()
            for text in (_xml_text(node) for node in article.findall(".//AbstractText"))
            if text.strip()
        )
        year = _xml_text(article.find(".//PubDate/Year"))
        results.append(
            {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "year": year,
            }
        )
    return results


def _host_allowed(url: str, allowed_domains: tuple[str, ...]) -> bool:
    """Return whether a URL belongs to an allowed domain."""

    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def _xml_text(node: ET.Element | None) -> str:
    """Safely extract combined text from one XML element."""

    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _safe_int(value: Any) -> int:
    """Convert loose numeric values to int."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "ALLOWED_DOMAINS",
    "LiteratureAgent",
    "ResearchBackendStatus",
    "ResearchBackendHealthSummary",
    "LiteratureHit",
    "ResearchQuery",
    "ResearchReport",
]
