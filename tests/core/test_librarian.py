from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from bio_harness.core.literature_cache import LiteratureCache
from bio_harness.tools.librarian import Librarian


def _uncached_librarian() -> Librarian:
    return Librarian(response_cache_enabled=False, health_memory_enabled=False)


def test_pubmed_search_retries_with_normalized_query_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    librarian = _uncached_librarian()
    seen_queries: list[str] = []

    def _fake_pubmed_search_once(self, query: str, max_results: int):  # noqa: ARG001
        seen_queries.append(query)
        if "published methods" in query.lower():
            return []
        return [{"title": "ATAC-seq peak calling", "abstract": "Use MACS2 with Tn5 shifting."}]

    monkeypatch.setattr(Librarian, "_pubmed_search_once", _fake_pubmed_search_once)

    results = librarian.pubmed_search(
        "ATAC-seq MACS2 Tn5 shifting differential accessibility published methods",
        max_results=5,
    )

    assert results
    assert len(seen_queries) >= 2
    assert "published methods" in seen_queries[0].lower()
    assert "published methods" not in seen_queries[1].lower()


def test_pubmed_search_retries_once_after_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    librarian = _uncached_librarian()
    attempts = {"count": 0}

    def _fake_pubmed_search_once(self, query: str, max_results: int):  # noqa: ARG001
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise HTTPError(
                url="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=None,
            )
        return [{"title": "direct RNA preset", "abstract": "Use the splice preset."}]

    monkeypatch.setattr(Librarian, "_pubmed_search_once", _fake_pubmed_search_once)
    monkeypatch.setattr("bio_harness.tools.librarian.time.sleep", lambda seconds: None)

    results = librarian.pubmed_search(
        "What minimap2 preset is recommended for Oxford Nanopore direct RNA sequencing?",
        max_results=5,
    )

    assert results
    assert attempts["count"] == 2


def test_pubmed_search_uses_response_cache_on_repeat(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    librarian = Librarian(
        literature_cache=LiteratureCache(
            cache_root=tmp_path / "cache",
            response_cache_enabled=True,
            health_memory_enabled=False,
        )
    )
    attempts = {"count": 0}

    def _fake_pubmed_search_once(self, query: str, max_results: int):  # noqa: ARG001
        attempts["count"] += 1
        return [{"title": "ATAC-seq peak calling", "abstract": "Use MACS2 with Tn5 shifting."}]

    monkeypatch.setattr(Librarian, "_pubmed_search_once", _fake_pubmed_search_once)

    first = librarian.pubmed_search("ATAC-seq MACS2 Tn5 shifting", max_results=5)
    second = librarian.pubmed_search("ATAC-seq MACS2 Tn5 shifting", max_results=5)

    assert first == second
    assert attempts["count"] == 1
    assert librarian.last_backend_diagnostic("pubmed")["cache_status"] == "hit"


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._buffer = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buffer.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None


def test_citation_search_uses_direct_http_and_filters_citations(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "data": [
            {
                "title": "High citation paper",
                "year": 2024,
                "citationCount": 120,
                "url": "https://www.semanticscholar.org/paper/high",
                "abstract": "High confidence benchmark paper.",
            },
            {
                "title": "Low citation paper",
                "year": 2023,
                "citationCount": 2,
                "url": "https://www.semanticscholar.org/paper/low",
                "abstract": "Should be filtered.",
            },
        ]
    }

    monkeypatch.setattr(
        "bio_harness.tools.librarian.urlopen",
        lambda request, timeout=8: _FakeResponse(payload),  # noqa: ARG005
    )

    librarian = _uncached_librarian()
    results = librarian.citation_search("RNA-seq DESeq2", min_citations=10, max_results=5)

    assert len(results) == 1
    assert results[0]["title"] == "High citation paper"
    assert results[0]["citations"] == 120


def test_citation_search_returns_empty_on_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_rate_limit(request, timeout=8):  # noqa: ARG001, ARG005
        raise HTTPError(
            url="https://api.semanticscholar.org/graph/v1/paper/search",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr("bio_harness.tools.librarian.urlopen", _raise_rate_limit)

    librarian = _uncached_librarian()
    assert librarian.citation_search("RNA-seq DESeq2", min_citations=0, max_results=5) == []


def test_web_search_uses_site_fallbacks_and_filters_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_queries: list[str] = []

    def _fake_html_search(self, query: str):
        seen_queries.append(query)
        if query.startswith("site:nature.com"):
            return [
                {
                    "title": "Nature protocol",
                    "href": "https://nature.com/articles/test",
                    "body": "Trusted protocol note.",
                },
                {
                    "title": "Blocked result",
                    "href": "https://example.com/random",
                    "body": "Should be filtered.",
                },
            ]
        return []

    monkeypatch.setattr(Librarian, "_duckduckgo_html_search", _fake_html_search)

    librarian = _uncached_librarian()
    results = librarian.web_search(
        "Oxford Nanopore direct RNA sequencing minimap2 preset",
        max_results=3,
        allowed_domains=["nature.com", "academic.oup.com"],
    )

    assert results == [
        {
            "title": "Nature protocol",
            "href": "https://nature.com/articles/test",
            "body": "Trusted protocol note.",
        }
    ]
    assert seen_queries[0] == "Oxford Nanopore direct RNA sequencing minimap2 preset"
    assert any(query.startswith("site:nature.com ") for query in seen_queries[1:])


def test_web_search_defaults_to_trusted_domains_when_none_supplied(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_queries: list[str] = []

    def _fake_html_search(self, query: str):
        seen_queries.append(query)
        if query.startswith("site:ncbi.nlm.nih.gov"):
            return [
                {
                    "title": "NCBI help",
                    "href": "https://ncbi.nlm.nih.gov/example",
                    "body": "Trusted reference.",
                }
            ]
        return []

    monkeypatch.setattr(Librarian, "_duckduckgo_html_search", _fake_html_search)

    librarian = _uncached_librarian()
    results = librarian.web_search("ATAC-seq MACS2 Tn5 shifting", max_results=2, allowed_domains=None)

    assert results
    assert results[0]["href"].startswith("https://ncbi.nlm.nih.gov/")
    assert any(query.startswith("site:ncbi.nlm.nih.gov ") for query in seen_queries[1:])


def test_normalize_web_query_strips_quotes_and_low_signal_phrases() -> None:
    librarian = _uncached_librarian()

    normalized = librarian._normalize_web_query(
        '"ATAC-seq" "chromatin accessibility" MACS2 "peak calling" preprocessing protocol peak calling workflow'
    )

    assert '"' not in normalized
    assert "ATAC-seq" in normalized
    assert "MACS2" in normalized
    assert "protocol" in normalized


def test_web_search_blocks_negative_control_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unexpected_html_search(self, query: str):  # noqa: ARG001
        raise AssertionError("negative control queries should not reach trusted-web search")

    monkeypatch.setattr(Librarian, "_duckduckgo_html_search", _unexpected_html_search)

    librarian = _uncached_librarian()
    results = librarian.web_search("fictional bioinformatics assay nonexistenttool benchmark sanity check")

    assert results == []
    assert librarian.last_backend_diagnostic("web")["detail"] == "negative_control_guard"


def test_duckduckgo_result_href_decodes_redirect_url() -> None:
    librarian = _uncached_librarian()

    decoded = librarian._duckduckgo_result_href(
        "//duckduckgo.com/l/?uddg=https%3A%2F%2Fnature.com%2Farticles%2Ftest&amp;rut=abc"
    )

    assert decoded == "https://nature.com/articles/test"
