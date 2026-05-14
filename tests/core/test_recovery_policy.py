from __future__ import annotations

from pathlib import Path

import pytest

from bio_harness.core.recovery_policy import (
    build_repair_audit_entry,
    can_attempt_repair,
    classify_failure_with_context,
    classify_failure,
    max_attempts_for_class,
)
from bio_harness.core.step_completion import write_completion_manifest


def test_validation_block_classification_supports_auto_repair_path():
    run = {
        "error": "Step 5 blocked by validation agent.",
        "validation_block_detected": True,
    }
    failure_class = classify_failure(run)
    assert failure_class == "validation_block"
    assert can_attempt_repair({}, failure_class) is True


def test_repair_attempt_limits_and_audit_entry():
    attempts = {"validation_block": 1}
    assert max_attempts_for_class("validation_block") == 1
    assert can_attempt_repair(attempts, "validation_block") is False

    audit = build_repair_audit_entry(
        run_id="run_123",
        failure_class="runtime_step_failure",
        attempt=2,
        action="replan_with_failure_context",
        details={
            "why": "Replanned with failure context.",
            "diff_summary": {"before_step_count": 5, "after_step_count": 6},
        },
    )
    assert audit["run_id"] == "run_123"
    assert audit["failure_class"] == "runtime_step_failure"
    assert audit["attempt"] == 2
    assert audit["patch_audit"]["run_id"] == "run_123"
    assert audit["patch_audit"]["diff_summary"]["after_step_count"] == 6


# ---------------------------------------------------------------------------
# classify_failure — parametrized runtime error patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run",
    [
        pytest.param(
            {"error": "Execution stalled for 1200s without executor progress."},
            id="execution_stall",
        ),
        pytest.param(
            {
                "error": "Execution stalled for 640s without executor progress.",
                "missing_sample_groups": ["control", "treatment"],
                "execution_stalled_detected": True,
            },
            id="stall_overrides_missing_groups",
        ),
        pytest.param(
            {
                "error": "Planner request timed out while waiting for model output.",
                "planner_timeout_detected": True,
            },
            id="planner_timeout",
        ),
        pytest.param(
            {
                "error": "Step 2 (bash_run) failed with exit code 127.",
                "step_statuses": ["completed", "failed"],
                "missing_sample_groups": ["control", "treatment"],
            },
            id="failed_step_outweighs_missing_groups",
        ),
    ],
)
def test_classify_failure_as_runtime_step_failure(run):
    assert classify_failure(run) == "runtime_step_failure"


def test_classify_failure_prefers_format_input_error_over_failed_step() -> None:
    run = {
        "error": "Step 1 failed with exit code 2. Input validation issue: Spatial coordinates contain missing or non-finite values.",
        "format_input_error_detected": True,
        "step_statuses": ["failed"],
    }

    assert classify_failure(run) == "format_input_error"


def test_classify_failure_with_context_ignores_failed_directory_only_artifact(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "assembly"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_completion_manifest(
        output_dir,
        tool_name="flye_assemble",
        outputs=[],
        exit_code=125,
        success=False,
        error="launcher failed",
    )
    plan = {
        "plan": [
            {
                "tool_name": "flye_assemble",
                "arguments": {"output_dir": str(output_dir)},
            }
        ]
    }
    result = classify_failure_with_context(
        {
            "error": "Step 1 (flye_assemble) failed with exit code 125.",
            "failed_tool_name": "flye_assemble",
            "failed_step_idx": 0,
            "step_statuses": ["failed"],
        },
        selected_dir=tmp_path,
        plan=plan,
    )

    assert result["recovery_strategy"] != "skip_step_use_artifact"
    assert result["existing_artifacts"] == []
