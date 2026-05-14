"""Tests for bio_harness.core.benchmark_policy."""

from __future__ import annotations

import pytest

from bio_harness.core.benchmark_policy import (
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    SCIENTIFIC_HARNESS_POLICY,
    filter_forbidden_benchmark_sources,
    is_bioagentbench_planning_strict_policy,
    is_blind_bioagentbench_policy,
    is_forbidden_benchmark_source,
    is_official_bioagentbench_policy,
    normalize_benchmark_policy,
)


# ---------------------------------------------------------------------------
# normalize_benchmark_policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("scientific_harness", SCIENTIFIC_HARNESS_POLICY),
        ("official_bioagentbench", OFFICIAL_BIOAGENTBENCH_POLICY),
        ("bioagentbench_planning_strict", BIOAGENTBENCH_PLANNING_STRICT_POLICY),
        ("SCIENTIFIC_HARNESS", SCIENTIFIC_HARNESS_POLICY),
        ("OFFICIAL_BIOAGENTBENCH", OFFICIAL_BIOAGENTBENCH_POLICY),
        ("BIOAGENTBENCH_PLANNING_STRICT", BIOAGENTBENCH_PLANNING_STRICT_POLICY),
        ("  scientific_harness  ", SCIENTIFIC_HARNESS_POLICY),
        ("unknown_policy", SCIENTIFIC_HARNESS_POLICY),
        ("", SCIENTIFIC_HARNESS_POLICY),
        (None, SCIENTIFIC_HARNESS_POLICY),
    ],
    ids=[
        "exact_scientific",
        "exact_official",
        "exact_planning_strict",
        "uppercase_scientific",
        "uppercase_official",
        "uppercase_planning_strict",
        "whitespace_padded",
        "unknown_falls_back",
        "empty_falls_back",
        "none_falls_back",
    ],
)
def test_normalize_benchmark_policy(value: str | None, expected: str):
    assert normalize_benchmark_policy(value) == expected


# ---------------------------------------------------------------------------
# is_official_bioagentbench_policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [
        ("official_bioagentbench", True),
        ("scientific_harness", False),
        (None, False),
        ("", False),
    ],
)
def test_is_official_bioagentbench_policy(value: str | None, expected: bool):
    assert is_official_bioagentbench_policy(value) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("bioagentbench_planning_strict", True),
        ("official_bioagentbench", False),
        ("scientific_harness", False),
        (None, False),
    ],
)
def test_is_bioagentbench_planning_strict_policy(value: str | None, expected: bool):
    assert is_bioagentbench_planning_strict_policy(value) is expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("bioagentbench_planning_strict", True),
        ("official_bioagentbench", True),
        ("scientific_harness", False),
        (None, False),
    ],
)
def test_is_blind_bioagentbench_policy(value: str | None, expected: bool):
    assert is_blind_bioagentbench_policy(value) is expected


# ---------------------------------------------------------------------------
# is_forbidden_benchmark_source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, expected",
    [
        # Forbidden patterns
        ("/data/external/tasks/evolution/ref.fa", True),
        ("/workspace/results/output.csv", True),
        ("C:\\data\\external\\tasks\\file.txt", True),
        # Safe patterns
        ("/data/internal/tasks/evolution/ref.fa", False),
        ("/workspace/outputs/result.csv", False),
        ("", False),
        (None, False),
    ],
    ids=[
        "external_tasks_forbidden",
        "results_forbidden",
        "windows_backslash_forbidden",
        "internal_tasks_safe",
        "outputs_safe",
        "empty_safe",
        "none_safe",
    ],
)
def test_is_forbidden_benchmark_source(path: str | None, expected: bool):
    assert is_forbidden_benchmark_source(path) is expected


# ---------------------------------------------------------------------------
# filter_forbidden_benchmark_sources
# ---------------------------------------------------------------------------


def test_filter_forbidden_returns_only_forbidden():
    paths = [
        "/data/external/tasks/evo/ref.fa",
        "/data/internal/safe.fa",
        "/workspace/results/output.csv",
        "/workspace/outputs/ok.csv",
    ]
    result = filter_forbidden_benchmark_sources(paths)
    assert len(result) == 2
    assert "/data/external/tasks/evo/ref.fa" in result
    assert "/workspace/results/output.csv" in result


def test_filter_forbidden_empty_input():
    assert filter_forbidden_benchmark_sources(None) == []
    assert filter_forbidden_benchmark_sources([]) == []
