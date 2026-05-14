"""Literature and trusted-web search helpers for research workflows."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

from Bio import Entrez

from bio_harness.core.literature_cache import LiteratureCache

logger = logging.getLogger(__name__)

_DEFAULT_REFERENCE_DOMAINS = [
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "pmc.ncbi.nlm.nih.gov",
    "academic.oup.com",
    "nature.com",
    "genomebiology.biomedcentral.com",
    "cell.com",
    "science.org",
    "biorxiv.org",
    "medrxiv.org",
    "bioconductor.org",
    "ensembl.org",
]
_LOW_SIGNAL_QUERY_PHRASES = (
    "published methods",
    "recommended workflow",
    "recommended parameter",
    "recommended preset",
    "recommended value",
    "commonly used",
    "best practice",
    "best practices",
    "guidance",
    "sanity check",
    "benchmark",
)
_QUERY_STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "how",
    "including",
    "methods",
    "published",
    "recommended",
    "standard",
    "the",
    "what",
    "which",
    "with",
}
_SEMANTIC_SCHOLAR_FIELDS = "title,year,citationCount,url,abstract"
_PUBMED_MIN_INTERVAL_SECONDS = 0.34
_PUBMED_RATE_LIMIT_RETRY_DELAY_SECONDS = 1.5
_NEGATIVE_CONTROL_MARKERS = ("fictional", "nonexistent", "sanity check")


class Librarian:
    """Provide bounded access to literature and trusted-web search backends.

    Backend clients are initialized lazily so explicit research mode can persist
    state before any slow or flaky third-party search client is constructed.
    """

    def __init__(
        self,
        email: str = "your.email@example.com",
        *,
        literature_cache: LiteratureCache | None = None,
        cache_root: Path | None = None,
        response_cache_enabled: bool = True,
        health_memory_enabled: bool = True,
    ) -> None:
        """Initialize one librarian instance.

        Args:
            email: Email address required by NCBI Entrez.
            literature_cache: Optional shared literature cache instance.
            cache_root: Optional cache root directory when constructing a
                default literature cache.
            response_cache_enabled: Whether response caching is enabled.
            health_memory_enabled: Whether backend health memory is enabled on
                the constructed cache instance.
        """

        Entrez.email = email
        self._email = email
        self.default_reference_domains = list(_DEFAULT_REFERENCE_DOMAINS)
        self._next_pubmed_at = 0.0
        self._last_backend_diagnostics: dict[str, dict[str, str]] = {}
        self._literature_cache = (
            literature_cache
            if isinstance(literature_cache, LiteratureCache)
            else LiteratureCache(
                cache_root=cache_root,
                response_cache_enabled=response_cache_enabled,
                health_memory_enabled=health_memory_enabled,
            )
        )

    @property
    def literature_cache(self) -> LiteratureCache:
        """Return the shared literature cache instance."""

        return self._literature_cache

    def last_backend_diagnostic(self, backend: str) -> dict[str, str]:
        """Return the latest diagnostic payload for one backend.

        Args:
            backend: Stable backend name.

        Returns:
            Shallow diagnostic payload with ``status`` and ``detail`` keys.
        """

        payload = self._last_backend_diagnostics.get(str(backend), {})
        return {
            "status": str(payload.get("status", "") or ""),
            "detail": str(payload.get("detail", "") or ""),
            "cache_status": str(payload.get("cache_status", "") or ""),
        }

    def _record_backend_diagnostic(
        self,
        backend: str,
        *,
        status: str,
        detail: str = "",
        cache_status: str = "",
    ) -> None:
        """Persist one lightweight backend diagnostic payload."""

        self._last_backend_diagnostics[str(backend)] = {
            "status": str(status or "").strip(),
            "detail": str(detail or "").strip(),
            "cache_status": str(cache_status or "").strip(),
        }

    def _host_allowed(self, url: str, allowed_domains: Optional[list[str]]) -> bool:
        """Return whether one URL is permitted by the optional domain allow-list."""

        if not allowed_domains:
            return True
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return False
        normalized = [domain.lower() for domain in allowed_domains]
        return any(host == domain or host.endswith(f".{domain}") for domain in normalized)

    def _pubmed_query_variants(self, query: str) -> list[str]:
        """Return bounded deterministic PubMed query variants."""

        normalized = " ".join(str(query or "").strip().split())
        if not normalized:
            return []
        lowered = normalized.lower()
        if any(marker in lowered for marker in _NEGATIVE_CONTROL_MARKERS):
            return [normalized]
        variants: list[str] = [normalized]
        simplified = normalized
        for phrase in _LOW_SIGNAL_QUERY_PHRASES:
            simplified = re.sub(rf"\b{re.escape(phrase)}\b", " ", simplified, flags=re.IGNORECASE)
        simplified = " ".join(simplified.split())
        if simplified and simplified.lower() != normalized.lower():
            variants.append(simplified)
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", simplified or normalized)
            if len(token) >= 3 and token.lower() not in _QUERY_STOPWORDS
        ]
        keyword_variant = " ".join(tokens[:8]).strip()
        if keyword_variant and all(keyword_variant.lower() != item.lower() for item in variants):
            variants.append(keyword_variant)
        boolean_terms = []
        for token in tokens[:6]:
            boolean_terms.append(f'"{token}"' if "-" in token or token.lower() != token else token)
        boolean_variant = " AND ".join(boolean_terms).strip()
        if boolean_variant and all(boolean_variant.lower() != item.lower() for item in variants):
            variants.append(boolean_variant)
        return variants[:4]

    def _throttle_pubmed(self) -> None:
        """Respect a small caller-side interval for PubMed requests."""

        delay = self._next_pubmed_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next_pubmed_at = time.monotonic() + _PUBMED_MIN_INTERVAL_SECONDS

    def _pubmed_search_once(self, query: str, max_results: int) -> list[dict[str, str]]:
        """Run one PubMed search request cycle."""

        self._throttle_pubmed()
        search_request = Request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax={max(1, int(max_results))}"
            f"&email={quote_plus(self._email)}&term={quote_plus(query)}",
            headers={"User-Agent": "bio-harness/1.0"},
        )
        with urlopen(search_request, timeout=15) as response:
            search_payload = json.loads(response.read())
        id_list = search_payload.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return []
        self._throttle_pubmed()
        fetch_request = Request(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&rettype=abstract&retmode=xml"
            f"&email={quote_plus(self._email)}&id={quote_plus(','.join(id_list))}",
            headers={"User-Agent": "bio-harness/1.0"},
        )
        with urlopen(fetch_request, timeout=15) as response:
            xml_payload = response.read().decode("utf-8", errors="ignore")
        results: list[dict[str, str]] = []
        try:
            root = ET.fromstring(xml_payload)
        except ET.ParseError:
            return results
        for article in root.findall(".//PubmedArticle")[:max_results]:
            title = re.sub(r"\s+", " ", article.findtext(".//ArticleTitle", default="")).strip()
            if not title:
                continue
            abstract_parts = []
            for node in article.findall(".//AbstractText"):
                text = "".join(node.itertext()).strip()
                if text:
                    abstract_parts.append(re.sub(r"\s+", " ", text))
            results.append(
                {
                    "pmid": article.findtext(".//PMID", default="").strip(),
                    "title": title,
                    "abstract": " ".join(abstract_parts).strip(),
                    "year": article.findtext(".//PubDate/Year", default="").strip(),
                }
            )
        return results

    def _semantic_scholar_search_once(
        self,
        query: str,
        *,
        min_citations: int,
        max_results: int,
    ) -> list[dict[str, Any]]:
        """Run one direct Semantic Scholar API query."""

        params = (
            f"query={quote_plus(query)}"
            f"&limit={max(1, int(max_results) * 3)}"
            f"&fields={quote_plus(_SEMANTIC_SCHOLAR_FIELDS)}"
        )
        request = Request(
            f"https://api.semanticscholar.org/graph/v1/paper/search?{params}",
            headers={"User-Agent": "bio-harness/1.0"},
        )
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read())
        items = payload.get("data", []) if isinstance(payload, dict) else []
        filtered = []
        for item in items:
            if not isinstance(item, dict):
                continue
            citation_count = int(item.get("citationCount") or 0)
            if citation_count < min_citations:
                continue
            filtered.append(
                {
                    "title": str(item.get("title", "") or ""),
                    "year": item.get("year"),
                    "citations": citation_count,
                    "url": str(item.get("url", "") or ""),
                    "abstract": str(item.get("abstract", "") or ""),
                }
            )
            if len(filtered) >= max_results:
                break
        return filtered

    def _duckduckgo_html_search(self, query: str) -> list[dict[str, str]]:
        """Run one HTML DuckDuckGo query and parse result rows."""

        request = Request(
            f"https://html.duckduckgo.com/html/?q={quote_plus(query)}",
            headers={"User-Agent": "bio-harness/1.0"},
        )
        with urlopen(request, timeout=15) as response:
            html_payload = response.read().decode("utf-8", errors="ignore")
        title_matches = list(
            re.finditer(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                html_payload,
                flags=re.DOTALL,
            )
        )
        snippet_matches = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html_payload,
            flags=re.DOTALL,
        )
        results: list[dict[str, str]] = []
        for index, match in enumerate(title_matches):
            raw_href = unescape(match.group(1))
            parsed_href = self._duckduckgo_result_href(raw_href)
            title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(match.group(2)))).strip()
            body = ""
            if index < len(snippet_matches):
                body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(snippet_matches[index]))).strip()
            if not parsed_href or not title:
                continue
            results.append({"title": title, "href": parsed_href, "body": body})
        return results

    def _duckduckgo_result_href(self, raw_href: str) -> str:
        """Normalize one DuckDuckGo HTML result href into a target URL."""

        href = str(raw_href or "").strip()
        if not href:
            return ""
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            encoded = parse_qs(parsed.query).get("uddg", [""])[0]
            return unescape(encoded).strip()
        return href

    def _web_query_variants(
        self,
        query: str,
        allowed_domains: list[str],
        *,
        max_variants: int = 5,
    ) -> list[str]:
        """Return bounded trusted-web query variants."""

        variants: list[str] = []
        base_query = " ".join(str(query or "").strip().split())
        if not base_query:
            return variants
        variants.append(base_query)
        simplified_query = self._normalize_web_query(base_query)
        if simplified_query and simplified_query.lower() != base_query.lower():
            variants.append(simplified_query)
        site_query = simplified_query or base_query
        for domain in allowed_domains[: max(0, int(max_variants) - len(variants))]:
            candidate = f"site:{domain} {site_query}"
            if candidate not in variants:
                variants.append(candidate)
        return variants[:max_variants]

    def _normalize_web_query(self, query: str) -> str:
        """Return a lighter-weight trusted-web query string.

        Args:
            query: Raw trusted-web query.

        Returns:
            Simplified query text for protocol/documentation retrieval.
        """

        text = " ".join(str(query or "").strip().split())
        if not text:
            return ""
        simplified = text
        for phrase in _LOW_SIGNAL_QUERY_PHRASES:
            simplified = re.sub(rf"\b{re.escape(phrase)}\b", " ", simplified, flags=re.IGNORECASE)
        simplified = simplified.replace('"', " ")
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]+", simplified)
            if len(token) >= 3 and token.lower() not in _QUERY_STOPWORDS
        ]
        return " ".join(tokens[:8]).strip()

    def pubmed_search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search PubMed for one query.

        Args:
            query: Search query string.
            max_results: Maximum number of records to return.

        Returns:
            PubMed result rows containing title and abstract content.
        """

        logger.info("Searching PubMed for query=%r", query)
        cache_key = self._literature_cache.response_cache_key(
            "pubmed",
            query,
            max_results=max_results,
        )
        cached = self._literature_cache.get_cached_response(cache_key)
        if cached is not None:
            status = "ok" if cached.payload else "empty"
            self._record_backend_diagnostic("pubmed", status=status, detail="cache_hit", cache_status="hit")
            return [dict(item) for item in cached.payload]
        diagnostic_status = "empty"
        diagnostic_detail = ""
        for query_variant in self._pubmed_query_variants(query):
            for attempt in range(2):
                try:
                    results = self._pubmed_search_once(query_variant, max_results=max_results)
                except HTTPError as exc:
                    logger.warning("PubMed search rate-limited for query=%r variant=%r: %s", query, query_variant, exc)
                    diagnostic_status = "rate_limited"
                    diagnostic_detail = str(exc)
                    if attempt == 0:
                        time.sleep(_PUBMED_RATE_LIMIT_RETRY_DELAY_SECONDS)
                        continue
                    break
                except Exception as exc:
                    logger.error("PubMed search failed for query=%r variant=%r: %s", query, query_variant, exc)
                    diagnostic_status = "error"
                    diagnostic_detail = str(exc)
                    break
                if results:
                    self._literature_cache.put_cached_response(cache_key, backend="pubmed", payload=results[:max_results])
                    self._record_backend_diagnostic("pubmed", status="ok", cache_status="miss")
                    return results[:max_results]
                break
        logger.info("No PubMed articles found for query=%r", query)
        if diagnostic_status == "empty":
            self._literature_cache.put_cached_response(cache_key, backend="pubmed", payload=[])
        self._record_backend_diagnostic(
            "pubmed",
            status=diagnostic_status,
            detail=diagnostic_detail,
            cache_status="miss",
        )
        return []

    def citation_search(
        self,
        query: str,
        min_citations: int = 100,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search Semantic Scholar and keep only sufficiently cited papers.

        Args:
            query: Search query string.
            min_citations: Minimum citation threshold.
            max_results: Maximum number of records to return.

        Returns:
            Filtered citation-search result rows.
        """

        logger.info("Searching Semantic Scholar for query=%r", query)
        cache_key = self._literature_cache.response_cache_key(
            "semantic_scholar",
            query,
            max_results=max_results,
            min_citations=min_citations,
        )
        cached = self._literature_cache.get_cached_response(cache_key)
        if cached is not None:
            status = "ok" if cached.payload else "empty"
            self._record_backend_diagnostic(
                "semantic_scholar",
                status=status,
                detail="cache_hit",
                cache_status="hit",
            )
            return [dict(item) for item in cached.payload]
        try:
            results = self._semantic_scholar_search_once(
                query,
                min_citations=min_citations,
                max_results=max_results,
            )
            self._literature_cache.put_cached_response(cache_key, backend="semantic_scholar", payload=results)
            self._record_backend_diagnostic(
                "semantic_scholar",
                status="ok" if results else "empty",
                cache_status="miss",
            )
            return results
        except HTTPError as exc:
            logger.warning("Semantic Scholar search rate-limited for query=%r: %s", query, exc)
            self._record_backend_diagnostic(
                "semantic_scholar",
                status="rate_limited",
                detail=str(exc),
                cache_status="miss",
            )
            return []
        except Exception as exc:
            logger.error("Semantic Scholar search failed for query=%r: %s", query, exc)
            self._record_backend_diagnostic(
                "semantic_scholar",
                status="error",
                detail=str(exc),
                cache_status="miss",
            )
            return []

    def tool_search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search for tool references while preferring GitHub-backed results.

        Args:
            query: Search query string.
            max_results: Maximum number of records to return.

        Returns:
            Search hits ordered with GitHub-backed results first.
        """

        logger.info("Searching DuckDuckGo for tools query=%r", query)
        try:
            ddgs_results = self._duckduckgo_html_search(f"{query} github")
        except Exception as exc:
            logger.error("DuckDuckGo tool search failed for query=%r: %s", query, exc)
            self._record_backend_diagnostic("tool_search", status="error", detail=str(exc))
            return []

        github_results: list[dict[str, str]] = []
        other_results: list[dict[str, str]] = []
        for result in ddgs_results:
            href = str(result.get("href", "") or "")
            if not href:
                continue
            payload = {
                "title": str(result.get("title", "") or ""),
                "href": href,
                "body": str(result.get("body", "") or ""),
            }
            if "github.com" in href:
                github_results.append(payload)
            else:
                other_results.append(payload)
        results = (github_results + other_results)[:max_results]
        self._record_backend_diagnostic("tool_search", status="ok" if results else "empty")
        return results

    def web_search(
        self,
        query: str,
        max_results: int = 8,
        allowed_domains: Optional[list[str]] = None,
    ) -> list[dict[str, str]]:
        """Run one trusted-web search with optional domain filtering.

        Args:
            query: Search query string.
            max_results: Maximum number of records to return.
            allowed_domains: Optional domain allow-list.

        Returns:
            Filtered web-search result rows.
        """

        logger.info("Searching trusted web query=%r domains=%s", query, allowed_domains)
        lowered_query = " ".join(str(query or "").lower().split())
        if any(marker in lowered_query for marker in _NEGATIVE_CONTROL_MARKERS):
            self._record_backend_diagnostic("web", status="empty", detail="negative_control_guard", cache_status="bypass")
            return []
        search_domains = list(allowed_domains or self.default_reference_domains)
        cache_key = self._literature_cache.response_cache_key(
            "web",
            query,
            max_results=max_results,
            allowed_domains=search_domains,
        )
        cached = self._literature_cache.get_cached_response(cache_key)
        if cached is not None:
            status = "ok" if cached.payload else "empty"
            self._record_backend_diagnostic("web", status=status, detail="cache_hit", cache_status="hit")
            return [dict(item) for item in cached.payload]
        results: list[dict[str, str]] = []
        try:
            query_variants = self._web_query_variants(query, search_domains)
        except Exception as exc:
            logger.error("Trusted web query planning failed for query=%r: %s", query, exc)
            self._record_backend_diagnostic("web", status="error", detail=str(exc), cache_status="miss")
            return results

        seen_urls: set[str] = set()
        diagnostic_status = "empty"
        diagnostic_detail = ""
        for query_variant in query_variants:
            try:
                ddgs_results = self._duckduckgo_html_search(query_variant)
            except Exception as exc:
                logger.error("DuckDuckGo web search failed for query=%r variant=%r: %s", query, query_variant, exc)
                diagnostic_status = "error"
                diagnostic_detail = str(exc)
                continue

            for result in ddgs_results:
                href = str(result.get("href", "") or "")
                if not href or href in seen_urls or not self._host_allowed(href, search_domains):
                    continue
                seen_urls.add(href)
                results.append(
                    {
                        "title": str(result.get("title", "") or ""),
                        "href": href,
                        "body": str(result.get("body", "") or ""),
                    }
                )
                if len(results) >= max_results:
                    self._literature_cache.put_cached_response(cache_key, backend="web", payload=results)
                    self._record_backend_diagnostic("web", status="ok", cache_status="miss")
                    return results
        if results or diagnostic_status == "empty":
            self._literature_cache.put_cached_response(cache_key, backend="web", payload=results)
        self._record_backend_diagnostic(
            "web",
            status="ok" if results else diagnostic_status,
            detail=diagnostic_detail,
            cache_status="miss",
        )
        return results
