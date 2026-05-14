from __future__ import annotations

from bio_harness.ui.run_data_root import resolve_effective_run_data_root


def test_resolve_effective_run_data_root_prefers_session_root() -> None:
    observed = resolve_effective_run_data_root(
        session_data_root="/tmp/session_data",
        run={"requested_data_root": "/tmp/run_data", "selected_dir": "/tmp/run_dir"},
        fallback_selected_dir="/tmp/fallback",
    )

    assert observed == "/tmp/session_data"


def test_resolve_effective_run_data_root_falls_back_to_persisted_run_root() -> None:
    observed = resolve_effective_run_data_root(
        session_data_root="",
        run={"requested_data_root": "/tmp/run_data", "selected_dir": "/tmp/run_dir"},
        fallback_selected_dir="/tmp/fallback",
    )

    assert observed == "/tmp/run_data"


def test_resolve_effective_run_data_root_uses_selected_dir_when_run_root_missing() -> None:
    observed = resolve_effective_run_data_root(
        session_data_root="",
        run={"selected_dir": "/tmp/run_dir"},
        fallback_selected_dir="/tmp/fallback",
    )

    assert observed == "/tmp/run_dir"


def test_resolve_effective_run_data_root_uses_fallback_when_no_other_root_exists() -> None:
    observed = resolve_effective_run_data_root(
        session_data_root="",
        run={},
        fallback_selected_dir="/tmp/fallback",
    )

    assert observed == "/tmp/fallback"
