"""Persistent backend health memory and response caching for literature retrieval.

This module keeps literature-retrieval state small, deterministic, and
benchmark-auditable. It provides two related facilities:

- response caching for successful or empty backend payloads
- rolling backend health memory for cross-run suppression of flaky optional
  backends
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_LITERATURE_CACHE_ROOT = Path(__file__).resolve().parents[2] / "workspace" / "cache"
_DEFAULT_RESPONSE_TTL_SECONDS = 6.0 * 60.0 * 60.0
_OPTIONAL_BACKEND_SUPPRESSION_TTLS = {
    "semantic_scholar": 30.0 * 60.0,
    "web": 10.0 * 60.0,
}
_HEALTH_SCHEMA_VERSION = 1
_RESPONSE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BackendHealthRecord:
    """One persisted backend health-memory row.

    Attributes:
        backend: Stable backend name.
        consecutive_error_count: Number of consecutive error outcomes.
        consecutive_timeout_count: Number of consecutive timeout outcomes.
        consecutive_empty_count: Number of consecutive empty outcomes.
        last_status: Most recent backend status.
        last_detail: Most recent stable backend detail string.
        suppressed_until_epoch: Epoch timestamp until which the backend should
            be treated as suppressed.
        updated_at_epoch: Epoch timestamp of the most recent update.
    """

    backend: str
    consecutive_error_count: int = 0
    consecutive_timeout_count: int = 0
    consecutive_empty_count: int = 0
    last_status: str = ""
    last_detail: str = ""
    suppressed_until_epoch: float = 0.0
    updated_at_epoch: float = 0.0


@dataclass(frozen=True)
class CachedBackendResponse:
    """One cached literature backend response payload.

    Attributes:
        backend: Stable backend name.
        cache_key: Deterministic cache key.
        payload: Cached normalized result payload.
        created_at_epoch: Creation timestamp.
        expires_at_epoch: Expiration timestamp.
    """

    backend: str
    cache_key: str
    payload: tuple[dict[str, Any], ...]
    created_at_epoch: float
    expires_at_epoch: float


class LiteratureCache:
    """Persist backend response cache and cross-run backend health state."""

    def __init__(
        self,
        cache_root: Path | None = None,
        *,
        response_cache_enabled: bool = True,
        health_memory_enabled: bool = True,
        response_ttl_seconds: float = _DEFAULT_RESPONSE_TTL_SECONDS,
        suppression_ttls: dict[str, float] | None = None,
    ) -> None:
        """Initialize one literature cache instance.

        Args:
            cache_root: Root directory for persisted cache files.
            response_cache_enabled: Whether response caching is enabled.
            health_memory_enabled: Whether backend health memory is enabled.
            response_ttl_seconds: Response cache entry TTL in seconds.
            suppression_ttls: Optional backend-specific suppression TTLs.
        """

        self._cache_root = Path(cache_root or DEFAULT_LITERATURE_CACHE_ROOT).resolve(strict=False)
        self._response_cache_enabled = bool(response_cache_enabled)
        self._health_memory_enabled = bool(health_memory_enabled)
        self._response_ttl_seconds = max(1.0, float(response_ttl_seconds))
        self._suppression_ttls = dict(_OPTIONAL_BACKEND_SUPPRESSION_TTLS)
        if isinstance(suppression_ttls, dict):
            for key, value in suppression_ttls.items():
                try:
                    self._suppression_ttls[str(key)] = max(1.0, float(value))
                except (TypeError, ValueError):
                    continue
        self._response_path = self._cache_root / "literature_response_cache.json"
        self._health_path = self._cache_root / "literature_backend_health.json"
        self._response_cache_stats = {
            "hits": 0,
            "misses": 0,
            "writes": 0,
        }

    @property
    def cache_root(self) -> Path:
        """Return the cache root directory."""

        return self._cache_root

    @property
    def response_cache_path(self) -> Path:
        """Return the persisted response-cache path."""

        return self._response_path

    @property
    def health_memory_path(self) -> Path:
        """Return the persisted backend-health path."""

        return self._health_path

    @property
    def response_cache_enabled(self) -> bool:
        """Return whether response caching is enabled."""

        return self._response_cache_enabled

    @property
    def health_memory_enabled(self) -> bool:
        """Return whether backend health memory is enabled."""

        return self._health_memory_enabled

    def clear_response_cache(self) -> None:
        """Delete the persisted response cache."""

        if self._response_path.exists():
            self._response_path.unlink()
        self._response_cache_stats = {"hits": 0, "misses": 0, "writes": 0}

    def clear_backend_health(self) -> None:
        """Delete the persisted backend health memory."""

        if self._health_path.exists():
            self._health_path.unlink()

    def response_cache_key(
        self,
        backend: str,
        query: str,
        *,
        max_results: int,
        min_citations: int = 0,
        allowed_domains: list[str] | None = None,
    ) -> str:
        """Build one deterministic response-cache key.

        Args:
            backend: Stable backend name.
            query: Backend query string.
            max_results: Maximum result count for the call.
            min_citations: Minimum citation threshold when relevant.
            allowed_domains: Optional allowed-domain filter.

        Returns:
            Stable hexadecimal cache key.
        """

        payload = {
            "backend": str(backend or "").strip().lower(),
            "query": " ".join(str(query or "").strip().split()).lower(),
            "max_results": int(max_results),
            "min_citations": int(min_citations),
            "allowed_domains": sorted(str(item or "").strip().lower() for item in (allowed_domains or [])),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def get_cached_response(self, cache_key: str) -> CachedBackendResponse | None:
        """Return one cached response when it is present and fresh.

        Args:
            cache_key: Deterministic cache key.

        Returns:
            Cached response payload when available, otherwise ``None``.
        """

        if not self._response_cache_enabled:
            return None
        payload = self._load_response_payload()
        entry = payload.get("entries", {}).get(str(cache_key), {})
        if not isinstance(entry, dict):
            self._response_cache_stats["misses"] += 1
            return None
        now_epoch = time.time()
        expires_at_epoch = float(entry.get("expires_at_epoch", 0.0) or 0.0)
        if expires_at_epoch <= now_epoch:
            entries = payload.get("entries", {})
            if isinstance(entries, dict) and str(cache_key) in entries:
                entries.pop(str(cache_key), None)
                self._write_json(self._response_path, payload)
            self._response_cache_stats["misses"] += 1
            return None
        rows = entry.get("payload", [])
        self._response_cache_stats["hits"] += 1
        return CachedBackendResponse(
            backend=str(entry.get("backend", "") or ""),
            cache_key=str(cache_key),
            payload=tuple(item for item in rows if isinstance(item, dict)),
            created_at_epoch=float(entry.get("created_at_epoch", 0.0) or 0.0),
            expires_at_epoch=expires_at_epoch,
        )

    def put_cached_response(
        self,
        cache_key: str,
        *,
        backend: str,
        payload: list[dict[str, Any]],
    ) -> None:
        """Persist one response-cache entry.

        Args:
            cache_key: Deterministic cache key.
            backend: Stable backend name.
            payload: Normalized response rows to persist.
        """

        if not self._response_cache_enabled:
            return
        now_epoch = time.time()
        store = self._load_response_payload()
        entries = store.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = {}
            store["entries"] = entries
        entries[str(cache_key)] = {
            "backend": str(backend or ""),
            "created_at_epoch": now_epoch,
            "expires_at_epoch": now_epoch + self._response_ttl_seconds,
            "payload": [item for item in payload if isinstance(item, dict)],
        }
        self._write_json(self._response_path, store)
        self._response_cache_stats["writes"] += 1

    def backend_health_record(self, backend: str) -> BackendHealthRecord:
        """Return one persisted backend-health row.

        Args:
            backend: Stable backend name.

        Returns:
            Persisted backend-health record, or a zero-value row when absent.
        """

        rows = self._load_health_payload().get("backends", {})
        payload = rows.get(str(backend), {}) if isinstance(rows, dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        return BackendHealthRecord(
            backend=str(backend or ""),
            consecutive_error_count=int(payload.get("consecutive_error_count", 0) or 0),
            consecutive_timeout_count=int(payload.get("consecutive_timeout_count", 0) or 0),
            consecutive_empty_count=int(payload.get("consecutive_empty_count", 0) or 0),
            last_status=str(payload.get("last_status", "") or ""),
            last_detail=str(payload.get("last_detail", "") or ""),
            suppressed_until_epoch=float(payload.get("suppressed_until_epoch", 0.0) or 0.0),
            updated_at_epoch=float(payload.get("updated_at_epoch", 0.0) or 0.0),
        )

    def backend_health_context(self) -> dict[str, dict[str, Any]]:
        """Return a policy-ready backend-health context payload."""

        if not self._health_memory_enabled:
            return {}
        payload = self._load_health_payload().get("backends", {})
        if not isinstance(payload, dict):
            return {}
        context: dict[str, dict[str, Any]] = {}
        for backend, row in payload.items():
            if not isinstance(row, dict):
                continue
            context[str(backend)] = {
                "persistent_error_count": int(row.get("consecutive_error_count", 0) or 0),
                "persistent_timeout_count": int(row.get("consecutive_timeout_count", 0) or 0),
                "persistent_empty_count": int(row.get("consecutive_empty_count", 0) or 0),
                "suppressed_until_epoch": float(row.get("suppressed_until_epoch", 0.0) or 0.0),
                "detail": str(row.get("last_detail", "") or ""),
            }
        return context

    def record_backend_outcome(
        self,
        backend: str,
        *,
        status: str,
        detail: str = "",
        hit_count: int = 0,
    ) -> None:
        """Persist one backend outcome into rolling health memory.

        Args:
            backend: Stable backend name.
            status: Backend status label.
            detail: Stable backend detail string.
            hit_count: Number of hits returned for the backend call.
        """

        if not self._health_memory_enabled:
            return
        backend_name = str(backend or "").strip()
        if not backend_name:
            return
        payload = self._load_health_payload()
        backends = payload.setdefault("backends", {})
        if not isinstance(backends, dict):
            backends = {}
            payload["backends"] = backends
        current = self.backend_health_record(backend_name)
        normalized_status = str(status or "").strip() or "unknown"
        now_epoch = time.time()
        next_record = BackendHealthRecord(
            backend=backend_name,
            consecutive_error_count=current.consecutive_error_count,
            consecutive_timeout_count=current.consecutive_timeout_count,
            consecutive_empty_count=current.consecutive_empty_count,
            last_status=normalized_status,
            last_detail=str(detail or "").strip(),
            suppressed_until_epoch=current.suppressed_until_epoch,
            updated_at_epoch=now_epoch,
        )
        if normalized_status == "ok" and int(hit_count) > 0:
            next_record = BackendHealthRecord(
                backend=backend_name,
                last_status=normalized_status,
                last_detail="",
                updated_at_epoch=now_epoch,
            )
        elif normalized_status == "empty":
            next_record = BackendHealthRecord(
                backend=backend_name,
                consecutive_empty_count=current.consecutive_empty_count + 1,
                last_status=normalized_status,
                last_detail="",
                updated_at_epoch=now_epoch,
            )
        elif normalized_status == "timeout":
            next_record = BackendHealthRecord(
                backend=backend_name,
                consecutive_timeout_count=current.consecutive_timeout_count + 1,
                last_status=normalized_status,
                last_detail=str(detail or "").strip(),
                suppressed_until_epoch=self._suppressed_until_epoch(backend_name, now_epoch, detail=detail),
                updated_at_epoch=now_epoch,
            )
        elif normalized_status == "error":
            next_record = BackendHealthRecord(
                backend=backend_name,
                consecutive_error_count=current.consecutive_error_count + 1,
                last_status=normalized_status,
                last_detail=str(detail or "").strip(),
                suppressed_until_epoch=self._suppressed_until_epoch(backend_name, now_epoch, detail=detail),
                updated_at_epoch=now_epoch,
            )
        backends[backend_name] = asdict(next_record)
        self._write_json(self._health_path, payload)

    def summary(self) -> dict[str, Any]:
        """Return one deterministic summary of cache and health-memory state."""

        context = self.backend_health_context()
        return {
            "cache_root": str(self._cache_root),
            "response_cache_enabled": self._response_cache_enabled,
            "health_memory_enabled": self._health_memory_enabled,
            "response_cache_path": str(self._response_path),
            "health_memory_path": str(self._health_path),
            "response_cache_stats": dict(self._response_cache_stats),
            "response_entry_count": self._response_entry_count(),
            "active_suppressions": sorted(
                backend
                for backend, payload in context.items()
                if float(payload.get("suppressed_until_epoch", 0.0) or 0.0) > time.time()
            ),
        }

    def _suppressed_until_epoch(
        self,
        backend: str,
        now_epoch: float,
        *,
        detail: str,
    ) -> float:
        """Return one suppression expiry timestamp for an optional backend."""

        if backend not in self._suppression_ttls:
            return 0.0
        detail_text = str(detail or "").lower()
        if "429" in detail_text or "rate limit" in detail_text or "timed out" in detail_text:
            return now_epoch + float(self._suppression_ttls[backend])
        return now_epoch + float(self._suppression_ttls[backend]) if backend == "semantic_scholar" else 0.0

    def _response_entry_count(self) -> int:
        """Return the number of live response-cache entries."""

        payload = self._load_response_payload().get("entries", {})
        return len(payload) if isinstance(payload, dict) else 0

    def _load_health_payload(self) -> dict[str, Any]:
        """Load the backend-health payload from disk."""

        return self._load_json(
            self._health_path,
            default={"schema_version": _HEALTH_SCHEMA_VERSION, "backends": {}},
        )

    def _load_response_payload(self) -> dict[str, Any]:
        """Load the response-cache payload from disk."""

        return self._load_json(
            self._response_path,
            default={"schema_version": _RESPONSE_SCHEMA_VERSION, "entries": {}},
        )

    def _load_json(self, path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
        """Load one JSON payload or return a default object."""

        if not path.exists():
            return dict(default)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(default)
        return payload if isinstance(payload, dict) else dict(default)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Write one JSON payload atomically enough for local benchmark use."""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

