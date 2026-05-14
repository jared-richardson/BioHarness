from __future__ import annotations

from pathlib import Path

from bio_harness.agents.orchestrator import Orchestrator
from bio_harness.harness.plan_helpers import _is_actionable_executable_plan


def _orchestrator_stub() -> Orchestrator:
    return Orchestrator.__new__(Orchestrator)


def test_normalize_plan_json_fills_defaults():
    orchestrator = _orchestrator_stub()

    normalized = orchestrator._normalize_plan_json({})

    assert normalized["thought_process"] == "No thought process provided by model."
    assert normalized["plan"] == []
    assert normalized["final_deliverables"] == []


def test_extract_step_contracts_reads_expected_files_and_deliverables():
    orchestrator = _orchestrator_stub()
    plan = {
        "plan": [
            {"step_id": 1, "expected_files": ["a.txt"], "validation_method": "non_empty"},
            {"step_id": 2, "deliverables": ["b.txt"]},
            {"step_id": "x", "expected_files": ["ignored.txt"]},
        ]
    }

    contracts = orchestrator._extract_step_contracts(plan)

    assert contracts == {
        1: {
            "expected_files": ["a.txt"],
            "validation_method": "non_empty",
            "success_criteria": "",
        },
        2: {
            "expected_files": ["b.txt"],
            "validation_method": "exists_non_empty",
            "success_criteria": "",
        },
    }


def test_validate_deliverables_passes_for_non_empty_relative_file(tmp_path: Path):
    orchestrator = _orchestrator_stub()
    output = tmp_path / "out.txt"
    output.write_text("ok\n", encoding="utf-8")

    result = orchestrator._validate_deliverables({"expected_files": ["out.txt"]}, str(tmp_path))

    assert result["passed"] is True
    assert result["reason"] == "ok"


def test_validate_deliverables_fails_for_missing_file(tmp_path: Path):
    orchestrator = _orchestrator_stub()

    result = orchestrator._validate_deliverables({"expected_files": ["missing.txt"]}, str(tmp_path))

    assert result["passed"] is False
    assert result["reason"].startswith("missing:")


def test_validate_deliverables_fails_for_empty_file(tmp_path: Path):
    orchestrator = _orchestrator_stub()
    output = tmp_path / "out.txt"
    output.write_text("", encoding="utf-8")

    result = orchestrator._validate_deliverables(
        {"expected_files": ["out.txt"], "validation_method": "exists_non_empty"},
        str(tmp_path),
    )

    assert result["passed"] is False
    assert result["reason"].startswith("empty:")


def test_is_actionable_executable_plan_rejects_output_free_inspection_bash() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "cd /tmp/run && "
                        "bcftools view -h evol1_raw_calls.vcf.gz | grep INFO && "
                        "bcftools view -H evol1_raw_calls.vcf.gz | head -n 20"
                    )
                },
            }
        ]
    }

    assert _is_actionable_executable_plan(plan) is False


def test_is_actionable_executable_plan_accepts_bash_with_real_outputs() -> None:
    plan = {
        "plan": [
            {
                "tool_name": "bash_run",
                "arguments": {
                    "command": (
                        "cd /tmp/run && "
                        "bcftools view -Oz -o filtered.vcf.gz evol1_raw_calls.vcf.gz && "
                        "tabix -p vcf filtered.vcf.gz"
                    )
                },
            }
        ]
    }

    assert _is_actionable_executable_plan(plan) is True
