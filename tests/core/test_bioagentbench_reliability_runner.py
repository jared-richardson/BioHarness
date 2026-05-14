from __future__ import annotations

import json
from pathlib import Path

from bio_harness.core.benchmark_policy import OFFICIAL_BIOAGENTBENCH_POLICY
from scripts.run_bioagentbench_reliability import (
    SystemStatus,
    _attempt_label,
    _attempt_state,
    _count_active_attempts,
    _count_successful_reliability_passes,
    _launch_block_reason,
    _validator_indicates_pass,
    TaskStatus,
)


def test_attempt_label_sanitizes_task_id() -> None:
    assert _attempt_label("viral-metagenomics", 2, "a") == "planning_strict_reliability_viral_metagenomics_pass2a"
    assert (
        _attempt_label("viral-metagenomics", 2, "a", label_prefix="planning_strict_retest_cf_fix")
        == "planning_strict_retest_cf_fix_viral_metagenomics_pass2a"
    )


def test_validator_indicates_pass_accepts_both_validator_formats() -> None:
    assert _validator_indicates_pass("BENCHMARK PASSED: True\n")
    assert _validator_indicates_pass("BENCHMARK PASSED (4/4 checks)\n")
    assert not _validator_indicates_pass("BENCHMARK PASSED: False\n")


