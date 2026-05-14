"""Shared helpers for pre-execution validation phases.

These helpers keep the plan-validation mixin focused on phase orchestration
while centralizing strict error formatting, repair-event payload construction,
and protocol-normalization state inspection.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Collection

PlanDict = dict[str, Any]
AppendEventFn = Callable[..., None]


@dataclass(frozen=True, slots=True)
class ProtocolNormalizationSnapshot:
    """Summarize the inputs that drive protocol normalization policy."""

    analysis_type: str
    protocol_source_files: tuple[str, ...]
    has_grounding: bool
    has_compiler: bool
    validation_passed: bool


def build_protocol_normalization_snapshot(
    analysis_spec: PlanDict,
    *,
    protocol_validation: PlanDict,
    template_compiler_types: Collection[str],
) -> ProtocolNormalizationSnapshot:
    """Build the protocol-normalization policy snapshot for one plan."""

    grounding = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    protocol_source_files = tuple(
        str(item)
        for item in (grounding.get("source_files", []) or [])
        if str(item).strip()
    )
    analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip()
    has_grounding = bool(grounding)
    has_compiler = analysis_type in set(template_compiler_types)
    validation_passed = bool(protocol_validation.get("passed", False))
    return ProtocolNormalizationSnapshot(
        analysis_type=analysis_type,
        protocol_source_files=protocol_source_files,
        has_grounding=has_grounding,
        has_compiler=has_compiler,
        validation_passed=validation_passed,
    )


def protocol_normalization_debug_message(
    snapshot: ProtocolNormalizationSnapshot,
) -> str:
    """Format the debug message for one protocol-normalization policy check."""

    return (
        "[DEBUG] Protocol normalization check: "
        f"has_grounding={snapshot.has_grounding}, "
        f"has_compiler={snapshot.has_compiler}, "
        f"validation_passed={snapshot.validation_passed}, "
        f"analysis_type={snapshot.analysis_type}"
    )


def should_attempt_protocol_normalization(
    snapshot: ProtocolNormalizationSnapshot,
    *,
    normalization_enabled: bool,
) -> bool:
    """Return whether deterministic protocol normalization should run."""

    if not normalization_enabled:
        return False
    if not (snapshot.has_grounding or snapshot.has_compiler):
        return False
    return snapshot.validation_passed or snapshot.has_compiler


def append_repair_applied_event(
    *,
    append_event: AppendEventFn,
    run: PlanDict,
    failure_class: str,
    action: str,
    details: PlanDict,
    severity: str = "warning",
) -> None:
    """Append a standard `REPAIR_APPLIED` event to the run timeline."""

    append_event(
        step_id=None,
        agent="RecoveryAgent",
        event_type="REPAIR_APPLIED",
        severity=severity,
        payload={
            "run_id": run.get("run_uid", ""),
            "failure_class": failure_class,
            "attempt": 0,
            "action": action,
            "details": details,
        },
    )


def format_strict_protocol_grounding_error(validation: PlanDict) -> str:
    """Format the strict protocol-grounding error message."""

    return (
        "Strict LLM planning is enabled and planner output failed protocol grounding. "
        + json.dumps(validation, ensure_ascii=True)
    )


def format_strict_contract_validation_error(validation: PlanDict) -> str:
    """Format the strict contract-validation error message."""

    missing_caps = list(validation.get("missing_capabilities", []))
    missing_required = list(validation.get("missing_required_tool_hints", []))
    missing_hints = list(validation.get("missing_tool_hints", []))
    direct_wrapper_issues = list(validation.get("direct_wrapper_issues", []))
    artifact_role_issues = list(validation.get("artifact_role_issues", []))
    return (
        "Strict LLM planning is enabled and planner output failed contract validation. "
        f"Missing capabilities: {missing_caps}; "
        f"missing required tool hints: {missing_required}; "
        f"missing advisory tool hints: {missing_hints}; "
        f"direct-wrapper issues: {direct_wrapper_issues}; "
        f"artifact-role issues: {artifact_role_issues}"
    )


def format_strict_semantic_validation_error(
    *,
    benchmark_policy: str,
    validation: PlanDict,
) -> str:
    """Format the strict semantic-validation error message."""

    return (
        "Strict semantic validation blocked execution for "
        f"{benchmark_policy} because planner output failed semantic validation. "
        + json.dumps(validation, ensure_ascii=True)
    )


__all__ = [
    "ProtocolNormalizationSnapshot",
    "append_repair_applied_event",
    "build_protocol_normalization_snapshot",
    "format_strict_contract_validation_error",
    "format_strict_protocol_grounding_error",
    "format_strict_semantic_validation_error",
    "protocol_normalization_debug_message",
    "should_attempt_protocol_normalization",
]
