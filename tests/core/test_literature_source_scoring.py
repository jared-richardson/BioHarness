from __future__ import annotations

from bio_harness.core.literature_agent import LiteratureHit, ResearchBackendStatus
from bio_harness.core.literature_source_scoring import (
    classify_source_class,
    method_specificity_score,
    source_quality_score,
    summarize_evidence,
)


def test_classify_source_class_distinguishes_primary_and_protocol_sources() -> None:
    assert classify_source_class("pubmed", "https://pubmed.ncbi.nlm.nih.gov/123/") == "pubmed_article"
    assert classify_source_class("web", "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/") == "pmc_article"
    assert classify_source_class("web", "https://bioconductor.org/packages/release/bioc/html/DESeq2.html") == "trusted_protocol_doc"
    assert classify_source_class("web", "https://support.bioconductor.org/p/86594/") == "community_support_doc"
    assert classify_source_class("web", "https://code.bioconductor.org/browse/ATACseqQC/") == "source_repository_doc"


def test_source_quality_score_prefers_primary_literature() -> None:
    pubmed_score = source_quality_score("pubmed_article", 10)
    protocol_score = source_quality_score("trusted_protocol_doc", 0)

    assert pubmed_score > protocol_score


def test_method_specificity_score_rewards_actionable_tool_mentions() -> None:
    score = method_specificity_score(
        "Recommended minimap2 preset for Oxford Nanopore direct RNA sequencing",
        "Published methods recommend splice alignment presets for direct RNA workflows.",
        question="What minimap2 preset is recommended for Oxford Nanopore direct RNA sequencing?",
        analysis_type="literature_research",
        tools_in_use=("minimap2",),
    )

    assert score >= 0.45


def test_summarize_evidence_flags_only_web_hits_as_partial_or_worse() -> None:
    hits = [
        LiteratureHit(
            title="DESeq2 vignette",
            abstract="Recommended workflow and parameter settings.",
            source="web",
            url="https://bioconductor.org/packages/release/bioc/html/DESeq2.html",
            year=2024,
            citation_count=0,
            relevance_score=0.8,
            source_quality_score=0.78,
            method_specificity_score=0.8,
            source_class="trusted_protocol_doc",
        )
    ]
    summary = summarize_evidence(
        hits,
        (
            ResearchBackendStatus(backend="pubmed", status="empty", queries_attempted=1, queries_succeeded=1),
            ResearchBackendStatus(backend="web", status="ok", queries_attempted=1, queries_succeeded=1, hit_count=1),
        ),
    )

    assert summary.evidence_sufficiency in {"partial", "insufficient", "backend_degraded"}
    assert "no_primary_literature" in summary.failure_reasons
