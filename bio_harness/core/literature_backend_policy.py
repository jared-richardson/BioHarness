"""Deterministic backend policy for literature retrieval.

This module separates backend eligibility and degradation handling from the
literature agent itself. The policy is intentionally deterministic so explicit
research benchmarking remains reproducible and benchmark-blind.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Mapping


@dataclass(frozen=True)
class BackendStatusSnapshot:
    """Immutable view of one backend's current session state.

    Attributes:
        backend: Stable backend name.
        queries_attempted: Number of attempted queries.
        hit_count: Number of normalized hits returned so far.
        timeout_count: Number of timeout outcomes observed so far.
        error_count: Number of error outcomes observed so far.
        detail: Most recent stable backend detail string.
    """

    backend: str
    queries_attempted: int = 0
    hit_count: int = 0
    timeout_count: int = 0
    error_count: int = 0
    persistent_timeout_count: int = 0
    persistent_error_count: int = 0
    persistent_empty_count: int = 0
    suppressed_until_epoch: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class BackendUsageDecision:
    """One deterministic backend eligibility decision.

    Attributes:
        backend: Stable backend name.
        eligible: Whether the backend should be queried now.
        tier: Health tier label for this backend.
        reason: Stable reason for the decision.
    """

    backend: str
    eligible: bool
    tier: str
    reason: str


def snapshot_backend_status(
    backend: str,
    backend_rollup: Mapping[str, Mapping[str, Any]],
) -> BackendStatusSnapshot:
    """Build one immutable snapshot from a mutable rollup payload.

    Args:
        backend: Backend name to snapshot.
        backend_rollup: Session rollup mapping keyed by backend name.

    Returns:
        Immutable backend snapshot.
    """

    payload = backend_rollup.get(backend, {})
    return BackendStatusSnapshot(
        backend=backend,
        queries_attempted=int(payload.get("queries_attempted", 0) or 0),
        hit_count=int(payload.get("hit_count", 0) or 0),
        timeout_count=int(payload.get("timeout_count", 0) or 0),
        error_count=int(payload.get("error_count", 0) or 0),
        persistent_timeout_count=int(payload.get("persistent_timeout_count", 0) or 0),
        persistent_error_count=int(payload.get("persistent_error_count", 0) or 0),
        persistent_empty_count=int(payload.get("persistent_empty_count", 0) or 0),
        suppressed_until_epoch=float(payload.get("suppressed_until_epoch", 0.0) or 0.0),
        detail=str(payload.get("detail", "") or ""),
    )


def merge_backend_health_memory(
    backend_rollup: Mapping[str, Mapping[str, Any]],
    health_memory: Mapping[str, Mapping[str, Any]],
    *,
    now_epoch: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge persisted backend health memory into a session rollup.

    Args:
        backend_rollup: In-run backend rollup keyed by backend name.
        health_memory: Persisted health-memory payload keyed by backend name.
        now_epoch: Optional fixed clock value for tests.

    Returns:
        Copy of the backend rollup enriched with persistent health fields.
    """

    now = time.time() if now_epoch is None else float(now_epoch)
    merged: dict[str, dict[str, Any]] = {}
    for backend in set(backend_rollup) | set(health_memory):
        payload = dict(backend_rollup.get(backend, {}))
        remembered = health_memory.get(backend, {})
        if isinstance(remembered, Mapping):
            payload["persistent_timeout_count"] = int(remembered.get("persistent_timeout_count", 0) or 0)
            payload["persistent_error_count"] = int(remembered.get("persistent_error_count", 0) or 0)
            payload["persistent_empty_count"] = int(remembered.get("persistent_empty_count", 0) or 0)
            suppressed_until_epoch = float(remembered.get("suppressed_until_epoch", 0.0) or 0.0)
            if suppressed_until_epoch > now:
                payload["suppressed_until_epoch"] = suppressed_until_epoch
            if not str(payload.get("detail", "") or ""):
                payload["detail"] = str(remembered.get("detail", "") or "")
        merged[str(backend)] = payload
    return merged


