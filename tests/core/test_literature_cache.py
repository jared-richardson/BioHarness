from __future__ import annotations

import time
from pathlib import Path

from bio_harness.core.literature_cache import LiteratureCache


def test_response_cache_round_trip(tmp_path: Path) -> None:
    cache = LiteratureCache(cache_root=tmp_path / "cache")
    cache_key = cache.response_cache_key("pubmed", "RNA-seq DESeq2", max_results=5)

    assert cache.get_cached_response(cache_key) is None

    cache.put_cached_response(
        cache_key,
        backend="pubmed",
        payload=[{"title": "DESeq2 best practices", "abstract": "Use DESeq2."}],
    )
    cached = cache.get_cached_response(cache_key)

    assert cached is not None
    assert cached.backend == "pubmed"
    assert cached.payload[0]["title"] == "DESeq2 best practices"
    assert cache.summary()["response_cache_stats"]["hits"] == 1


def test_response_cache_expiry_evicts_entry(tmp_path: Path) -> None:
    cache = LiteratureCache(cache_root=tmp_path / "cache", response_ttl_seconds=1.0)
    cache_key = cache.response_cache_key("web", "ATAC-seq MACS2", max_results=3)
    cache.put_cached_response(cache_key, backend="web", payload=[{"title": "Protocol", "href": "https://nature.com/x"}])

    time.sleep(1.05)

    assert cache.get_cached_response(cache_key) is None


def test_backend_health_record_suppresses_optional_backend_after_rate_limit(tmp_path: Path) -> None:
    cache = LiteratureCache(cache_root=tmp_path / "cache")

    cache.record_backend_outcome(
        "semantic_scholar",
        status="error",
        detail="HTTP Error 429:",
        hit_count=0,
    )

    record = cache.backend_health_record("semantic_scholar")
    context = cache.backend_health_context()

    assert record.suppressed_until_epoch > time.time()
    assert context["semantic_scholar"]["suppressed_until_epoch"] > time.time()


def test_backend_health_record_resets_after_success(tmp_path: Path) -> None:
    cache = LiteratureCache(cache_root=tmp_path / "cache")
    cache.record_backend_outcome("semantic_scholar", status="timeout", detail="timed out after 12.0s", hit_count=0)

    assert cache.backend_health_record("semantic_scholar").suppressed_until_epoch > time.time()

    cache.record_backend_outcome("semantic_scholar", status="ok", detail="", hit_count=2)
    record = cache.backend_health_record("semantic_scholar")

    assert record.suppressed_until_epoch == 0.0
    assert record.consecutive_error_count == 0
    assert record.consecutive_timeout_count == 0
