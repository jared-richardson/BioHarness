from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from run_variant_benchmark import (  # noqa: E402
    _build_runner_command,
    _extended_results_from_payload,
    _feature_results_from_payload,
    _load_suite_results,
    _official_results_from_payload,
    _variant_env,
    main,
)


def test_build_runner_command_strips_remainder_separator(tmp_path: Path) -> None:
    suite_script = tmp_path / "runner.py"

    command = _build_runner_command(
        suite_script,
        suite="extended",
        runner_args=["--", "--quick", "--case-id", "abc"],
        config_overrides={},
    )

    assert command == [
        sys.executable,
        str(suite_script),
        "--quick",
        "--case-id",
        "abc",
    ]


def test_variant_env_applies_env_and_config_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_MODEL_HEAVY", "old-heavy")
    monkeypatch.setenv("BIO_HARNESS_MAX_REPAIRS", "9")

    env = _variant_env(
        env_overrides={
            "BIO_HARNESS_MODEL": "qwen3-coder-next:latest",
            "BIO_HARNESS_MODEL_HEAVY": "",
        },
        config_overrides={"max_repairs": 0, "diagnostic_traces": False},
    )

    assert env["BIO_HARNESS_MODEL"] == "qwen3-coder-next:latest"
    assert "BIO_HARNESS_MODEL_HEAVY" not in env
    assert env["BIO_HARNESS_DIAGNOSTIC_TRACES"] == "False"


def test_feature_results_from_payload_parses_scenarios() -> None:
    payload = {
        "scenarios": [
            {
                "feature": "output-quality-gate",
                "scenario_id": "bam_basic",
                "passed": True,
                "score": 0.95,
                "elapsed_seconds": 1.25,
                "error": "",
            }
        ]
    }

    results = _feature_results_from_payload(payload, "baseline")

    assert len(results) == 1
    assert results[0].variant_id == "baseline"
    assert results[0].task_name == "output-quality-gate:bam_basic"
    assert results[0].status == "pass"
    assert results[0].score == 0.95
    assert results[0].runtime_seconds == 1.25


def test_extended_results_from_payload_parses_items() -> None:
    payload = {
        "items": [
            {
                "case_id": "case_01",
                "passed": False,
                "status": "timed_out",
                "error": "timed out",
                "lane": "tier1_runnable",
            }
        ]
    }

    results = _extended_results_from_payload(payload, "baseline")

    assert len(results) == 1
    assert results[0].task_name == "case_01"
    assert results[0].status == "timed_out"
    assert results[0].score == 0.0
    assert results[0].error_message == "timed out"
    assert results[0].metadata["lane"] == "tier1_runnable"


def test_official_results_from_payload_parses_items() -> None:
    payload = {
        "items": [
            {
                "task_id": "single-cell",
                "official_report_bucket": "official_blind_clean",
                "validation_passed": True,
                "harness_error": "",
            },
            {
                "task_id": "giab",
                "official_report_bucket": "invalid_for_official_reporting",
                "validation_passed": False,
                "harness_error": "planner timeout",
            },
        ]
    }

    results = _official_results_from_payload(payload, "baseline")

    assert len(results) == 2
    assert results[0].task_name == "single-cell"
    assert results[0].status == "pass"
    assert results[0].score == 1.0
    assert results[1].task_name == "giab"
    assert results[1].status == "invalid_for_official_reporting"
    assert results[1].error_message == "planner timeout"


def test_load_suite_results_returns_empty_for_missing_report(tmp_path: Path) -> None:
    results = _load_suite_results(
        suite="extended",
        report_path=tmp_path / "missing.json",
        variant_id="baseline",
    )

    assert results == []


def test_main_dry_run_prints_variant_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_variant_benchmark.py",
            "--variant-id",
            "no_recovery",
            "--suite",
            "extended",
            "--report-path",
            str(report_path),
            "--dry-run",
            "--",
            "--lane",
            "tier1_runnable",
        ],
    )

    assert main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["variant_id"] == "no_recovery"
    assert payload["suite"] == "extended"
    assert payload["config_overrides"] == {"max_repairs": 0}
    assert payload["command"][-4:] == ["--max-repairs", "0", "--lane", "tier1_runnable"]


def test_main_records_results_from_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "official_summary.json"
    report_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "task_id": "alzheimer-mouse",
                        "official_report_bucket": "official_blind_clean",
                        "validation_passed": True,
                        "harness_error": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store_path = tmp_path / "variants.jsonl"
    seen: dict[str, object] = {}

    def _fake_run(command: list[str], cwd: str, env: dict[str, str], check: bool) -> SimpleNamespace:
        seen["command"] = command
        seen["cwd"] = cwd
        seen["env"] = env
        seen["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("run_variant_benchmark.subprocess.run", _fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_variant_benchmark.py",
            "--variant-id",
            "no_recovery",
            "--suite",
            "official",
            "--store-path",
            str(store_path),
            "--report-path",
            str(report_path),
        ],
    )

    assert main() == 0

    assert isinstance(seen["command"], list)
    assert seen["command"][1].endswith("run_bioagentbench_official.py")
    assert seen["cwd"] == str(Path(__file__).resolve().parents[2])
    assert seen["check"] is False
    assert "--max-repairs" in seen["command"]

    rows = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["task_name"] == "alzheimer-mouse"
    assert rows[0]["status"] == "pass"
    assert rows[0]["variant_id"] == "no_recovery"