def classify_backend_tier(
    snapshot: BackendStatusSnapshot,
    *,
    optional_backend: bool,
) -> str:
    """Classify one backend into a stable health tier.

    Args:
        snapshot: Backend session snapshot.
        optional_backend: Whether the backend is optional in the current stack.

    Returns:
        One of ``healthy``, ``degraded``, or ``suppressed``.
    """

    detail = snapshot.detail.lower()
    if snapshot.suppressed_until_epoch > time.time():
        return "suppressed"
    if snapshot.timeout_count > 0 or "rate limit" in detail or "429" in detail:
        return "suppressed"
    if snapshot.error_count > 0 or (optional_backend and snapshot.persistent_error_count >= 2):
        return "degraded"
    if optional_backend and (snapshot.queries_attempted >= 2 or snapshot.persistent_empty_count >= 3) and snapshot.hit_count <= 0:
        return "degraded"
    return "healthy"


def decide_backend_usage(
    backend: str,
    backend_rollup: Mapping[str, Mapping[str, Any]],
    *,
    current_hit_count: int,
    primary_hit_count: int,
    query_available: bool,
    trusted_protocol_hit_count: int = 0,
    protocol_coverage_required: bool = False,
) -> BackendUsageDecision:
    """Return one deterministic backend-usage decision.

    Args:
        backend: Backend name.
        backend_rollup: Session rollup mapping keyed by backend name.
        current_hit_count: Total hit count accumulated so far.
        primary_hit_count: PubMed hit count accumulated so far.
        query_available: Whether the backend query plan still has a query for
            the current step.
        trusted_protocol_hit_count: Count of accepted canonical protocol or
            package-document hits accumulated so far.
        protocol_coverage_required: Whether the current question benefits from
            preserving at least one trusted protocol document.

    Returns:
        Stable backend usage decision.
    """

    if not query_available:
        return BackendUsageDecision(
            backend=backend,
            eligible=False,
            tier="healthy",
            reason="query_plan_exhausted",
        )

    optional_backend = backend in {"web", "semantic_scholar"}
    snapshot = snapshot_backend_status(backend, backend_rollup)
    tier = classify_backend_tier(snapshot, optional_backend=optional_backend)
    if backend == "pubmed":
        if tier == "suppressed":
            return BackendUsageDecision(backend=backend, eligible=False, tier=tier, reason="backend_suppressed")
        return BackendUsageDecision(backend=backend, eligible=True, tier=tier, reason="primary_backend")
    if backend == "web":
        if tier == "suppressed":
            return BackendUsageDecision(backend=backend, eligible=False, tier=tier, reason="backend_suppressed")
        if protocol_coverage_required and trusted_protocol_hit_count <= 0:
            return BackendUsageDecision(backend=backend, eligible=True, tier=tier, reason="protocol_gap")
        if primary_hit_count >= 2:
            return BackendUsageDecision(backend=backend, eligible=False, tier=tier, reason="primary_hits_sufficient")
        if tier == "degraded" and current_hit_count > 0:
            return BackendUsageDecision(backend=backend, eligible=False, tier=tier, reason="optional_backend_degraded")
        return BackendUsageDecision(backend=backend, eligible=True, tier=tier, reason="supplement_primary")
    if backend == "semantic_scholar":
        if primary_hit_count >= 1:
            return BackendUsageDecision(
                backend=backend,
                eligible=False,
                tier=tier,
                reason="primary_literature_present",
            )
        if current_hit_count >= 2:
            return BackendUsageDecision(
                backend=backend,
                eligible=False,
                tier=tier,
                reason="evidence_already_sufficient",
            )
        if tier in {"suppressed", "degraded"}:
            return BackendUsageDecision(backend=backend, eligible=False, tier=tier, reason="backend_suppressed")
        return BackendUsageDecision(backend=backend, eligible=True, tier=tier, reason="citation_enrichment")
    return BackendUsageDecision(backend=backend, eligible=False, tier=tier, reason="unsupported_backend")
