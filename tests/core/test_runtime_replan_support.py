from __future__ import annotations

from scripts.run_agent_e2e_runtime_replan_support import (
    evaluate_runtime_replan_candidate,
)


def _kw_only_prune_candidate(plan, *, failure_class, before_steps):
    return plan, {"step_growth": 0, "heavy_reintroduced": False}


def test_evaluate_runtime_replan_candidate_applies_valid_candidate() -> None:
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

    evaluation = evaluate_runtime_replan_candidate(
        run=run,
        candidate=candidate,
        failure_class="runtime_step_failure",
        focus_mode="step_local",
        attempt_num=1,
        strategy="runtime_repair_runtime_step_failure_step_local_1",
        before_steps=1,
        data_root="/tmp/data",
        selected_dir="/tmp/workspace",
        canonicalize_plan=lambda plan, data_root: (plan, {"changed": False}),
        prune_candidate=_kw_only_prune_candidate,
        missing_scripts_for_plan=lambda plan, selected_dir: [],
        assess_contract=lambda plan: {"passed": True, "missing_capabilities": [], "missing_tool_hints": []},
        apply_repaired_plan_with_resume=lambda run, plan: {
            "resume_idx": 0,
            "preserved_completed_steps": 0,
        },
    )

    assert evaluation.applied is True
    assert evaluation.attempt_row["status"] == "applied"
    assert evaluation.details["repair_focus_mode"] == "step_local"
    assert evaluation.details["diff_summary"]["after_step_count"] == 1


def test_evaluate_runtime_replan_candidate_rejects_contract_failure() -> None:
    run = {"plan": {"plan": []}}
    candidate = {
        "plan": [
            {
                "tool_name": "fastqc_run",
                "arguments": {"input_file": "reads.fastq.gz", "output_dir": "new_qc"},
                "step_id": 1,
            }
        ]
    }

    evaluation = evaluate_runtime_replan_candidate(
        run=run,
        candidate=candidate,
        failure_class="runtime_step_failure",
        focus_mode="full_plan",
        attempt_num=3,
        strategy="runtime_repair_runtime_step_failure_full_plan_3",
        before_steps=0,
        data_root="/tmp/data",
        selected_dir="/tmp/workspace",
        canonicalize_plan=lambda plan, data_root: (plan, {"changed": False}),
        prune_candidate=_kw_only_prune_candidate,
        missing_scripts_for_plan=lambda plan, selected_dir: [],
        assess_contract=lambda plan: {
            "passed": False,
            "missing_capabilities": ["alignment"],
        },
        apply_repaired_plan_with_resume=lambda run, plan: {"resume_idx": 0},
    )

    assert evaluation.applied is False
    assert evaluation.attempt_row["reason"] == "contract_validation_failed"
    assert evaluation.validation["missing_capabilities"] == ["alignment"]


def test_evaluate_runtime_replan_candidate_rejects_heavy_reintroduced_guard() -> None:
    run = {"plan": {"plan": []}}
    candidate = {
        "plan": [
            {
                "tool_name": "fastqc_run",
                "arguments": {"input_file": "reads.fastq.gz", "output_dir": "new_qc"},
                "step_id": 1,
            }
        ]
    }

    evaluation = evaluate_runtime_replan_candidate(
        run=run,
        candidate=candidate,
        failure_class="runtime_step_failure",
        focus_mode="subgraph_local",
        attempt_num=2,
        strategy="runtime_repair_runtime_step_failure_subgraph_local_2",
        before_steps=0,
        data_root="/tmp/data",
        selected_dir="/tmp/workspace",
        canonicalize_plan=lambda plan, data_root: (plan, {"changed": False}),
        prune_candidate=lambda plan, *, failure_class, before_steps: (
            plan,
            {"step_growth": 0, "heavy_reintroduced": True},
        ),
        missing_scripts_for_plan=lambda plan, selected_dir: [],
        assess_contract=lambda plan: {"passed": True},
        apply_repaired_plan_with_resume=lambda run, plan: {"resume_idx": 0},
    )

    assert evaluation.applied is False
    assert evaluation.attempt_row["reason"] == "heavy_steps_reintroduced"
