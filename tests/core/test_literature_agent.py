"""Tests for the literature research agent."""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import URLError

from bio_harness.core.literature_cache import LiteratureCache
from bio_harness.core.literature_agent import (
    ALLOWED_DOMAINS,
    LiteratureAgent,
    ResearchBackendHealthSummary,
    ResearchBackendStatus,
    LiteratureHit,
    ResearchQuery,
    ResearchReport,
    _compute_confidence,
    _backend_health_summary_from_statuses,
    _backend_statuses_from_rollup,
    _compose_evidence_hits,
    _deduplicate_hits,
    _extract_parameter_suggestions,
    _generate_search_queries,
    _initial_backend_rollup,
    _merge_backend_status,
    _pubmed_search_http,
    _rank_hits,
)
from bio_harness.core.literature_query_builder import build_literature_search_queries


def test_literature_hit_frozen() -> None:
    hit = LiteratureHit("Title", "Abstract", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 10, 0.5)
    assert hit.title == "Title"


def test_research_query_defaults() -> None:
    query = ResearchQuery(question="What is best for RNA-seq?", analysis_type="rna_seq")
    assert query.tools_in_use == ()
    assert query.max_results == 10


def test_research_report_frozen() -> None:
    report = ResearchReport(query=ResearchQuery(question="Q", analysis_type="A"))
    assert report.sources_consulted == 0
    assert report.backend_statuses == ()
    assert report.evidence_failure_reasons == ()


def test_generate_search_queries_basic() -> None:
    queries = _generate_search_queries("best DE method", "rna_seq_de", ())
    assert any("bioinformatics" in query for query in queries)


def test_generate_search_queries_with_tools() -> None:
    queries = _generate_search_queries("best method", "rna_seq", ("deseq2", "edger"))
    assert any("deseq2 edger" in query for query in queries)


def test_generate_search_queries_expands_abbreviations() -> None:
    queries = _generate_search_queries("best DE workflow", "rna_seq", ())
    assert any("differential expression" in query for query in queries)


def test_build_literature_search_queries_is_method_aware_for_atac_seq() -> None:
    queries = build_literature_search_queries(
        "Based on published methods, what preprocessing and peak-calling steps are standard for ATAC-seq differential accessibility, including whether Tn5 shifting and MACS2 are commonly used?",
        "atac_seq",
        ("macs2",),
    )

    assert any("ATAC-seq" in query for query in queries)
    assert any("MACS2" in query for query in queries)
    assert any("Tn5" in query for query in queries)


def test_build_literature_search_queries_is_method_aware_for_direct_rna() -> None:
    queries = build_literature_search_queries(
        "What minimap2 preset is typically recommended for Oxford Nanopore direct RNA sequencing?",
        "literature_research",
        ("minimap2",),
    )

    assert any("direct RNA sequencing" in query for query in queries)
    assert any("Oxford Nanopore" in query for query in queries)
    assert any("minimap2" in query for query in queries)


