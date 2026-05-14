"""Backend-health benchmark helpers for explicit literature research."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable
from urllib.parse import urlparse

from bio_harness.core.literature_cache import LiteratureCache
from bio_harness.tools.librarian import Librarian


@dataclass(frozen=True)
class BackendHealthQuery:
    """One canonical biomedical backend-health query."""

    query_id: str
    description: str
    query_text: str


@dataclass(frozen=True)
class BackendHealthResult:
    """One backend result for one canonical query."""

    query_id: str
    backend: str
    status: str
    hit_count: int
    duration_seconds: float
    domains: tuple[str, ...]
    cache_status: str = ""
    detail: str = ""


def default_backend_health_queries() -> tuple[BackendHealthQuery, ...]:
    """Return the default canonical biomedical backend-health queries."""

    return (
        BackendHealthQuery(
            query_id="rnaseq_deseq2_small_sample",
            description="DESeq2 small-sample differential-expression methods.",
            query_text="RNA-seq DESeq2 small sample differential expression published methods",
        ),
        BackendHealthQuery(
            query_id="atacseq_macs2_tn5",
            description="ATAC-seq MACS2 / Tn5 protocol guidance.",
            query_text="ATAC-seq MACS2 Tn5 shifting differential accessibility published methods",
        ),
        BackendHealthQuery(
            query_id="direct_rna_minimap2",
            description="Oxford Nanopore direct RNA minimap2 preset guidance.",
            query_text="Oxford Nanopore direct RNA sequencing minimap2 preset published methods",
        ),
        BackendHealthQuery(
            query_id="negative_control",
            description="Low-yield negative-control query.",
            query_text="fictional bioinformatics assay nonexistenttool benchmark sanity check",
        ),
    )


def run_literature_backend_health_benchmark(
    *,
    output_root: Path,
    queries: tuple[BackendHealthQuery, ...] | None = None,
    librarian: Librarian | Any | None = None,
    timeout_seconds: dict[str, float] | None = None,
    allowed_domains: tuple[str, ...] | None = None,
    cache_root: Path | None = None,
    cache_mode: str = "disabled",
    health_memory_mode: str = "disabled",
) -> dict[str, Any]:
    """Run the canonical literature backend-health benchmark."""

    output_root.mkdir(parents=True, exist_ok=True)
    benchmark_queries = queries or default_backend_health_queries()
    timeouts = {
        "pubmed": 20.0,
        "semantic_scholar": 12.0,
        "web": 8.0,
    }
    if isinstance(timeout_seconds, dict):
        for key, value in timeout_seconds.items():
            try:
                timeouts[str(key)] = max(0.1, float(value))
            except (TypeError, ValueError):
                continue
    effective_cache_root = Path(cache_root or (output_root / "_cache")).resolve(strict=False)
    cache = LiteratureCache(
        cache_root=effective_cache_root,
        response_cache_enabled=cache_mode != "disabled",
        health_memory_enabled=health_memory_mode != "disabled",
    )
    if cache_mode == "cold":
        cache.clear_response_cache()
    if health_memory_mode == "reset":
        cache.clear_backend_health()
    agent = librarian if librarian is not None else Librarian(literature_cache=cache)
    results: list[BackendHealthResult] = []
    for query in benchmark_queries:
        results.extend(
            _benchmark_one_query(
                query,
                librarian=agent,
                timeouts=timeouts,
                allowed_domains=allowed_domains,
            )
        )
    for item in results:
        cache.record_backend_outcome(
            item.backend,
            status=item.status,
            detail=item.detail,
            hit_count=item.hit_count,
        )
    summary = _aggregate_results(
        results,
        cache_summary=cache.summary(),
        cache_mode=cache_mode,
        health_memory_mode=health_memory_mode,
    )
    (output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_root / "summary.md").write_text(_summary_markdown(summary).strip() + "\n", encoding="utf-8")
    return summary


def _benchmark_one_query(
    query: BackendHealthQuery,
    *,
    librarian: Librarian | Any,
    timeouts: dict[str, float],
    allowed_domains: tuple[str, ...] | None,
) -> list[BackendHealthResult]:
    """Benchmark all literature backends for one canonical query."""

    return [
        _run_backend_call(
            query,
            backend="pubmed",
            timeout_seconds=float(timeouts["pubmed"]),
            call=lambda: getattr(librarian, "pubmed_search")(query.query_text, max_results=5),
            diagnostic_getter=lambda: _backend_diagnostic(librarian, "pubmed"),
        ),
        _run_backend_call(
            query,
            backend="semantic_scholar",
            timeout_seconds=float(timeouts["semantic_scholar"]),
            call=lambda: getattr(librarian, "citation_search")(query.query_text, min_citations=0, max_results=5),
            diagnostic_getter=lambda: _backend_diagnostic(librarian, "semantic_scholar"),
        ),
        _run_backend_call(
            query,
            backend="web",
            timeout_seconds=float(timeouts["web"]),
            call=lambda: getattr(librarian, "web_search")(
                query.query_text,
                max_results=5,
                allowed_domains=list(allowed_domains) if allowed_domains else None,
            ),
            diagnostic_getter=lambda: _backend_diagnostic(librarian, "web"),
        ),
    ]


def _run_backend_call(
    query: BackendHealthQuery,
    *,
    backend: str,
    timeout_seconds: float,
    call: Callable[[], list[dict[str, Any]]],
    diagnostic_getter: Callable[[], dict[str, str]] | None = None,
) -> BackendHealthResult:
    """Run one bounded backend call and normalize the result."""

    result = _call_with_timeout(call, timeout_seconds=timeout_seconds)
    if result["status"] != "ok":
        return BackendHealthResult(
            query_id=query.query_id,
            backend=backend,
            status=str(result["status"]),
            hit_count=0,
            duration_seconds=float(result["duration_seconds"]),
            domains=(),
            cache_status="",
            detail=str(result["detail"]),
        )
    payload = result["payload"]
    rows = payload if isinstance(payload, list) else []
    domains = _domains_for_backend_rows(backend, rows)
    status = "ok" if rows else "empty"
    detail = str(result["detail"])
    if status == "empty" and diagnostic_getter is not None:
        diagnostic = diagnostic_getter()
        diagnostic_status = str(diagnostic.get("status", "") or "")
        if diagnostic_status in {"rate_limited", "error"}:
            status = "error"
            detail = str(diagnostic.get("detail", "") or diagnostic_status)
    diagnostic = diagnostic_getter() if diagnostic_getter is not None else {}
    return BackendHealthResult(
        query_id=query.query_id,
        backend=backend,
        status=status,
        hit_count=len(rows),
        duration_seconds=float(result["duration_seconds"]),
        domains=tuple(domains),
        cache_status=str(diagnostic.get("cache_status", "") or ""),
        detail=detail,
    )


def _backend_diagnostic(librarian: Librarian | Any, backend: str) -> dict[str, str]:
    """Read one lightweight diagnostic payload from a librarian instance."""

    if not hasattr(librarian, "last_backend_diagnostic"):
        return {"status": "", "detail": ""}
    try:
        payload = librarian.last_backend_diagnostic(backend)
    except Exception:
        return {"status": "", "detail": ""}
    if not isinstance(payload, dict):
        return {"status": "", "detail": ""}
    return {
        "status": str(payload.get("status", "") or ""),
        "detail": str(payload.get("detail", "") or ""),
        "cache_status": str(payload.get("cache_status", "") or ""),
    }


def _call_with_timeout(
    call: Callable[[], list[dict[str, Any]]],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run one backend call with a caller-side timeout."""

    queue: Queue[tuple[str, Any]] = Queue(maxsize=1)
    started_at = time.monotonic()

    def _runner() -> None:
        try:
            queue.put(("ok", call()))
        except Exception as exc:  # pragma: no cover - exercised in tests via the outer result
            queue.put(("error", exc))

    thread = threading.Thread(target=_runner, name="literature-backend-health", daemon=True)
    thread.start()
    try:
        status, payload = queue.get(timeout=max(0.1, float(timeout_seconds)))
    except Empty:
        return {
            "status": "timeout",
            "payload": [],
            "detail": f"timed out after {float(timeout_seconds):.1f}s",
            "duration_seconds": round(time.monotonic() - started_at, 3),
        }
    duration_seconds = round(time.monotonic() - started_at, 3)
    if status == "error":
        return {
            "status": "error",
            "payload": [],
            "detail": str(payload),
            "duration_seconds": duration_seconds,
        }
    return {
        "status": "ok",
        "payload": payload,
        "detail": "",
        "duration_seconds": duration_seconds,
    }


