from __future__ import annotations

from bio_harness.core.literature_backend_policy import (
    classify_backend_tier,
    decide_backend_usage,
    merge_backend_health_memory,
    snapshot_backend_status,
)


def test_snapshot_backend_status_reads_rollup_payload() -> None:
    snapshot = snapshot_backend_status(
        "pubmed",
        {
            "pubmed": {
                "queries_attempted": 2,
                "hit_count": 3,
                "timeout_count": 0,
                "error_count": 1,
                "detail": "transient xml parse failure",
            }
        },
    )

    assert snapshot.backend == "pubmed"
    assert snapshot.queries_attempted == 2
    assert snapshot.hit_count == 3
    assert snapshot.error_count == 1


def test_classify_backend_tier_suppresses_optional_backend_after_repeated_empty_queries() -> None:
    snapshot = snapshot_backend_status(
        "semantic_scholar",
        {
            "semantic_scholar": {
                "queries_attempted": 2,
                "hit_count": 0,
                "timeout_count": 0,
                "error_count": 0,
                "detail": "",
            }
        },
    )

    assert classify_backend_tier(snapshot, optional_backend=True) == "degraded"


def test_decide_backend_usage_prefers_primary_literature_over_citation_enrichment() -> None:
    decision = decide_backend_usage(
        "semantic_scholar",
        {"semantic_scholar": {"queries_attempted": 0, "hit_count": 0}},
        current_hit_count=1,
        primary_hit_count=1,
        query_available=True,
    )

    assert not decision.eligible
    assert decision.reason == "primary_literature_present"


def test_decide_backend_usage_suppresses_pubmed_after_timeout() -> None:
    decision = decide_backend_usage(
        "pubmed",
        {
            "pubmed": {
                "queries_attempted": 1,
                "hit_count": 0,
                "timeout_count": 1,
                "error_count": 0,
                "detail": "timed out after 5.0s",
            }
        },
        current_hit_count=0,
        primary_hit_count=0,
        query_available=True,
    )

    assert not decision.eligible
    assert decision.tier == "suppressed"
    assert decision.reason == "backend_suppressed"


def test_decide_backend_usage_keeps_web_alive_until_protocol_gap_is_closed() -> None:
    decision = decide_backend_usage(
        "web",
        {"web": {"queries_attempted": 1, "hit_count": 8}},
        current_hit_count=8,
        primary_hit_count=4,
        query_available=True,
        trusted_protocol_hit_count=0,
        protocol_coverage_required=True,
    )

    assert decision.eligible
    assert decision.reason == "protocol_gap"


def test_merge_backend_health_memory_preserves_active_suppression() -> None:
    merged = merge_backend_health_memory(
        {"semantic_scholar": {"queries_attempted": 0, "hit_count": 0}},
        {
            "semantic_scholar": {
                "persistent_error_count": 1,
                "persistent_timeout_count": 0,
                "persistent_empty_count": 0,
                "suppressed_until_epoch": 9999999999.0,
                "detail": "HTTP Error 429:",
            }
        },
        now_epoch=1.0,
    )

    snapshot = snapshot_backend_status("semantic_scholar", merged)

    assert snapshot.suppressed_until_epoch == 9999999999.0
    assert snapshot.detail == "HTTP Error 429:"
    assert classify_backend_tier(snapshot, optional_backend=True) == "suppressed"
