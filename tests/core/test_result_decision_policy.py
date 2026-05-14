from __future__ import annotations

from bio_harness.core.error_diagnosis import ErrorDiagnosis
from bio_harness.core.output_quality import QualityLevel, QualityMetric, QualityReport
from bio_harness.core.result_decision_policy import ResultDecision, decide_result
from bio_harness.core.result_interpreter import InterpretationResult


def _report(*metrics: QualityMetric) -> QualityReport:
    overall = QualityLevel.PASS
    if any(metric.level == QualityLevel.FAIL for metric in metrics):
        overall = QualityLevel.FAIL
    elif any(metric.level == QualityLevel.WARNING for metric in metrics):
        overall = QualityLevel.WARNING
    return QualityReport(
        path="/tmp/output.tsv",
        file_type="tsv",
        metrics=tuple(metrics),
        overall_level=overall,
        summary="test",
    )


def _metric(name: str, level: QualityLevel) -> QualityMetric:
    return QualityMetric(
        name=name,
        value=1.0,
        level=level,
        message=f"{name}={level.value}",
        threshold="test",
    )


def test_decide_result_accepts_clean_reports() -> None:
    outcome = decide_result([_report(_metric("row_count", QualityLevel.PASS))])

    assert outcome.decision == ResultDecision.ACCEPT


def test_decide_result_accepts_with_warning_when_only_warnings_exist() -> None:
    outcome = decide_result([_report(_metric("duplicate_rate", QualityLevel.WARNING))])

    assert outcome.decision == ResultDecision.ACCEPT_WITH_WARNING
    assert outcome.warning_metric_names == ("duplicate_rate",)


def test_decide_result_reruns_when_failures_are_parameter_fixable() -> None:
    outcome = decide_result([_report(_metric("low_mapping_rate", QualityLevel.FAIL))])

    assert outcome.decision == ResultDecision.RERUN_PARAMETER_CHANGE


def test_decide_result_escalates_on_structural_failures() -> None:
    outcome = decide_result([_report(_metric("missing_required_column", QualityLevel.FAIL))])

    assert outcome.decision == ResultDecision.ESCALATE_TO_RESEARCHER


def test_decide_result_uses_review_signals_for_method_switch() -> None:
    interpretation = InterpretationResult(
        analysis_type="rna_seq",
        metrics_summary={},
        interpretation="summary",
        concerns=("Switch method family; transcript quantifier is a poor fit for this assay.",),
        model_used="template",
    )

    outcome = decide_result(
        [_report(_metric("row_count", QualityLevel.PASS))],
        interpretation=interpretation,
    )

    assert outcome.decision == ResultDecision.SWITCH_METHOD_FAMILY


def test_decide_result_escalates_when_diagnosis_requires_manual_review() -> None:
    diagnosis = ErrorDiagnosis(
        tool_name="samtools",
        failure_class="corrupt_input",
        root_cause="Input file is corrupt.",
        suggested_fix="Restage the input.",
        confidence="high",
        diagnosed_by="heuristic",
    )

    outcome = decide_result(
        [_report(_metric("variant_count", QualityLevel.FAIL))],
        diagnoses=[diagnosis],
    )

    assert outcome.decision == ResultDecision.ESCALATE_TO_RESEARCHER
