"""Deterministic post-run review helpers for completed outputs.

This module combines interpretation, output-quality inspection, and decision
policy into one shared review object. It is intended for researcher-facing
reporting and completed-run follow-ups rather than benchmark execution control.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from bio_harness.core.error_diagnosis import ErrorDiagnosis
from bio_harness.core.output_catalog import build_output_catalog
from bio_harness.core.output_quality import (
    QualityLevel,
    QualityMetric,
    QualityReport,
    assess_output_quality,
)
from bio_harness.core.output_semantic_features import extract_single_cell_fragmentation_features
from bio_harness.core.result_decision_policy import ResultDecisionOutcome, decide_result
from bio_harness.core.result_interpreter import InterpretationResult, interpret_run_results
from bio_harness.core.tabular_io import load_delimited_dict_rows


@dataclass(frozen=True)
class RunResultReview:
    """Combined deterministic review for one completed run or artifact directory.

    Attributes:
        analysis_type: Analysis type used for interpretation and quality rules.
        interpretation: Plain-English scientist-facing interpretation.
        quality_reports: Per-artifact quality reports collected from outputs.
        decision: Deterministic next-action recommendation.
    """

    analysis_type: str
    interpretation: InterpretationResult
    quality_reports: tuple[QualityReport, ...]
    decision: ResultDecisionOutcome


def review_run_results(
    selected_dir: Path,
    analysis_type: str,
    plan: dict[str, Any],
    *,
    llm: Any | None = None,
    diagnoses: Sequence[ErrorDiagnosis] | None = None,
    review_signals: Iterable[str] = (),
    step_statuses: list[str] | None = None,
) -> RunResultReview:
    """Review completed outputs into one deterministic interpretation and action.

    Args:
        selected_dir: Directory containing completed outputs.
        analysis_type: Analysis type for the reviewed outputs.
        plan: Final structured plan for the run.
        llm: Optional summarization model for interpretation text.
        diagnoses: Optional structured diagnoses relevant to the reviewed run.
        review_signals: Optional extra review notes.
        step_statuses: Optional step statuses aligned to the plan.

    Returns:
        Shared review object for reporting and completed-run explanations.
    """

    interpretation = interpret_run_results(
        selected_dir,
        analysis_type,
        plan,
        llm=llm,
    )
    quality_reports = _collect_quality_reports(
        selected_dir,
        analysis_type=analysis_type,
        plan=plan,
        step_statuses=step_statuses,
    )
    decision = decide_result(
        _decision_quality_reports(quality_reports),
        interpretation=interpretation,
        diagnoses=diagnoses,
        review_signals=review_signals,
    )
    return RunResultReview(
        analysis_type=str(analysis_type or ""),
        interpretation=interpretation,
        quality_reports=quality_reports,
        decision=decision,
    )


def result_review_to_json(review: RunResultReview) -> dict[str, Any]:
    """Serialize a run result review into JSON-friendly primitives."""

    return asdict(review)


def result_review_to_markdown(
    review: RunResultReview,
    *,
    selected_dir: Path | None = None,
) -> str:
    """Render a researcher-facing Markdown summary for one result review."""

    lines = [
        "# Result Review",
        "",
        f"- Analysis type: `{review.analysis_type}`",
        f"- Recommended action: `{review.decision.decision.value}`",
        f"- Rationale: {review.decision.rationale}",
        "",
        review.interpretation.interpretation,
        "",
    ]
    if review.interpretation.concerns:
        lines.extend(
            [
                "## Interpretation Concerns",
                "",
                *[f"- {item}" for item in review.interpretation.concerns],
                "",
            ]
        )
    lines.extend(
        [
            "## Quality Review",
            "",
        ]
    )
    if review.quality_reports:
        lines.extend(
            f"- `{_quality_label(report.path, selected_dir=selected_dir)}`: "
            f"`{report.overall_level.value}` - {report.summary}"
            for report in review.quality_reports
        )
    else:
        lines.append("- No output artifacts were available for quality review.")
    if review.decision.fail_metric_names or review.decision.warning_metric_names:
        lines.extend(["", "## Decision Signals", ""])
        if review.decision.fail_metric_names:
            lines.append(
                "- Fail metrics: "
                + ", ".join(f"`{name}`" for name in review.decision.fail_metric_names)
            )
        if review.decision.warning_metric_names:
            lines.append(
                "- Warning metrics: "
                + ", ".join(f"`{name}`" for name in review.decision.warning_metric_names)
            )
    if review.decision.review_signals:
        lines.extend(["", "## Review Notes", ""])
        lines.extend(f"- {signal}" for signal in review.decision.review_signals)
    return "\n".join(lines)


def _collect_quality_reports(
    selected_dir: Path,
    *,
    analysis_type: str,
    plan: dict[str, Any],
    step_statuses: list[str] | None,
) -> tuple[QualityReport, ...]:
    """Collect per-artifact quality reports from a completed output directory."""

    catalog = build_output_catalog(
        selected_dir,
        plan,
        step_statuses=step_statuses,
        analysis_type=analysis_type,
    )
    reports = [
        assess_output_quality(
            Path(entry.path),
            tool_name=entry.tool_name,
            analysis_type=analysis_type,
        )
        for entry in catalog.entries
        if entry.review_action in {"assess_quality", "summarize_only"}
    ]
    reports.extend(
        _collect_cross_artifact_quality_reports(
            catalog.reviewable_entries,
            selected_dir=selected_dir,
            analysis_type=analysis_type,
        )
    )
    return tuple(reports)


def _decision_quality_reports(
    quality_reports: Sequence[QualityReport],
) -> tuple[QualityReport, ...]:
    """Return only reports that can drive a deterministic decision."""

    return tuple(
        report
        for report in quality_reports
        if report.overall_level != QualityLevel.SKIP
    )


def _collect_cross_artifact_quality_reports(
    catalog_entries: Sequence[Any],
    *,
    selected_dir: Path,
    analysis_type: str,
) -> tuple[QualityReport, ...]:
    """Collect semantic reports that require multiple artifacts together."""

    if "single_cell" not in str(analysis_type or "").lower():
        return ()
    clusters_path = None
    markers_path = None
    for entry in catalog_entries:
        candidate = Path(entry.path)
        if not candidate.exists() or candidate.suffix.lower() not in {".csv", ".tsv"}:
            continue
        table_kind = _classify_single_cell_table(candidate)
        if table_kind == "clusters" and clusters_path is None:
            clusters_path = candidate
        elif table_kind == "markers" and markers_path is None:
            markers_path = candidate
    if not clusters_path or not markers_path:
        return ()

    features = extract_single_cell_fragmentation_features(clusters_path, markers_path)
    if features is None:
        return ()

    metrics = [
        QualityMetric(
            name="cluster_to_cell_ratio",
            value=features.cluster_to_cell_ratio,
            level=QualityLevel.PASS,
            message=(
                f"Single-cell clustering assigns {features.cluster_count} clusters "
                f"across {features.cell_count} cells."
            ),
            threshold="informational",
        ),
        QualityMetric(
            name="singleton_cluster_fraction",
            value=features.singleton_cluster_fraction,
            level=QualityLevel.PASS,
            message=(
                "Singleton clusters account for "
                f"{features.singleton_cluster_fraction:.1%} of observed clusters."
            ),
            threshold="informational",
        ),
    ]
    if features.marker_clusters_missing_from_assignments:
        metrics.append(
            QualityMetric(
                name="marker_cluster_assignment_mismatch",
                value=float(len(features.marker_clusters_missing_from_assignments)),
                level=QualityLevel.FAIL,
                message=(
                    "Marker table references clusters missing from assignments: "
                    + ", ".join(features.marker_clusters_missing_from_assignments)
                    + "."
                ),
                threshold="marker clusters missing from assignments -> fail",
            )
        )
    fragmentation_fail = (
        features.cell_count >= 8
        and features.cluster_to_cell_ratio >= 0.75
        and features.singleton_cluster_fraction >= 0.5
        and features.median_cluster_size <= 1.5
    )
    metrics.append(
        QualityMetric(
            name="implausible_cluster_fragmentation",
            value=features.cluster_to_cell_ratio,
            level=QualityLevel.FAIL if fragmentation_fail else QualityLevel.PASS,
            message=(
                "Single-cell clustering is implausibly fragmented for the observed number of cells."
                if fragmentation_fail
                else "Single-cell clustering granularity is plausible for the observed number of cells."
            ),
            threshold=(
                "cell_count >= 8 and cluster_to_cell_ratio >= 0.75 and "
                "singleton_cluster_fraction >= 0.50 and median_cluster_size <= 1.5 -> fail"
            ),
        )
    )
    overall = _semantic_overall_level(metrics)
    summary = (
        f"Single-cell semantic review {overall.value}: "
        f"{features.cell_count} cells, {features.cluster_count} clusters, "
        f"{features.singleton_cluster_fraction:.1%} singleton clusters."
    )
    return (
        QualityReport(
            path=str((Path(selected_dir).expanduser() / "single_cell_semantic_review").resolve(strict=False)),
            file_type="single_cell_semantic",
            metrics=tuple(metrics),
            overall_level=overall,
            summary=summary,
        ),
    )


def _classify_single_cell_table(path: Path) -> str:
    """Return ``clusters`` or ``markers`` when a table looks like a known SC output."""

    try:
        columns, _rows, _delimiter = load_delimited_dict_rows(path)
    except Exception:
        return ""
    lower_columns = {column.lower() for column in columns}
    if "cluster" not in lower_columns:
        return ""
    if lower_columns.intersection({"cell_id", "cell", "barcode", "barcodes", "obs_name"}):
        return "clusters"
    if "gene" in lower_columns and lower_columns.intersection({"pval_adj", "padj", "score", "log2fc", "logfc"}):
        return "markers"
    return ""


def _semantic_overall_level(metrics: Sequence[QualityMetric]) -> QualityLevel:
    """Return the worst level represented in a semantic metric list."""

    if any(metric.level == QualityLevel.FAIL for metric in metrics):
        return QualityLevel.FAIL
    if any(metric.level == QualityLevel.WARNING for metric in metrics):
        return QualityLevel.WARNING
    return QualityLevel.PASS


def _quality_label(path_text: str, *, selected_dir: Path | None) -> str:
    """Return a selected-dir-relative quality label when possible."""

    report_path = Path(path_text).expanduser().resolve(strict=False)
    if selected_dir is None:
        return report_path.name
    try:
        base = Path(selected_dir).expanduser().resolve(strict=False)
        return str(report_path.relative_to(base))
    except Exception:
        return report_path.name


__all__ = [
    "RunResultReview",
    "result_review_to_json",
    "result_review_to_markdown",
    "review_run_results",
]
