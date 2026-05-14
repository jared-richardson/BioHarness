"""Tests for the fast-signal reproduction baseline driver."""

from __future__ import annotations

from scripts.run_fast_signal_reproduction_baseline import (
    _classify_status,
    _completed_replicate_ids,
    _load_output_rows,
    _ollama_env_overrides,
    _run_replicate,
    _write_output_summary,
)


def test_reproduction_driver_dry_run_records_measurement_metadata() -> None:
    row = _run_replicate(
        experiment_id="exp44",
        command="echo hello",
        replicate=1,
        replicate_id="exp44.shard-a.replicate-1",
        shard_id="a",
        same_class_markers=[],
        timeout_seconds=0,
        measurement_purpose="A1_acceptance_smoke",
        override_reason="bootstrap measurement driver",
        optimization_profile="safe_local",
        model="qwen3.6:35b-a3b",
        model_digest="digest",
        backend_version="backend",
        env_overrides={"OLLAMA_KEEP_ALIVE": "12h"},
        dry_run=True,
    )

    assert row.status == "dry_run"
    assert row.measurement_purpose == "A1_acceptance_smoke"
    assert row.override_gate_status == "wait"
    assert row.override_reason == "bootstrap measurement driver"
    assert row.optimization_profile == "safe_local"
    assert row.metadata["replicate_id"] == "exp44.shard-a.replicate-1"
    assert row.metadata["env_overrides"] == {"OLLAMA_KEEP_ALIVE": "12h"}
    assert row.model_digest == "digest"


def test_reproduction_driver_classifies_infra_errors_before_failure_class() -> None:
    status = _classify_status(
        1,
        "Ollama connection refused while running duplicate_detector_granularity",
        ["duplicate_detector_granularity"],
    )

    assert status == "infra_error"


def test_reproduction_driver_classifies_same_class_marker() -> None:
    status = _classify_status(
        1,
        "duplicate_detector_granularity reproduced",
        ["duplicate_detector_granularity"],
    )

    assert status == "fail_same_class"


def test_reproduction_driver_builds_ollama_env_overrides() -> None:
    overrides = _ollama_env_overrides(keep_alive="12h", num_parallel="4")

    assert overrides == {
        "OLLAMA_KEEP_ALIVE": "12h",
        "OLLAMA_NUM_PARALLEL": "4",
    }


def test_reproduction_driver_reads_completed_replicates(tmp_path) -> None:
    output = tmp_path / "reproduction_rates.json"
    output.write_text(
        """
        {
          "rows": [
            {"metadata": {"replicate_id": "exp44.shard-a.replicate-1"}},
            {"metadata": {"replicate_id": "exp44.shard-a.replicate-2"}}
          ]
        }
        """,
        encoding="utf-8",
    )

    assert _completed_replicate_ids(output) == {
        "exp44.shard-a.replicate-1",
        "exp44.shard-a.replicate-2",
    }


def test_reproduction_driver_writes_resumeable_summary(tmp_path) -> None:
    output = tmp_path / "reproduction_rates.json"
    row = _run_replicate(
        experiment_id="exp44",
        command="echo hello",
        replicate=1,
        replicate_id="exp44.shard-a.replicate-1",
        shard_id="a",
        same_class_markers=[],
        timeout_seconds=0,
        measurement_purpose="phase0",
        override_reason="",
        optimization_profile="safe_local",
        model="qwen3.6:35b-a3b",
        model_digest="digest",
        backend_version="backend",
        env_overrides={},
        dry_run=True,
    )

    _write_output_summary(output, [row], skipped=[])

    assert _completed_replicate_ids(output) == {"exp44.shard-a.replicate-1"}
    loaded = _load_output_rows(output)
    assert len(loaded) == 1
    assert loaded[0].metadata["replicate_id"] == "exp44.shard-a.replicate-1"
