"""Quality-focused comparison helpers for completed run directories."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bio_harness.core.output_catalog import build_output_catalog
from bio_harness.core.output_quality import assess_output_quality


@dataclass(frozen=True)
class MetricComparison:
    """Comparison of one metric across two runs."""

    metric_name: str
    run_a_value: float | None
    run_b_value: float | None
    delta: float | None
    delta_pct: float | None
    better_run: str
    interpretation: str


@dataclass(frozen=True)
class QualityComparison:
    """Aggregate quality comparison between two runs."""

    run_a_dir: str
    run_b_dir: str
    metric_comparisons: tuple[MetricComparison, ...]
    overall_winner: str
    summary: str


def compare_run_quality(
    run_a_dir: Path,
    run_b_dir: Path,
    plan_a: dict[str, Any] | None = None,
    plan_b: dict[str, Any] | None = None,
) -> QualityComparison:
    """Compare output-quality metrics between two completed runs.

    Args:
        run_a_dir: First selected directory.
        run_b_dir: Second selected directory.
        plan_a: Optional plan for run A.
        plan_b: Optional plan for run B.

    Returns:
        Structured quality comparison.
    """

    metrics_a = _extract_run_metrics(run_a_dir, plan=plan_a)
    metrics_b = _extract_run_metrics(run_b_dir, plan=plan_b)
    comparisons: list[MetricComparison] = []
    pipeline_a = _pipeline_signature(run_a_dir)
    pipeline_b = _pipeline_signature(run_b_dir)
    comparison_keys = sorted(set(metrics_a) | set(metrics_b))
    for scope in comparison_keys:
        scope_a = metrics_a.get(scope, {})
        scope_b = metrics_b.get(scope, {})
        for metric_name in sorted(set(scope_a) | set(scope_b)):
            value_a = scope_a.get(metric_name)
            value_b = scope_b.get(metric_name)
            delta = None if value_a is None or value_b is None else float(value_b) - float(value_a)
            delta_pct = None
            if delta is not None and value_a not in (None, 0):
                delta_pct = (delta / float(value_a)) * 100.0
            better_run = _determine_better(metric_name, value_a, value_b)
            comparisons.append(
                MetricComparison(
                    metric_name=f"{scope}.{metric_name}",
                    run_a_value=value_a,
                    run_b_value=value_b,
                    delta=delta,
                    delta_pct=delta_pct,
                    better_run=better_run,
                    interpretation=_comparison_interpretation(scope, metric_name, value_a, value_b, better_run, delta),
                )
            )

    overall_winner = (
        "different_pipeline"
        if pipeline_a and pipeline_b and pipeline_a != pipeline_b
        else _overall_winner(comparisons)
    )
    summary = _comparison_summary(overall_winner, comparisons)
    return QualityComparison(
        run_a_dir=str(Path(run_a_dir).expanduser().resolve(strict=False)),
        run_b_dir=str(Path(run_b_dir).expanduser().resolve(strict=False)),
        metric_comparisons=tuple(comparisons),
        overall_winner=overall_winner,
        summary=summary,
    )


def quality_comparison_to_markdown(comparison: QualityComparison) -> str:
    """Render a quality comparison as Markdown.

    Args:
        comparison: Comparison result to render.

    Returns:
        Markdown table and summary text.
    """

    lines = [
        "# Quality Comparison",
        "",
        f"- Run A: `{comparison.run_a_dir}`",
        f"- Run B: `{comparison.run_b_dir}`",
        f"- Overall winner: `{comparison.overall_winner}`",
        f"- Summary: {comparison.summary}",
        "",
        "| Metric | Run A | Run B | Delta | Better |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for metric in comparison.metric_comparisons:
        delta_text = "" if metric.delta is None else f"{metric.delta:.3f}"
        run_a_text = "" if metric.run_a_value is None else f"{metric.run_a_value:.3f}"
        run_b_text = "" if metric.run_b_value is None else f"{metric.run_b_value:.3f}"
        lines.append(
            f"| {metric.metric_name} | {run_a_text} | {run_b_text} | {delta_text} | {metric.better_run} |"
        )
    return "\n".join(lines)


def _extract_run_metrics(
    run_dir: Path,
    plan: dict[str, Any] | None = None,
) -> dict[str, dict[str, float]]:
    """Extract comparable metrics from one run directory.

    Args:
        run_dir: Directory to inspect.
        plan: Optional plan dictionary used to improve role matching.

    Returns:
        Nested metric dictionary keyed by output scope.
    """

    catalog = build_output_catalog(run_dir, plan or {"plan": [], "final_deliverables": []})
    extracted: dict[str, dict[str, float]] = {}
    for entry in catalog.entries:
        scope = _scope_name(entry)
        if not scope:
            continue
        quality = assess_output_quality(
            Path(entry.path),
            tool_name=entry.tool_name,
            analysis_type="differential_expression" if scope == "de_results" else "",
        )
        scope_metrics = extracted.setdefault(scope, {})
        for metric in quality.metrics:
            scope_metrics.setdefault(metric.name, float(metric.value))
    _merge_result_json_metrics(extracted, run_dir)
    return extracted


def _determine_better(
    metric_name: str,
    value_a: float | None,
    value_b: float | None,
) -> str:
    """Determine which run is better for one metric.

    Args:
        metric_name: Metric name without scope prefix.
        value_a: Run A value.
        value_b: Run B value.

    Returns:
        `a`, `b`, `same`, or `unknown`.
    """

    if value_a is None or value_b is None:
        return "unknown"
    if _is_effectively_same(metric_name, value_a, value_b):
        return "same"
    if metric_name in {
        "mapping_rate",
        "pass_fraction",
        "mean_quality",
        "significant_row_count",
        "read_count",
        "total_reads",
        "variant_count",
        "completion_rate",
        "output_count",
        "steps_completed",
    }:
        return "b" if value_b > value_a else "a"
    if metric_name in {"duplicate_rate", "repairs", "elapsed_seconds"}:
        return "b" if value_b < value_a else "a"
    if metric_name == "ts_tv_ratio":
        return "b" if abs(value_b - 2.0) < abs(value_a - 2.0) else "a"
    return "unknown"


def _is_effectively_same(metric_name: str, value_a: float, value_b: float) -> bool:
    """Return whether two metric values should be treated as equivalent."""

    delta = abs(float(value_b) - float(value_a))
    if delta == 0.0:
        return True
    if metric_name in {"mapping_rate", "pass_fraction", "mean_quality", "completion_rate"}:
        return delta <= 0.02
    if metric_name == "variant_count":
        return delta <= max(3.0, max(abs(value_a), abs(value_b)) * 0.05)
    if metric_name in {"read_count", "total_reads", "steps_completed"}:
        return delta <= 2.0
    if metric_name == "output_count":
        return False
    if metric_name == "repairs":
        return delta <= 1.0
    if metric_name == "elapsed_seconds":
        return delta <= 10.0
    return False


def _scope_name(entry: Any) -> str:
    """Map a catalog entry to a comparison scope."""

    format_name = str(getattr(entry, "format", "") or "").lower()
    tool_name = str(getattr(entry, "tool_name", "") or "").lower()
    relative_path = str(getattr(entry, "relative_path", "") or "").lower()
    if format_name == "bam":
        return "alignment"
    if format_name == "vcf":
        return "variant_calling"
    if tool_name in {"deseq2_run", "edger_run", "limma_voom_run"} or "deseq" in relative_path:
        return "de_results"
    return ""


def _merge_result_json_metrics(
    extracted: dict[str, dict[str, float]],
    run_dir: Path,
) -> None:
    """Merge benchmark-friendly summary metrics from one run result file."""

    result_path = Path(run_dir) / "result.json"
    if not result_path.exists():
        return
    try:
        payload = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict):
        return

    summary_metrics = extracted.setdefault("summary", {})
    quality_metrics = payload.get("quality_metrics", {})
    if isinstance(quality_metrics, dict):
        for metric_name, raw_value in quality_metrics.items():
            try:
                summary_metrics.setdefault(str(metric_name), float(raw_value))
            except (TypeError, ValueError):
                continue

    repairs = payload.get("auto_repair_history_count")
    if repairs is not None:
        try:
            summary_metrics.setdefault("repairs", float(repairs))
        except (TypeError, ValueError):
            pass

    elapsed_seconds = payload.get("elapsed_seconds")
    if elapsed_seconds is not None:
        try:
            summary_metrics.setdefault("elapsed_seconds", float(elapsed_seconds))
        except (TypeError, ValueError):
            pass

    steps_completed = payload.get("steps_completed")
    if steps_completed is not None:
        try:
            summary_metrics.setdefault("steps_completed", float(steps_completed))
        except (TypeError, ValueError):
            pass

    steps_total = payload.get("steps_total")
    try:
        completed = float(steps_completed) if steps_completed is not None else None
        total = float(steps_total) if steps_total is not None else None
    except (TypeError, ValueError):
        completed = None
        total = None
    if completed is not None and total not in (None, 0.0):
        summary_metrics.setdefault("completion_rate", completed / total)

    outputs = payload.get("outputs", [])
    if isinstance(outputs, list):
        summary_metrics.setdefault("output_count", float(len(outputs)))


def _pipeline_signature(run_dir: Path) -> tuple[str, ...]:
    """Return a comparable tool signature from one run result file."""

    result_path = Path(run_dir) / "result.json"
    if not result_path.exists():
        return ()
    try:
        payload = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return ()
    quality_metrics = payload.get("quality_metrics", {})
    if not isinstance(quality_metrics, dict):
        return ()
    tools_used = quality_metrics.get("tools_used", [])
    if not isinstance(tools_used, list):
        return ()
    return tuple(sorted(str(tool).strip().lower() for tool in tools_used if str(tool).strip()))


def _comparison_interpretation(
    scope: str,
    metric_name: str,
    value_a: float | None,
    value_b: float | None,
    better_run: str,
    delta: float | None,
) -> str:
    """Build a short interpretation for one metric comparison."""

    if value_a is None or value_b is None:
        return f"Metric {scope}.{metric_name} is missing in one run."
    if better_run == "same":
        return f"Both runs have the same {scope}.{metric_name} value."
    if better_run == "unknown":
        return f"Metric {scope}.{metric_name} is context-dependent and was not scored."
    winner = "Run B" if better_run == "b" else "Run A"
    if delta is None:
        return f"{winner} is better for {scope}.{metric_name}."
    return f"{winner} is better for {scope}.{metric_name} by {abs(delta):.3f}."


def _overall_winner(comparisons: list[MetricComparison]) -> str:
    """Determine an overall winner from per-metric winners."""

    wins_a = sum(1 for item in comparisons if item.better_run == "a")
    wins_b = sum(1 for item in comparisons if item.better_run == "b")
    if wins_a == wins_b:
        return "mixed" if wins_a or wins_b else "same"
    return "a" if wins_a > wins_b else "b"


def _comparison_summary(overall_winner: str, comparisons: list[MetricComparison]) -> str:
    """Build a plain-English comparison summary."""

    if not comparisons:
        return "No comparable quality metrics were found in either run."
    if overall_winner == "same":
        return "Both runs have the same quality metrics for the compared outputs."
    if overall_winner == "mixed":
        return "The compared runs have mixed quality results across the measured metrics."
    winner = "Run A" if overall_winner == "a" else "Run B"
    key_differences = [item.metric_name for item in comparisons if item.better_run == overall_winner][:3]
    if key_differences:
        return f"{winner} performs better on key metrics: {', '.join(key_differences)}."
    return f"{winner} has the stronger quality profile overall."


__all__ = [
    "MetricComparison",
    "QualityComparison",
    "compare_run_quality",
    "quality_comparison_to_markdown",
]
