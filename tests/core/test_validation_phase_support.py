from __future__ import annotations

from scripts.run_agent_e2e_validation_phase_support import (
    append_repair_applied_event,
    build_protocol_normalization_snapshot,
    format_strict_contract_validation_error,
    format_strict_protocol_grounding_error,
    format_strict_semantic_validation_error,
    protocol_normalization_debug_message,
    should_attempt_protocol_normalization,
)


def test_build_protocol_normalization_snapshot_collects_expected_flags() -> None:
    snapshot = build_protocol_normalization_snapshot(
        {
            "analysis_type": "rna_seq_differential_expression",
            "protocol_grounding": {"source_files": ["a.md", "", "b.md"]},
        },
        protocol_validation={"passed": True},
        template_compiler_types={"rna_seq_differential_expression"},
    )

    assert snapshot.analysis_type == "rna_seq_differential_expression"
    assert snapshot.protocol_source_files == ("a.md", "b.md")
    assert snapshot.has_grounding is True
    assert snapshot.has_compiler is True
    assert snapshot.validation_passed is True


def test_protocol_normalization_debug_message_includes_snapshot_fields() -> None:
    snapshot = build_protocol_normalization_snapshot(
        {"analysis_type": "transcript_quantification", "protocol_grounding": {}},
        protocol_validation={"passed": False},
        template_compiler_types={"transcript_quantification"},
    )

    message = protocol_normalization_debug_message(snapshot)

    assert "has_grounding=False" in message
    assert "has_compiler=True" in message
    assert "validation_passed=False" in message
    assert "analysis_type=transcript_quantification" in message


def test_should_attempt_protocol_normalization_requires_policy_and_grounding() -> None:
    snapshot = build_protocol_normalization_snapshot(
        {"analysis_type": "plain_text", "protocol_grounding": {}},
        protocol_validation={"passed": True},
        template_compiler_types={"other_type"},
    )

    assert should_attempt_protocol_normalization(snapshot, normalization_enabled=False) is False
    assert should_attempt_protocol_normalization(snapshot, normalization_enabled=True) is False


def test_append_repair_applied_event_builds_standard_payload() -> None:
    events: list[dict[str, object]] = []

    def _append_event(**kwargs: object) -> None:
        events.append(kwargs)

    append_repair_applied_event(
        append_event=_append_event,
        run={"run_uid": "run-123"},
        failure_class="semantic_validation",
        action="repair_step",
        details={"changed": True},
        severity="info",
    )

    assert events == [
        {
            "step_id": None,
            "agent": "RecoveryAgent",
            "event_type": "REPAIR_APPLIED",
            "severity": "info",
            "payload": {
                "run_id": "run-123",
                "failure_class": "semantic_validation",
                "attempt": 0,
                "action": "repair_step",
                "details": {"changed": True},
            },
        }
    ]


def test_format_strict_protocol_grounding_error_serializes_validation() -> None:
    message = format_strict_protocol_grounding_error({"passed": False, "reason": "bad"})

    assert "failed protocol grounding" in message
    assert '"reason": "bad"' in message


def test_format_strict_contract_validation_error_lists_issue_buckets() -> None:
    message = format_strict_contract_validation_error(
        {
            "missing_capabilities": ["alignment"],
            "missing_required_tool_hints": ["star_align"],
            "missing_tool_hints": ["featurecounts_run"],
            "direct_wrapper_issues": ["missing_args"],
            "artifact_role_issues": ["input_equals_output"],
        }
    )

    assert "Missing capabilities: ['alignment']" in message
    assert "missing required tool hints: ['star_align']" in message
    assert "missing advisory tool hints: ['featurecounts_run']" in message
    assert "direct-wrapper issues: ['missing_args']" in message
    assert "artifact-role issues: ['input_equals_output']" in message


def test_format_strict_semantic_validation_error_serializes_validation() -> None:
    message = format_strict_semantic_validation_error(
        benchmark_policy="bioagentbench_planning_strict",
        validation={"passed": False, "issues": [{"issue": "invented_scientific_output"}]},
    )

    assert "Strict semantic validation blocked execution" in message
    assert "bioagentbench_planning_strict" in message
    assert '"invented_scientific_output"' in message
