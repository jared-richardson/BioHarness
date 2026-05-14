from __future__ import annotations

from scripts.run_agent_e2e_preexecution_repair_support import (
    adopt_preexecution_candidate_if_valid,
    protocol_repair_strategy,
)


def test_protocol_repair_strategy_prefers_first_named_strategy() -> None:
    strategy = protocol_repair_strategy(
        {"repairs": [{"strategy": ""}, {"strategy": "template_guided_patch"}]}
    )

    assert strategy == "template_guided_patch"


def test_protocol_repair_strategy_defaults_when_missing() -> None:
    assert protocol_repair_strategy({"repairs": []}) == "guided_patch"


def test_adopt_preexecution_candidate_if_valid_installs_valid_plan() -> None:
    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "fastqc_run",
                    "arguments": {"input_file": "reads.fastq.gz", "output_dir": "old_qc"},
                    "step_id": 1,
                }
            ]
        }
    }
    candidate = {
        "plan": [
            {
                "tool_name": "fastqc_run",
                "arguments": {"input_file": "reads.fastq.gz", "output_dir": "new_qc"},
                "step_id": 1,
            },
            {
                "tool_name": "fastqc_run",
                "arguments": {"input_file": "reads_2.fastq.gz", "output_dir": "new_qc_2"},
                "step_id": 2,
            },
        ]
    }

    result = adopt_preexecution_candidate_if_valid(
        run=run,
        candidate=candidate,
        normalize_plan_for_execution=lambda plan: (
            plan,
            {"changed": False},
            {"changed": False},
        ),
        validate_plan=lambda plan: {"passed": True, "plan_tools": [step["tool_name"] for step in plan["plan"]]},
        mark_planned=True,
        clear_error=True,
        include_diff_summary=True,
    )

    assert result is not None
    assert run["plan"] == candidate
    assert result["validation_after"]["passed"] is True
    assert result["diff_summary"]["before_step_count"] == 1
    assert result["diff_summary"]["after_step_count"] == 2


def test_adopt_preexecution_candidate_if_valid_rejects_failed_validation() -> None:
    run = {
        "plan": {
            "plan": [
                {
                    "tool_name": "fastqc_run",
                    "arguments": {"input_file": "reads.fastq.gz", "output_dir": "old_qc"},
                    "step_id": 1,
                }
            ]
        }
    }
    candidate = {
        "plan": [
            {
                "tool_name": "fastqc_run",
                "arguments": {"input_file": "reads.fastq.gz", "output_dir": "new_qc"},
                "step_id": 1,
            }
        ]
    }

    result = adopt_preexecution_candidate_if_valid(
        run=run,
        candidate=candidate,
        normalize_plan_for_execution=lambda plan: (
            plan,
            {"changed": True},
            {"changed": False},
        ),
        validate_plan=lambda _plan: {"passed": False, "issues": ["bad"]},
        mark_planned=False,
        clear_error=False,
        include_diff_summary=False,
    )

    assert result is None
    assert run["plan"]["plan"][0]["arguments"]["output_dir"] == "old_qc"