def test_count_successful_reliability_passes_only_counts_passing_reliability_runs(tmp_path: Path) -> None:
    passing = tmp_path / "planning_strict_reliability_demo_pass1a"
    passing.mkdir()
    (passing / "result.json").write_text(
        json.dumps(
            {
                "benchmark_policy": "bioagentbench_planning_strict",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (passing / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")

    failing = tmp_path / "planning_strict_reliability_demo_pass2a"
    failing.mkdir()
    (failing / "result.json").write_text(
        json.dumps(
            {
                "benchmark_policy": "bioagentbench_planning_strict",
                "status": "failed",
            }
        ),
        encoding="utf-8",
    )
    (failing / "validator.log").write_text("BENCHMARK PASSED: False\n", encoding="utf-8")

    unrelated = tmp_path / "planning_strict_fullsuite_r2"
    unrelated.mkdir()
    (unrelated / "result.json").write_text(
        json.dumps(
            {
                "benchmark_policy": "bioagentbench_planning_strict",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (unrelated / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")

    assert _count_successful_reliability_passes(tmp_path) == ["planning_strict_reliability_demo_pass1a"]


def test_count_successful_reliability_passes_filters_by_label_prefix(tmp_path: Path) -> None:
    legacy = tmp_path / "planning_strict_reliability_demo_pass1a"
    legacy.mkdir()
    (legacy / "result.json").write_text(
        json.dumps({"benchmark_policy": "bioagentbench_planning_strict", "status": "completed"}),
        encoding="utf-8",
    )
    (legacy / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")

    fresh = tmp_path / "planning_strict_retest_cf_fix_demo_pass1a"
    fresh.mkdir()
    (fresh / "result.json").write_text(
        json.dumps({"benchmark_policy": "bioagentbench_planning_strict", "status": "completed"}),
        encoding="utf-8",
    )
    (fresh / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")

    assert _count_successful_reliability_passes(
        tmp_path,
        label_prefix="planning_strict_retest_cf_fix",
    ) == ["planning_strict_retest_cf_fix_demo_pass1a"]


def test_attempt_state_reports_running_failed_and_missing(tmp_path: Path) -> None:
    running = tmp_path / "planning_strict_reliability_demo_pass1a"
    running.mkdir()
    assert _attempt_state(running) == "running"

    failed = tmp_path / "planning_strict_reliability_demo_pass2a"
    failed.mkdir()
    (failed / "result.json").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    assert _attempt_state(failed) == "failed"

    assert _attempt_state(tmp_path / "planning_strict_reliability_demo_pass3a") == "missing"


def test_count_successful_reliability_passes_respects_configured_benchmark_policy(tmp_path: Path) -> None:
    passing = tmp_path / "backend_backend_refresh_demo_pass1a"
    passing.mkdir()
    (passing / "result.json").write_text(
        json.dumps(
            {
                "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (passing / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")

    assert _count_successful_reliability_passes(
        tmp_path,
        label_prefix="backend_backend_refresh",
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    ) == ["backend_backend_refresh_demo_pass1a"]


def test_attempt_state_uses_configured_benchmark_policy_for_passes(tmp_path: Path) -> None:
    passing = tmp_path / "backend_backend_refresh_demo_pass1a"
    passing.mkdir()
    (passing / "result.json").write_text(
        json.dumps(
            {
                "benchmark_policy": OFFICIAL_BIOAGENTBENCH_POLICY,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (passing / "validator.log").write_text("BENCHMARK PASSED: True\n", encoding="utf-8")

    assert _attempt_state(
        passing,
        benchmark_policy=OFFICIAL_BIOAGENTBENCH_POLICY,
    ) == "passed"


def test_count_active_attempts_includes_preexisting_running_rows() -> None:
    rows = [
        TaskStatus(
            task_id="a",
            runs_root="/tmp/a",
            completed_passes=0,
            successful_labels=[],
            active_label="pass1a",
            active_state="running",
            target_passes=3,
            failed_label="",
            launched_by_monitor=[],
            selected_dir="/tmp/a/pass1a",
            result_status="",
            validator_pass=None,
            auto_repair_history_count=None,
            launch_block_reason="",
        ),
        TaskStatus(
            task_id="b",
            runs_root="/tmp/b",
            completed_passes=1,
            successful_labels=["pass1a"],
            active_label="pass2a",
            active_state="launching",
            target_passes=3,
            failed_label="",
            launched_by_monitor=["pass2a"],
            selected_dir="/tmp/b/pass2a",
            result_status="",
            validator_pass=None,
            auto_repair_history_count=None,
            launch_block_reason="",
        ),
        TaskStatus(
            task_id="c",
            runs_root="/tmp/c",
            completed_passes=3,
            successful_labels=["pass1a", "pass2a", "pass3a"],
            active_label="",
            active_state="",
            target_passes=3,
            failed_label="",
            launched_by_monitor=[],
            selected_dir="",
            result_status="completed",
            validator_pass=True,
            auto_repair_history_count=0,
            launch_block_reason="",
        ),
    ]

    assert _count_active_attempts(rows) == 2


def test_launch_block_reason_checks_parallel_limit_before_new_launch() -> None:
    status = SystemStatus(
        cpu_count=8,
        active_attempts=4,
        load_1=4.0,
        load_5=3.5,
        load_per_core_1=0.5,
        available_mem_gb=12.0,
    )

    assert _launch_block_reason(
        system_status=status,
        parallel_limit=4,
        max_load_per_core=1.5,
        min_available_mem_gb=4.0,
        launch_stagger_seconds=20,
        seconds_since_last_launch=120.0,
    ) == "parallel_limit:4/4"


def test_launch_block_reason_checks_compute_health() -> None:
    status = SystemStatus(
        cpu_count=8,
        active_attempts=1,
        load_1=16.0,
        load_5=15.0,
        load_per_core_1=2.0,
        available_mem_gb=2.5,
    )

    assert _launch_block_reason(
        system_status=status,
        parallel_limit=4,
        max_load_per_core=1.5,
        min_available_mem_gb=4.0,
        launch_stagger_seconds=20,
        seconds_since_last_launch=120.0,
    ) == "load_per_core:2.0>1.500"


def test_launch_block_reason_checks_launch_stagger_after_compute_is_healthy() -> None:
    status = SystemStatus(
        cpu_count=8,
        active_attempts=1,
        load_1=2.0,
        load_5=2.0,
        load_per_core_1=0.25,
        available_mem_gb=12.0,
    )

    assert _launch_block_reason(
        system_status=status,
        parallel_limit=4,
        max_load_per_core=1.5,
        min_available_mem_gb=4.0,
        launch_stagger_seconds=20,
        seconds_since_last_launch=5.0,
    ) == "launch_stagger:5/20s"
