from __future__ import annotations

from bio_harness.ui.run_paths import resolve_effective_chat_selected_dir


def test_resolve_effective_chat_selected_dir_keeps_session_directory_for_normal_ui() -> None:
    observed = resolve_effective_chat_selected_dir(
        {"run_dir": "/tmp/run_001"},
        session_selected_dir="/tmp/workspace/project_a",
        benchmark_policy="scientific_harness",
    )

    assert observed == "/tmp/workspace/project_a"


def test_resolve_effective_chat_selected_dir_prefers_run_dir_for_blind_benchmark_ui() -> None:
    observed = resolve_effective_chat_selected_dir(
        {"run_dir": "/tmp/run_002"},
        session_selected_dir="/tmp/workspace/project_b",
        benchmark_policy="official_bioagentbench",
    )

    assert observed == "/tmp/run_002"


def test_resolve_effective_chat_selected_dir_falls_back_when_run_dir_missing() -> None:
    observed = resolve_effective_chat_selected_dir(
        {"run_dir": ""},
        session_selected_dir="/tmp/workspace/project_c",
        benchmark_policy="bioagentbench_planning_strict",
    )

    assert observed == "/tmp/workspace/project_c"