def test_rank_hits_prefers_recent() -> None:
    query = ResearchQuery(question="RNA-seq normalization", analysis_type="rna_seq")
    old = LiteratureHit("Normalization methods", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2010, 50, 0.0)
    new = LiteratureHit("Normalization methods", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/2/", 2024, 50, 0.0)
    ranked = _rank_hits([old, new], query)
    assert ranked[0].year == 2024


def test_rank_hits_prefers_cited() -> None:
    query = ResearchQuery(question="RNA-seq normalization", analysis_type="rna_seq")
    low = LiteratureHit("Normalization methods", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 10, 0.0)
    high = LiteratureHit("Normalization methods", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/2/", 2024, 1000, 0.0)
    ranked = _rank_hits([low, high], query)
    assert ranked[0].citation_count == 1000


def test_rank_hits_prefers_pubmed_over_web() -> None:
    query = ResearchQuery(question="RNA-seq normalization", analysis_type="rna_seq")
    pubmed = LiteratureHit("Normalization methods", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 10, 0.0)
    web = LiteratureHit("Normalization methods", "", "web", "https://nature.com/test", 2024, 10, 0.0)
    ranked = _rank_hits([web, pubmed], query)
    assert ranked[0].source == "pubmed"
    assert ranked[0].source_quality_score >= ranked[1].source_quality_score


def test_rank_hits_keyword_overlap() -> None:
    query = ResearchQuery(question="ATAC-seq normalization", analysis_type="atac_seq")
    relevant = LiteratureHit("ATAC-seq normalization", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 10, 0.0)
    weak = LiteratureHit("Protein folding methods", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/2/", 2024, 10, 0.0)
    ranked = _rank_hits([weak, relevant], query)
    assert ranked[0].title == "ATAC-seq normalization"


def test_deduplicate_by_title() -> None:
    hit_a = LiteratureHit("Shared title", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 1, 0.1)
    hit_b = LiteratureHit("Shared title", "", "web", "https://nature.com/other", 2023, 1, 0.2)
    deduped = _deduplicate_hits([hit_a, hit_b])
    assert len(deduped) == 1


def test_deduplicate_preserves_order() -> None:
    hit_a = LiteratureHit("A", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 1, 0.9)
    hit_b = LiteratureHit("A", "", "web", "https://nature.com/a", 2024, 1, 0.8)
    deduped = _deduplicate_hits([hit_a, hit_b])
    assert deduped[0].url == hit_a.url


def test_compose_evidence_hits_preserves_trusted_protocol_doc_for_method_question() -> None:
    query = ResearchQuery(
        question="What are the standard preprocessing and peak-calling workflow steps for ATAC-seq?",
        analysis_type="atac_seq",
        max_results=4,
    )
    hits = [
        LiteratureHit(
            f"PubMed hit {index}",
            "ATAC-seq preprocessing and differential accessibility methods.",
            "pubmed",
            f"https://pubmed.ncbi.nlm.nih.gov/{index}/",
            2024 - index,
            10,
            0.9 - index * 0.01,
            source_quality_score=0.95,
            method_specificity_score=0.75,
            source_class="pubmed_article",
        )
        for index in range(1, 5)
    ]
    hits.append(
        LiteratureHit(
            "ATACseqQC Guide",
            "Protocol and workflow guidance for ATAC-seq preprocessing.",
            "web",
            "https://bioconductor.org/packages/release/bioc/vignettes/ATACseqQC/inst/doc/ATACseqQC.html",
            2024,
            0,
            0.5,
            source_quality_score=0.78,
            method_specificity_score=0.9,
            source_class="trusted_protocol_doc",
        )
    )

    composed = _compose_evidence_hits(hits, query)

    assert len(composed) == 4
    assert any(hit.source_class == "trusted_protocol_doc" for hit in composed)
    assert sum(1 for hit in composed if hit.source == "pubmed") == 3


def test_compose_evidence_hits_does_not_force_protocol_doc_for_non_method_question() -> None:
    query = ResearchQuery(
        question="What is the historical adoption of ATAC-seq in chromatin studies?",
        analysis_type="atac_seq",
        max_results=4,
    )
    hits = [
        LiteratureHit(
            f"PubMed hit {index}",
            "Historical review of ATAC-seq adoption.",
            "pubmed",
            f"https://pubmed.ncbi.nlm.nih.gov/{index}/",
            2024 - index,
            10,
            0.9 - index * 0.01,
            source_quality_score=0.95,
            method_specificity_score=0.6,
            source_class="pubmed_article",
        )
        for index in range(1, 5)
    ]
    hits.append(
        LiteratureHit(
            "ATACseqQC Guide",
            "Protocol and workflow guidance for ATAC-seq preprocessing.",
            "web",
            "https://bioconductor.org/packages/release/bioc/vignettes/ATACseqQC/inst/doc/ATACseqQC.html",
            2024,
            0,
            0.5,
            source_quality_score=0.78,
            method_specificity_score=0.9,
            source_class="trusted_protocol_doc",
        )
    )

    composed = _compose_evidence_hits(hits, query)

    assert len(composed) == 4
    assert all(hit.source == "pubmed" for hit in composed)


class _MockLLM:
    def summarize_text(self, text: str, instruction: str) -> str:
        return "Summary line.\n- Use deseq2\n- deseq2 alpha = 0.05"


def test_extract_parameter_suggestions_from_text() -> None:
    suggestions = _extract_parameter_suggestions("Use deseq2 alpha = 0.05", ("deseq2",))
    assert suggestions == (("deseq2", "alpha", "0.05"),)


def test_extract_parameter_no_match() -> None:
    suggestions = _extract_parameter_suggestions("No settings given.", ("deseq2",))
    assert suggestions == ()


def test_confidence_zero_hits() -> None:
    assert _compute_confidence([], "") == 0.0


def test_confidence_high_citations() -> None:
    hits = [
        LiteratureHit("A", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/1/", 2024, 500, 0.9),
        LiteratureHit("B", "", "pubmed", "https://pubmed.ncbi.nlm.nih.gov/2/", 2023, 400, 0.8),
    ]
    assert _compute_confidence(hits, "summary") > 0.5


class _MockLibrarian:
    def __init__(self) -> None:
        self.pubmed_calls = 0
        self.web_calls = 0

    def pubmed_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        self.pubmed_calls += 1
        return [
            {
                "pmid": "1",
                "title": "DESeq2 for RNA-seq",
                "abstract": "Use DESeq2 for small-sample RNA-seq studies.",
                "year": "2024",
            }
        ]

    def citation_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return [
            {
                "title": "RNA-seq best practices",
                "abstract": "Highly cited best-practice paper.",
                "year": 2022,
                "citations": 1200,
                "url": "https://doi.org/example-paper",
            }
        ]

    def web_search(self, query: str, max_results: int = 10, allowed_domains: list[str] | None = None):  # noqa: ARG002
        self.web_calls += 1
        allowed = allowed_domains or []
        hits = [
            {
                "title": "Allowed domain",
                "body": "A trusted note.",
                "href": "https://nature.com/article",
            },
            {
                "title": "Blocked domain",
                "body": "Should be filtered.",
                "href": "https://example.com/random",
            },
        ]
        return [hit for hit in hits if any(domain in hit["href"] for domain in allowed)]


class _SearchOnlyLibrarian:
    def search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return [
            {
                "pmid": "11",
                "title": "edgeR improves FDR control with two replicates",
                "abstract": "We recommend edgeR for experiments with only two replicates per condition because it better controls false discovery rate.",
                "year": 2021,
            }
        ][:max_results]


def test_research_protocol_choice_end_to_end() -> None:
    agent = LiteratureAgent(librarian=_MockLibrarian(), biollm=_MockLLM())
    report = agent.research_protocol_choice("rna_seq_de", ["deseq2", "edger"], context="small sample study")
    assert report.sources_consulted > 0
    assert report.synthesis
    assert "Use deseq2" in report.recommendations
    assert any(status.backend == "pubmed" for status in report.backend_statuses)


def test_research_error_context_end_to_end() -> None:
    agent = LiteratureAgent(librarian=_MockLibrarian(), biollm=None)
    report = agent.research_error_context("design matrix not full rank", tool_name="deseq2_run")
    assert report.hits
    assert report.confidence > 0.0


def test_allowed_domains_enforced() -> None:
    agent = LiteratureAgent(librarian=_MockLibrarian(), biollm=None, allowed_domains=("nature.com",))
    report = agent.research(ResearchQuery(question="rna-seq best practice", analysis_type="rna_seq"))
    assert all(any(domain in hit.url for domain in ("nature.com", "pubmed.ncbi.nlm.nih.gov", "doi.org")) for hit in report.hits)


def test_cache_prevents_duplicate_queries() -> None:
    librarian = _MockLibrarian()
    agent = LiteratureAgent(librarian=librarian, biollm=None)
    query = ResearchQuery(question="rna-seq best practice", analysis_type="rna_seq")
    expected_queries = len(_generate_search_queries(query.question, query.analysis_type, query.tools_in_use))
    first = agent.research(query)
    second = agent.research(query)
    assert first == second
    assert librarian.pubmed_calls == expected_queries


def test_search_fallback_without_pubmed_search() -> None:
    agent = LiteratureAgent(librarian=_SearchOnlyLibrarian(), biollm=None)
    report = agent.research(
        ResearchQuery(
            question="Should I use DESeq2 or edgeR with two replicates?",
            analysis_type="rna_seq_differential_expression",
        )
    )

    assert report.hits
    assert "edgeR" in report.synthesis
    assert any("edgeR" in recommendation for recommendation in report.recommendations)


def test_research_allows_none_analysis_type() -> None:
    agent = LiteratureAgent(librarian=_SearchOnlyLibrarian(), biollm=None)
    report = agent.research(ResearchQuery(question="Chocolate cake?", analysis_type=None))

    assert report.hits


class _HangingWebLibrarian(_MockLibrarian):
    def web_search(  # noqa: ARG002
        self,
        query: str,
        max_results: int = 10,
        allowed_domains: list[str] | None = None,
    ):
        import time

        time.sleep(0.2)
        return []


def test_research_records_backend_timeout_without_hanging() -> None:
    agent = LiteratureAgent(
        librarian=_HangingWebLibrarian(),
        biollm=None,
        backend_timeout_seconds={"web": 0.01, "pubmed": 0.05, "semantic_scholar": 0.05},
        total_timeout_seconds=0.2,
    )

    report = agent.research(ResearchQuery(question="rna-seq best practice", analysis_type="rna_seq"))

    web_status = next(status for status in report.backend_statuses if status.backend == "web")
    assert web_status.status == "timeout"
    assert "timeout" in report.synthesis.lower()
    assert report.recommendations


def test_research_report_accepts_backend_statuses() -> None:
    report = ResearchReport(
        query=ResearchQuery(question="Q", analysis_type="A"),
        evidence_sufficiency="sufficient",
        evidence_failure_reasons=(),
        primary_literature_count=1,
        trusted_web_count=0,
        unique_source_count=1,
        backend_diversity_count=1,
        backend_statuses=(
            ResearchBackendStatus(
                backend="pubmed",
                status="ok",
                queries_attempted=1,
                queries_succeeded=1,
            ),
        ),
        backend_health_summary=(
            ResearchBackendHealthSummary(
                backend="pubmed",
                tier="healthy",
                reason="primary_backend",
            ),
        ),
    )

    assert report.backend_statuses[0].backend == "pubmed"
    assert report.evidence_sufficiency == "sufficient"
    assert report.backend_health_summary[0].tier == "healthy"


def test_backend_health_summary_treats_skipped_optional_backend_as_healthy_when_not_needed() -> None:
    agent = LiteratureAgent(librarian=_MockLibrarian(), biollm=None)
    report = agent.research(
        ResearchQuery(
            question="What minimap2 preset is typically recommended for Oxford Nanopore direct RNA sequencing?",
            analysis_type="literature_research",
            tools_in_use=("minimap2",),
        )
    )

    web_summary = next(item for item in report.backend_health_summary if item.backend == "web")
    assert web_summary.tier == "healthy"


def test_skipped_reason_does_not_overwrite_prior_empty_backend_detail() -> None:
    rollup = _initial_backend_rollup()
    _merge_backend_status(
        rollup,
        ResearchBackendStatus(
            backend="web",
            status="empty",
            queries_attempted=2,
            queries_succeeded=2,
            hit_count=0,
            detail="",
        ),
    )
    _merge_backend_status(
        rollup,
        ResearchBackendStatus(
            backend="web",
            status="skipped",
            detail="primary_hits_sufficient",
        ),
    )

    backend_statuses = _backend_statuses_from_rollup(rollup)
    web_status = next(item for item in backend_statuses if item.backend == "web")
    web_summary = next(
        item for item in _backend_health_summary_from_statuses(backend_statuses) if item.backend == "web"
    )

    assert web_status.status == "empty"
    assert web_status.detail == ""
    assert web_summary.tier == "degraded"
    assert web_summary.reason == ""


class _CircuitBreakerLibrarian:
    def __init__(self) -> None:
        self.citation_calls = 0
        self.web_calls = 0

    def pubmed_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return []

    def citation_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        import time

        self.citation_calls += 1
        time.sleep(0.2)
        return []

    def web_search(self, query: str, max_results: int = 10, allowed_domains: list[str] | None = None):  # noqa: ARG002
        self.web_calls += 1
        return []

    def last_backend_diagnostic(self, backend: str) -> dict[str, str]:  # noqa: ARG002
        return {"status": "", "detail": ""}


def test_research_circuit_breaks_semantic_scholar_after_timeout() -> None:
    librarian = _CircuitBreakerLibrarian()
    agent = LiteratureAgent(
        librarian=librarian,
        biollm=None,
        backend_timeout_seconds={"semantic_scholar": 0.01, "pubmed": 0.05, "web": 0.05},
        total_timeout_seconds=0.6,
    )

    report = agent.research(
        ResearchQuery(
            question="What minimap2 preset is recommended for Oxford Nanopore direct RNA sequencing?",
            analysis_type="literature_research",
            tools_in_use=("minimap2",),
        )
    )

    semantic_status = next(status for status in report.backend_statuses if status.backend == "semantic_scholar")
    assert semantic_status.timeout_count == 1
    assert librarian.citation_calls == 1
    assert report.evidence_sufficiency == "backend_degraded"


class _DiagnosticAwareLibrarian:
    def __init__(self) -> None:
        self.citation_calls = 0

    def pubmed_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return []

    def citation_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        self.citation_calls += 1
        return []

    def web_search(self, query: str, max_results: int = 10, allowed_domains: list[str] | None = None):  # noqa: ARG002
        return []

    def last_backend_diagnostic(self, backend: str) -> dict[str, str]:
        if backend == "semantic_scholar":
            return {"status": "rate_limited", "detail": "HTTP Error 429"}
        return {"status": "", "detail": ""}


def test_research_uses_backend_diagnostics_to_suppress_rate_limited_semantic_scholar() -> None:
    librarian = _DiagnosticAwareLibrarian()
    agent = LiteratureAgent(
        librarian=librarian,
        biollm=None,
        backend_timeout_seconds={"semantic_scholar": 0.05, "pubmed": 0.05, "web": 0.05},
        total_timeout_seconds=0.5,
    )

    report = agent.research(
        ResearchQuery(
            question="What minimap2 preset is recommended for Oxford Nanopore direct RNA sequencing?",
            analysis_type="literature_research",
            tools_in_use=("minimap2",),
        )
    )

    semantic_status = next(status for status in report.backend_statuses if status.backend == "semantic_scholar")
    assert semantic_status.status == "error"
    assert semantic_status.error_count == 1
    assert "429" in semantic_status.detail
    assert librarian.citation_calls == 1
    assert "no_primary_literature" in report.evidence_failure_reasons


class _PersistentSuppressionLibrarian:
    def __init__(self) -> None:
        self.citation_calls = 0

    def pubmed_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        return []

    def citation_search(self, query: str, max_results: int = 10):  # noqa: ARG002
        self.citation_calls += 1
        return []

    def web_search(self, query: str, max_results: int = 10, allowed_domains: list[str] | None = None):  # noqa: ARG002
        return []

    def last_backend_diagnostic(self, backend: str) -> dict[str, str]:
        if backend == "semantic_scholar":
            return {"status": "rate_limited", "detail": "HTTP Error 429"}
        return {"status": "", "detail": ""}


def test_research_reuses_persisted_backend_health_to_skip_suppressed_optional_backend(tmp_path: Path) -> None:
    shared_cache = LiteratureCache(
        cache_root=tmp_path / "cache",
        response_cache_enabled=False,
        health_memory_enabled=True,
    )
    librarian = _PersistentSuppressionLibrarian()

    first_agent = LiteratureAgent(librarian=librarian, biollm=None, literature_cache=shared_cache)
    first_report = first_agent.research(
        ResearchQuery(
            question="What minimap2 preset is recommended for Oxford Nanopore direct RNA sequencing?",
            analysis_type="literature_research",
            tools_in_use=("minimap2",),
        )
    )
    second_agent = LiteratureAgent(librarian=librarian, biollm=None, literature_cache=shared_cache)
    second_report = second_agent.research(
        ResearchQuery(
            question="What minimap2 preset is recommended for Oxford Nanopore direct RNA sequencing in published methods?",
            analysis_type="literature_research",
            tools_in_use=("minimap2",),
        )
    )

    first_semantic = next(item for item in first_report.backend_statuses if item.backend == "semantic_scholar")
    second_semantic = next(item for item in second_report.backend_statuses if item.backend == "semantic_scholar")

    assert first_semantic.status == "error"
    assert second_semantic.status == "skipped"
    assert second_semantic.detail == "backend_suppressed"
    assert librarian.citation_calls == 1


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        self.close()
        return False


def test_pubmed_search_http_parses_xml(monkeypatch) -> None:
    search_json = json.dumps({"esearchresult": {"idlist": ["1"]}}).encode()
    fetch_xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID><Article><ArticleTitle>Test title</ArticleTitle><Abstract><AbstractText>Test abstract</AbstractText></Abstract></Article><DateCreated><Year>2024</Year></DateCreated></MedlineCitation><PubmedData><History></History></PubmedData></PubmedArticle></PubmedArticleSet>"""

    responses = [_FakeResponse(search_json), _FakeResponse(fetch_xml)]

    def _urlopen(url: str, timeout: int = 15):  # noqa: ARG001
        return responses.pop(0)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    results = _pubmed_search_http("rna-seq")
    assert results[0]["title"] == "Test title"


def test_pubmed_search_http_empty_results(monkeypatch) -> None:
    search_json = json.dumps({"esearchresult": {"idlist": []}}).encode()

    def _urlopen(url: str, timeout: int = 15):  # noqa: ARG001
        return _FakeResponse(search_json)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    assert _pubmed_search_http("rna-seq") == []


def test_pubmed_search_http_timeout_handling(monkeypatch) -> None:
    def _urlopen(url: str, timeout: int = 15):  # noqa: ARG001
        raise URLError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    assert _pubmed_search_http("rna-seq") == []


def test_allowed_domains_tuple_not_empty() -> None:
    assert "pubmed.ncbi.nlm.nih.gov" in ALLOWED_DOMAINS
