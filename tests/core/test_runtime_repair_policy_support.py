from __future__ import annotations

from scripts.run_agent_e2e_runtime_repair_policy_support import (
    apply_runtime_mutation_repair_ladder,
    direct_skill_smoke_guard,
    unrecoverable_signature_guard,
)


def test_direct_skill_smoke_guard_blocks_runtime_repair() -> None:
    repaired, action, details = direct_skill_smoke_guard(
        failure_class="runtime_step_failure",
        is_direct_skill_smoke=True,
        details={"why": "repair_map:runtime_step_failure"},
    )

    assert repaired is False
    assert action == "direct_skill_smoke_repair_disabled"
    assert details["failure_class"] == "runtime_step_failure"


def test_unrecoverable_signature_guard_blocks_zero_count_signature() -> None:
    repaired, action, details = unrecoverable_signature_guard(
        signatures={"deseq2_all_zero_counts"},
        details={"why": "repair_map:runtime_step_failure"},
    )

    assert repaired is False
    assert action == "unrecoverable_bad_input"
    assert details["failure_signatures"] == ["deseq2_all_zero_counts"]


def test_unrecoverable_signature_guard_blocks_spatial_coordinate_signature() -> None:
    repaired, action, details = unrecoverable_signature_guard(
        signatures={"spatial_coordinates_invalid"},
        details={"why": "repair_map:runtime_step_failure"},
    )

    assert repaired is False
    assert action == "unrecoverable_bad_input"
    assert details["failure_signatures"] == ["spatial_coordinates_invalid"]


def test_unrecoverable_signature_guard_blocks_format_input_error_marker() -> None:
    repaired, action, details = unrecoverable_signature_guard(
        signatures={"format_input_error_marker"},
        details={"why": "repair_map:runtime_step_failure"},
    )

    assert repaired is False
    assert action == "unrecoverable_bad_input"
    assert details["failure_signatures"] == ["format_input_error_marker"]


def test_apply_runtime_mutation_repair_ladder_returns_first_success() -> None:
    calls: list[str] = []

    repaired, action, details = apply_runtime_mutation_repair_ladder(
        failure_class="runtime_step_failure",
        details={"why": "repair_map:runtime_step_failure"},
        runtime_plan_mutation_guard=lambda _failure_class: {"allowed": True},
        repair_steps=[
            ("first", lambda: (False, {"attempt": "first"})),
            ("second", lambda: calls.append("second") or (True, {"fixed": True})),
        ],
    )

    assert repaired is True
    assert action == "second"
    assert details["fixed"] is True
    assert calls == ["second"]


def test_apply_runtime_mutation_repair_ladder_merges_guard_when_blocked() -> None:
    repaired, action, details = apply_runtime_mutation_repair_ladder(
        failure_class="validation_block",
        details={"why": "repair_map:validation_block"},
        runtime_plan_mutation_guard=lambda _failure_class: {
            "allowed": False,
            "benchmark_policy": "bioagentbench_planning_strict",
        },
        repair_steps=[],
    )

    assert repaired is False
    assert action == ""
    assert details["benchmark_policy"] == "bioagentbench_planning_strict"
