from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from trace_driven_improvement import (  # noqa: E402
    _collect_failed_runs,
    _collect_repair_history,
    _heuristic_analysis,
    main,
)


@pytest.fixture()
def mock_runs_dir(tmp_path: Path) -> Path:
    run1 = tmp_path / "run1"
    run1.mkdir()
    (run1 / "result.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failure_class": "tool_missing",
                "failed_step_number": 0,
                "analysis_type": "rna_seq_differential_expression",
                "final_plan": {"plan": [{"tool_name": "star_align", "arguments": {}}]},
                "auto_repair_history": [
                    {"action": "substitute", "failure_class": "tool_missing"},
                ],
            }
        ),
        encoding="utf-8",
    )
    step0 = run1 / "step_0"
    step0.mkdir()
    (step0 / "stderr.log").write_text("Error: STAR not found on PATH\n", encoding="utf-8")

    run2 = tmp_path / "run2"
    run2.mkdir()
    (run2 / "result.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failure_class": "tool_missing",
                "failed_step_number": 0,
                "analysis_type": "rna_seq_differential_expression",
                "final_plan": {"plan": [{"tool_name": "star_align", "arguments": {}}]},
                "auto_repair_history": [
                    {"action": "substitute", "failure_class": "tool_missing"},
                ],
            }
        ),
        encoding="utf-8",
    )
    step0b = run2 / "step_0"
    step0b.mkdir()
    (step0b / "stderr.log").write_text("Error: STAR binary not found\n", encoding="utf-8")

    run3 = tmp_path / "run3"
    run3.mkdir()
    (run3 / "result.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")

    return tmp_path


def test_collect_failed_runs(mock_runs_dir: Path) -> None:
    failed = _collect_failed_runs(mock_runs_dir)

    assert len(failed) == 2
    assert all(row["status"] == "failed" for row in failed)


def test_collect_repair_history() -> None:
    result = {
        "auto_repair_history": [
            {"action": "substitute", "failure_class": "tool_missing"},
            {"action": "replan", "failure_class": "runtime_step_failure"},
        ]
    }

    history = _collect_repair_history(result)

    assert len(history) == 2
    assert history[0]["action"] == "substitute"
    assert history[1]["failure_class"] == "runtime_step_failure"


def test_heuristic_analysis_proposes_repeated_tool_and_analysis(mock_runs_dir: Path) -> None:
    failed = _collect_failed_runs(mock_runs_dir)
    proposals = _heuristic_analysis(failed)

    tool_names = [proposal["name"] for proposal in proposals if proposal["scope"] == "tool"]
    analysis_names = [proposal["name"] for proposal in proposals if proposal["scope"] == "analysis"]

    assert "star_align" in tool_names
    assert "rna_seq_differential_expression" in analysis_names


def test_heuristic_analysis_ignores_single_failures(tmp_path: Path) -> None:
    run1 = tmp_path / "run1"
    run1.mkdir()
    (run1 / "result.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "failure_class": "runtime_step_failure",
                "failed_step_number": 0,
                "final_plan": {"plan": [{"tool_name": "fastqc_run", "arguments": {}}]},
            }
        ),
        encoding="utf-8",
    )

    proposals = _heuristic_analysis(_collect_failed_runs(tmp_path))
    tool_names = [proposal["name"] for proposal in proposals if proposal["scope"] == "tool"]

    assert "fastqc_run" not in tool_names


def test_main_write_updates_catalog(tmp_path: Path, mock_runs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_path = tmp_path / "repair_advisories.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "trace_driven_improvement.py",
            str(mock_runs_dir),
            "--catalog-path",
            str(catalog_path),
            "--write",
        ],
    )

    assert main() == 0

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert "tool_advisories" in payload
    assert "star_align" in payload["tool_advisories"]
