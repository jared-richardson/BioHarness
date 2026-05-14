from __future__ import annotations

import re

from bio_harness.core.literature_query_profiles import build_backend_query_plan


def test_build_backend_query_plan_infers_direct_rna_parameter_profile() -> None:
    plan = build_backend_query_plan(
        "What minimap2 preset is typically recommended for Oxford Nanopore direct RNA sequencing in published methods?",
        "literature_research",
        ("minimap2",),
    )

    assert plan.assay_profile == "direct_rna"
    assert plan.intent_profile == "parameter"
    assert any("recommended preset" in query.lower() for query in plan.pubmed_queries)
    assert any("documentation" in query.lower() for query in plan.web_queries)
    assert plan.web_domains[0] == "academic.oup.com"
    assert all(len(re.findall(r'"[^"]+"|[A-Za-z0-9][A-Za-z0-9\\-]+', query)) <= 10 for query in plan.pubmed_queries)
    assert all(len(re.findall(r'"[^"]+"|[A-Za-z0-9][A-Za-z0-9\\-]+', query)) <= 9 for query in plan.web_queries)


def test_build_backend_query_plan_makes_citation_queries_shorter_than_pubmed_queries() -> None:
    plan = build_backend_query_plan(
        "Based on published methods, what are the standard preprocessing and peak-calling steps for ATAC-seq differential accessibility, including whether Tn5 shifting and MACS2 are commonly used?",
        "atac_seq",
        ("macs2",),
    )

    assert plan.assay_profile == "atac_seq"
    assert plan.intent_profile == "protocol"
    assert plan.citation_queries
    assert len(plan.citation_queries[0].split()) <= len(plan.pubmed_queries[0].split())
    assert any("ATACseqQC" in query for query in plan.web_queries)
    assert plan.web_domains[:3] == ("bioconductor.org", "pmc.ncbi.nlm.nih.gov", "nature.com")
