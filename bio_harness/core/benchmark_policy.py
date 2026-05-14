from __future__ import annotations

from typing import Iterable


SCIENTIFIC_HARNESS_POLICY = "scientific_harness"
OFFICIAL_BIOAGENTBENCH_POLICY = "official_bioagentbench"
BIOAGENTBENCH_PLANNING_STRICT_POLICY = "bioagentbench_planning_strict"
VALID_BENCHMARK_POLICIES = frozenset(
    {
        SCIENTIFIC_HARNESS_POLICY,
        OFFICIAL_BIOAGENTBENCH_POLICY,
        BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    }
)


def normalize_benchmark_policy(value: str | None) -> str:
    token = str(value or "").strip().lower()
    if token in VALID_BENCHMARK_POLICIES:
        return token
    return SCIENTIFIC_HARNESS_POLICY


def is_official_bioagentbench_policy(value: str | None) -> bool:
    return normalize_benchmark_policy(value) == OFFICIAL_BIOAGENTBENCH_POLICY


def is_bioagentbench_planning_strict_policy(value: str | None) -> bool:
    return normalize_benchmark_policy(value) == BIOAGENTBENCH_PLANNING_STRICT_POLICY


def is_blind_bioagentbench_policy(value: str | None) -> bool:
    normalized = normalize_benchmark_policy(value)
    return normalized in {
        OFFICIAL_BIOAGENTBENCH_POLICY,
        BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    }


def is_forbidden_benchmark_source(path: str | None) -> bool:
    normalized = str(path or "").replace("\\", "/").lower()
    if not normalized:
        return False
    return ("/external/" in normalized and "/tasks/" in normalized) or "/results/" in normalized


def filter_forbidden_benchmark_sources(paths: Iterable[str] | None) -> list[str]:
    return [str(path).strip() for path in (paths or []) if is_forbidden_benchmark_source(str(path).strip())]