def _domains_for_backend_rows(backend: str, rows: list[dict[str, Any]]) -> list[str]:
    """Extract host domains from backend result rows."""

    if backend == "pubmed":
        return ["pubmed.ncbi.nlm.nih.gov"] if rows else []
    domains: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_url = str(row.get("url", "") or row.get("href", "") or "").strip()
        if not raw_url:
            continue
        hostname = (urlparse(raw_url).hostname or "").lower()
        if hostname and hostname not in domains:
            domains.append(hostname)
    return domains


def _aggregate_results(
    results: list[BackendHealthResult],
    *,
    cache_summary: dict[str, Any],
    cache_mode: str,
    health_memory_mode: str,
) -> dict[str, Any]:
    """Aggregate backend-health case results into one summary payload."""

    per_backend: dict[str, dict[str, Any]] = {}
    for item in results:
        payload = per_backend.setdefault(
            item.backend,
            {
                "queries_total": 0,
                "ok_count": 0,
                "empty_count": 0,
                "timeout_count": 0,
                "error_count": 0,
                "cache_hit_count": 0,
                "cache_miss_count": 0,
                "non_empty_hit_rate": 0.0,
                "mean_duration_seconds": 0.0,
                "domains": [],
            },
        )
        payload["queries_total"] += 1
        payload[f"{item.status}_count"] = int(payload.get(f"{item.status}_count", 0) or 0) + 1
        if item.cache_status == "hit":
            payload["cache_hit_count"] = int(payload.get("cache_hit_count", 0) or 0) + 1
        elif item.cache_status == "miss":
            payload["cache_miss_count"] = int(payload.get("cache_miss_count", 0) or 0) + 1
        payload["mean_duration_seconds"] += float(item.duration_seconds)
        for domain in item.domains:
            if domain not in payload["domains"]:
                payload["domains"].append(domain)
    for backend, payload in per_backend.items():
        total = int(payload.get("queries_total", 0) or 0)
        ok_count = int(payload.get("ok_count", 0) or 0)
        payload["non_empty_hit_rate"] = (ok_count / total) if total else 0.0
        payload["mean_duration_seconds"] = round(
            float(payload.get("mean_duration_seconds", 0.0) or 0.0) / total,
            3,
        ) if total else 0.0
        payload["backend"] = backend
    return {
        "queries": [asdict(item) for item in results],
        "per_backend": per_backend,
        "queries_total": len({item.query_id for item in results}),
        "cache_mode": str(cache_mode),
        "health_memory_mode": str(health_memory_mode),
        "cache_summary": cache_summary,
    }


def _summary_markdown(summary: dict[str, Any]) -> str:
    """Render the backend-health summary as Markdown."""

    lines = [
        "# Literature Backend Health Benchmark",
        "",
        f"- Canonical queries: `{summary.get('queries_total', 0)}`",
        f"- Cache mode: `{summary.get('cache_mode', 'disabled')}`",
        f"- Health memory mode: `{summary.get('health_memory_mode', 'disabled')}`",
        "",
        "| Backend | Queries | Non-empty hit rate | Mean duration (s) | Cache hits | Timeouts | Errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    per_backend = summary.get("per_backend", {})
    if isinstance(per_backend, dict):
        for backend, payload in sorted(per_backend.items()):
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{backend}` | {int(payload.get('queries_total', 0))} | "
                f"{float(payload.get('non_empty_hit_rate', 0.0)):.2f} | "
                f"{float(payload.get('mean_duration_seconds', 0.0)):.3f} | "
                f"{int(payload.get('cache_hit_count', 0))} | "
                f"{int(payload.get('timeout_count', 0))} | "
                f"{int(payload.get('error_count', 0))} |"
            )
    return "\n".join(lines)
