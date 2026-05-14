"""Deterministic decision policy for completed run outputs.

This module converts post-run quality and diagnosis signals into a concise
action recommendation. It is intentionally conservative and deterministic so
the product can use it outside benchmark mode without adding opaque policy
behavior to strict benchmark paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from bio_harness.core.error_diagnosis import ErrorDiagnosis
from bio_harness.core.output_quality import QualityLevel, QualityReport
from bio_harness.core.result_interpreter import InterpretationResult

_PARAMETER_FIXABLE_METRICS = {
    "low_mapping_rate",
    "mapping_rate",
    "mean_quality",
    "short_reads",
    "pass_fraction",
    "mean_gq",
    "high_na_fraction",
    "significant_genes",
    "significant_row_count",
}
_STRUCTURAL_FAIL_METRICS = {
    "missing_required_column",
    "empty_file",
    "truncated_file",
    "unsupported_format",
}
_PARAMETER_FIXABLE_FAILURE_CLASSES = {
    "incompatible_parameters",
    "out_of_memory",
}
_ESCALATION_FAILURE_CLASSES = {
    "corrupt_input",
    "missing_dependency",
    "permission_filesystem",
    "novel_unknown",
}
_METHOD_SWITCH_MARKERS = (
    "switch method family",
    "wrong method family",
    "poor fit for this assay",
    "method family mismatch",
)


class ResultDecision(str, Enum):
    """High-level actions for completed outputs."""

    ACCEPT = "accept"
    ACCEPT_WITH_WARNING = "accept_with_warning"
    RERUN_PARAMETER_CHANGE = "rerun_with_parameter_change"
    SWITCH_METHOD_FAMILY = "switch_method_family"
    ESCALATE_TO_RESEARCHER = "escalate_to_researcher"


@dataclass(frozen=True)
class ResultDecisionOutcome:
    """Decision payload for one completed run.

    Attributes:
        decision: Recommended next action.
        rationale: Plain-language explanation for the decision.
        fail_metric_names: Metric names that directly triggered the decision.
        warning_metric_names: Warning-level metric names observed.
        review_signals: Free-form review notes consulted by the policy.
    """

    decision: ResultDecision
    rationale: str
    fail_metric_names: tuple[str, ...]
    warning_metric_names: tuple[str, ...]
    review_signals: tuple[str, ...]


def decide_result(
    quality_reports: Sequence[QualityReport],
    *,
    interpretation: InterpretationResult | None = None,
    diagnoses: Sequence[ErrorDiagnosis] | None = None,
    review_signals: Iterable[str] = (),
) -> ResultDecisionOutcome:
    """Convert quality and diagnosis outputs into a next-action decision.

    Args:
        quality_reports: Completed output quality reports.
        interpretation: Optional interpretation summary.
        diagnoses: Optional structured diagnoses relevant to the run.
        review_signals: Optional free-form review notes or review-skill outputs.

    Returns:
        Deterministic result-decision outcome.
    """

    diagnoses = list(diagnoses or [])
    signals = [str(signal).strip() for signal in review_signals if str(signal).strip()]
    if interpretation is not None:
        signals.extend(str(item).strip() for item in interpretation.concerns if str(item).strip())

    if _signals_request_method_switch(signals):
        return ResultDecisionOutcome(
            decision=ResultDecision.SWITCH_METHOD_FAMILY,
            rationale="Review signals indicate the chosen method family is a poor fit for the observed outputs.",
            fail_metric_names=(),
            warning_metric_names=(),
            review_signals=tuple(signals),
        )

    fail_metric_names = tuple(
        metric.name
        for report in quality_reports
        for metric in report.metrics
        if metric.level == QualityLevel.FAIL
    )
    warning_metric_names = tuple(
        metric.name
        for report in quality_reports
        for metric in report.metrics
        if metric.level == QualityLevel.WARNING
    )

    if not quality_reports:
        return ResultDecisionOutcome(
            decision=ResultDecision.ESCALATE_TO_RESEARCHER,
            rationale="No quality reports were available, so the run should be reviewed manually before acceptance.",
            fail_metric_names=(),
            warning_metric_names=(),
            review_signals=tuple(signals),
        )

    if fail_metric_names:
        if _has_structural_failure(fail_metric_names):
            return ResultDecisionOutcome(
                decision=ResultDecision.ESCALATE_TO_RESEARCHER,
                rationale="Structural output problems were detected, so the run should not be accepted automatically.",
                fail_metric_names=fail_metric_names,
                warning_metric_names=warning_metric_names,
                review_signals=tuple(signals),
            )
        if _has_parameter_fixable_failure(fail_metric_names, diagnoses):
            return ResultDecisionOutcome(
                decision=ResultDecision.RERUN_PARAMETER_CHANGE,
                rationale="Quality failures look parameter-fixable, so the next step should be a constrained rerun with adjusted settings.",
                fail_metric_names=fail_metric_names,
                warning_metric_names=warning_metric_names,
                review_signals=tuple(signals),
            )
        if _has_escalation_diagnosis(diagnoses):
            return ResultDecisionOutcome(
                decision=ResultDecision.ESCALATE_TO_RESEARCHER,
                rationale="Diagnosed failures indicate a researcher should inspect the outputs and inputs directly.",
                fail_metric_names=fail_metric_names,
                warning_metric_names=warning_metric_names,
                review_signals=tuple(signals),
            )
        return ResultDecisionOutcome(
            decision=ResultDecision.ESCALATE_TO_RESEARCHER,
            rationale="Fail-level quality issues remain unresolved, so the run should be escalated rather than silently accepted.",
            fail_metric_names=fail_metric_names,
            warning_metric_names=warning_metric_names,
            review_signals=tuple(signals),
        )

    if warning_metric_names:
        return ResultDecisionOutcome(
            decision=ResultDecision.ACCEPT_WITH_WARNING,
            rationale="The run passed core quality checks but carries warnings that should be surfaced to the researcher.",
            fail_metric_names=(),
            warning_metric_names=warning_metric_names,
            review_signals=tuple(signals),
        )

    return ResultDecisionOutcome(
        decision=ResultDecision.ACCEPT,
        rationale="The inspected outputs passed deterministic quality checks with no warning-level findings.",
        fail_metric_names=(),
        warning_metric_names=(),
        review_signals=tuple(signals),
    )


def _signals_request_method_switch(signals: Sequence[str]) -> bool:
    """Return whether review signals explicitly request a method-family switch."""

    lowered = " ".join(str(signal).lower() for signal in signals)
    return any(marker in lowered for marker in _METHOD_SWITCH_MARKERS)


def _has_structural_failure(metric_names: Sequence[str]) -> bool:
    """Return whether any fail metric is structural rather than tunable."""

    return any(name in _STRUCTURAL_FAIL_METRICS for name in metric_names)


def _has_parameter_fixable_failure(
    metric_names: Sequence[str],
    diagnoses: Sequence[ErrorDiagnosis],
) -> bool:
    """Return whether failures look fixable by a constrained parameter rerun."""

    if any(name in _PARAMETER_FIXABLE_METRICS for name in metric_names):
        return True
    return any(d.failure_class in _PARAMETER_FIXABLE_FAILURE_CLASSES for d in diagnoses)


def _has_escalation_diagnosis(diagnoses: Sequence[ErrorDiagnosis]) -> bool:
    """Return whether diagnoses require researcher escalation."""

    return any(d.failure_class in _ESCALATION_FAILURE_CLASSES for d in diagnoses)


__all__ = [
    "ResultDecision",
    "ResultDecisionOutcome",
    "decide_result",
]
