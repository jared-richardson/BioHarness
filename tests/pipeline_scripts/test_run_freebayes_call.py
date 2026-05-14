"""Tests for ``run_freebayes_call`` region-parallelism + watchdogs."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from bio_harness.pipeline_scripts import run_freebayes_call as mod
from bio_harness.pipeline_scripts.run_freebayes_call import (
    DEFAULT_STALL_TIMEOUT_SECONDS,
    DEFAULT_WALL_TIMEOUT_SECONDS,
    EXIT_STALL_WATCHDOG,
    EXIT_WALL_TIMEOUT,
    _choose_worker_count,
    _parse_fai_contigs,
    _run_with_watchdogs,
    plan_regions,
)


def _write_fai(path: Path, contigs: list[tuple[str, int]]) -> None:
    """Write a minimal .fai with (name, length, offset, linebases, linewidth)."""

    lines = []
    for name, length in contigs:
        lines.append(f"{name}\t{length}\t0\t60\t61")
    path.write_text("\n".join(lines) + "\n")


def test_parse_fai_contigs_returns_positive_length_entries(tmp_path: Path) -> None:
    fai = tmp_path / "ref.fa.fai"
    _write_fai(fai, [("chrA", 1_000_000), ("chrB", 500_000), ("bogus", 0)])
    contigs = _parse_fai_contigs(fai)
    assert contigs == [("chrA", 1_000_000), ("chrB", 500_000)]


def test_parse_fai_contigs_missing_returns_empty(tmp_path: Path) -> None:
    assert _parse_fai_contigs(tmp_path / "nope.fai") == []


def test_plan_regions_single_worker_yields_one_batch() -> None:
    batches = plan_regions([("chr1", 3_000_000), ("chr2", 1_500_000)], target_workers=1)
    assert len(batches) == 1
    # chr1 should be chunked into <=2Mb pieces
    assert any(r.startswith("chr1:") for r in batches[0])
    # chr2 fits in one chunk
    assert any(r.startswith("chr2:") for r in batches[0])


def test_plan_regions_balances_total_bp_across_workers() -> None:
    # One large contig (10Mb) and several small ones
    contigs = [("chr1", 10_000_000), ("chr2", 200_000), ("chr3", 150_000), ("chr4", 100_000)]
    batches = plan_regions(contigs, target_workers=4)
    assert 1 <= len(batches) <= 4
    # Estimate length per bin from region strings
    def _bin_length(regions: list[str]) -> int:
        total = 0
        for region in regions:
            _, span = region.split(":")
            start, end = span.split("-")
            total += int(end) - int(start) + 1
        return total

    totals = [_bin_length(b) for b in batches]
    # No bin should hold everything and load difference should be modest
    assert max(totals) - min(totals) <= 2_000_000 + 1


def test_plan_regions_caps_max_chunk_bp() -> None:
    batches = plan_regions(
        [("chrHuge", 9_000_000)],
        target_workers=1,
        max_chunk_bp=2_000_000,
    )
    assert len(batches) == 1
    regions = batches[0]
    # 9Mb / 2Mb chunk → at least 5 chunks
    assert len(regions) >= 5


def test_plan_regions_empty_contigs() -> None:
    assert plan_regions([], target_workers=4) == []


def test_choose_worker_count_respects_explicit_request() -> None:
    assert _choose_worker_count(2) == 2
    assert _choose_worker_count(1) == 1
    # Explicit large request capped at 16
    assert _choose_worker_count(100) == 16


def test_choose_worker_count_default_caps_at_8_and_leaves_headroom() -> None:
    n = _choose_worker_count(None)
    assert 1 <= n <= 8


def test_run_with_watchdogs_stall_triggers_exit_code(tmp_path: Path) -> None:
    # A sleep command that never writes output — stall watchdog should fire.
    stdout = tmp_path / "out.vcf"
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    rc = _run_with_watchdogs(
        command=cmd,
        stdout_path=stdout,
        wall_timeout_seconds=30,
        stall_timeout_seconds=3,
        watchdog_interval_seconds=1,
        log_prefix="test",
    )
    assert rc == EXIT_STALL_WATCHDOG


def test_run_with_watchdogs_wall_timeout_triggers_when_output_grows_continuously(
    tmp_path: Path,
) -> None:
    # Output grows constantly (no stall) but wall clock exceeds budget.
    stdout = tmp_path / "out.vcf"
    script = (
        "import sys, time\n"
        "end = time.time() + 30\n"
        "while time.time() < end:\n"
        "    sys.stdout.write('.'); sys.stdout.flush(); time.sleep(0.2)\n"
    )
    cmd = [sys.executable, "-c", script]
    rc = _run_with_watchdogs(
        command=cmd,
        stdout_path=stdout,
        wall_timeout_seconds=3,
        stall_timeout_seconds=60,
        watchdog_interval_seconds=1,
        log_prefix="test",
    )
    assert rc == EXIT_WALL_TIMEOUT


def test_run_with_watchdogs_passthrough_successful_exit(tmp_path: Path) -> None:
    stdout = tmp_path / "out.vcf"
    cmd = [sys.executable, "-c", "print('hello'); exit(0)"]
    rc = _run_with_watchdogs(
        command=cmd,
        stdout_path=stdout,
        wall_timeout_seconds=30,
        stall_timeout_seconds=10,
        watchdog_interval_seconds=1,
        log_prefix="test",
    )
    assert rc == 0
    assert "hello" in stdout.read_text()


def test_run_with_watchdogs_non_zero_exit_passthrough(tmp_path: Path) -> None:
    stdout = tmp_path / "out.vcf"
    cmd = [sys.executable, "-c", "import sys; sys.exit(7)"]
    rc = _run_with_watchdogs(
        command=cmd,
        stdout_path=stdout,
        wall_timeout_seconds=30,
        stall_timeout_seconds=10,
        watchdog_interval_seconds=1,
        log_prefix="test",
    )
    assert rc == 7


def test_run_freebayes_call_requires_output_destination(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output_vcf or output_vcf_gz"):
        mod.run_freebayes_call(
            reference_fasta=tmp_path / "ref.fa",
            input_bam=tmp_path / "sample.bam",
        )


def test_default_watchdog_thresholds_are_reasonable() -> None:
    # Contractual: wall > stall + 60 so the wall clamp logic is meaningful.
    assert DEFAULT_WALL_TIMEOUT_SECONDS >= DEFAULT_STALL_TIMEOUT_SECONDS + 60
    assert DEFAULT_STALL_TIMEOUT_SECONDS >= 60
