"""Frozen benchmark scenarios for deterministic result-review decisions.

This module provides a shared scenario matrix for validating the post-run
review layer. The same scenarios are used by unit-style benchmark tests and
the script-side JSON benchmark runner to avoid drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bio_harness.core.result_decision_policy import ResultDecision
from bio_harness.core.result_review import RunResultReview, review_run_results

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_ROOT = PROJECT_ROOT / "benchmark_data" / "result_review_decision"


@dataclass(frozen=True)
class ResultReviewBenchmarkScenario:
    """One frozen benchmark scenario for result-review decisions.

    Attributes:
        name: Stable scenario identifier.
        analysis_type: Analysis type passed to the review layer.
        expected_decision: Expected high-level action for the scenario.
        fixture_dir: Directory containing the scenario artifacts.
        level: Difficulty-band label such as ``L1`` through ``L5``.
        boundary_metric: Metric or semantic edge emphasized by the scenario.
        review_signals: Optional extra review notes that influence the policy.
    """

    name: str
    analysis_type: str
    expected_decision: ResultDecision
    fixture_dir: Path
    level: str = ""
    boundary_metric: str = ""
    review_signals: tuple[str, ...] = ()


def benchmark_scenarios(
    benchmark_root: Path | None = None,
) -> tuple[ResultReviewBenchmarkScenario, ...]:
    """Return the frozen result-review benchmark scenario matrix."""

    root = Path(benchmark_root or DEFAULT_BENCHMARK_ROOT).expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    scenarios: list[ResultReviewBenchmarkScenario] = []
    for raw in manifest.get("scenarios", []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        scenarios.append(
            ResultReviewBenchmarkScenario(
                name=name,
                analysis_type=str(raw.get("analysis_type", "")).strip(),
                expected_decision=ResultDecision(str(raw.get("expected_decision", "")).strip()),
                fixture_dir=root / name,
                level=str(raw.get("level", "")).strip() or "UNSPECIFIED",
                boundary_metric=str(raw.get("boundary_metric", "")).strip(),
                review_signals=tuple(
                    str(signal).strip()
                    for signal in (raw.get("review_signals", []) or [])
                    if str(signal).strip()
                ),
            )
        )
    return tuple(scenarios)


def review_benchmark_scenario(
    scenario: ResultReviewBenchmarkScenario,
) -> RunResultReview:
    """Materialize and review one frozen benchmark scenario."""

    return review_run_results(
        scenario.fixture_dir,
        scenario.analysis_type,
        {"plan": [], "final_deliverables": []},
        llm=None,
        review_signals=scenario.review_signals,
    )


def run_result_review_benchmark(
    output_path: Path | None = None,
    benchmark_root: Path | None = None,
) -> dict[str, Any]:
    """Run the frozen result-review benchmark matrix.

    Args:
        output_path: Optional JSON output path.
        benchmark_root: Optional benchmark fixture root.

    Returns:
        JSON-serializable benchmark summary.
    """

    scenarios = benchmark_scenarios(benchmark_root=benchmark_root)
    per_scenario: list[dict[str, Any]] = []
    expected_counts: dict[str, int] = {}
    observed_counts: dict[str, int] = {}
    confusion: dict[str, dict[str, int]] = {}
    level_stats: dict[str, dict[str, Any]] = {}
    failing_scenarios: list[dict[str, Any]] = []
    for scenario in scenarios:
        review = review_benchmark_scenario(scenario)
        expected_label = scenario.expected_decision.value
        observed_label = review.decision.decision.value
        passed = review.decision.decision == scenario.expected_decision
        expected_counts[expected_label] = expected_counts.get(expected_label, 0) + 1
        observed_counts[observed_label] = observed_counts.get(observed_label, 0) + 1
        row = confusion.setdefault(expected_label, {})
        row[observed_label] = row.get(observed_label, 0) + 1
        level_bucket = level_stats.setdefault(
            scenario.level,
            {
                "scenarios_evaluated": 0,
                "correct": 0,
                "expected_decision_counts": {},
                "observed_decision_counts": {},
                "decision_confusion": {},
                "scenario_names": [],
                "boundary_metrics": {},
            },
        )
        level_bucket["scenarios_evaluated"] += 1
        level_bucket["correct"] += 1 if passed else 0
        level_bucket["scenario_names"].append(scenario.name)
        if scenario.boundary_metric:
            boundary_counts = level_bucket["boundary_metrics"]
            boundary_counts[scenario.boundary_metric] = boundary_counts.get(scenario.boundary_metric, 0) + 1
        level_expected = level_bucket["expected_decision_counts"]
        level_expected[expected_label] = level_expected.get(expected_label, 0) + 1
        level_observed = level_bucket["observed_decision_counts"]
        level_observed[observed_label] = level_observed.get(observed_label, 0) + 1
        level_confusion = level_bucket["decision_confusion"].setdefault(expected_label, {})
        level_confusion[observed_label] = level_confusion.get(observed_label, 0) + 1
        per_scenario.append(
            {
                "name": scenario.name,
                "analysis_type": scenario.analysis_type,
                "fixture_dir": str(scenario.fixture_dir),
                "level": scenario.level,
                "boundary_metric": scenario.boundary_metric,
                "expected_decision": expected_label,
                "observed_decision": observed_label,
                "passed": passed,
                "rationale": review.decision.rationale,
                "warning_metric_names": list(review.decision.warning_metric_names),
                "fail_metric_names": list(review.decision.fail_metric_names),
            }
        )
        if not passed:
            failing_scenarios.append(
                {
                    "name": scenario.name,
                    "level": scenario.level,
                    "boundary_metric": scenario.boundary_metric,
                    "expected_decision": expected_label,
                    "observed_decision": observed_label,
                    "rationale": review.decision.rationale,
                }
            )

    accuracy = sum(1 for row in per_scenario if row["passed"]) / float(len(per_scenario) or 1)
    rendered_level_stats: dict[str, Any] = {}
    for level, raw in sorted(level_stats.items(), key=lambda item: _level_sort_key(item[0])):
        total = int(raw.get("scenarios_evaluated", 0) or 0)
        correct = int(raw.get("correct", 0) or 0)
        rendered_level_stats[level] = {
            "scenarios_evaluated": total,
            "accuracy": round(correct / float(total or 1), 3),
            "expected_decision_counts": dict(sorted(raw.get("expected_decision_counts", {}).items())),
            "observed_decision_counts": dict(sorted(raw.get("observed_decision_counts", {}).items())),
            "decision_confusion": {
                expected: dict(sorted(observed.items()))
                for expected, observed in sorted(raw.get("decision_confusion", {}).items())
            },
            "scenario_names": list(raw.get("scenario_names", [])),
            "boundary_metrics": dict(sorted(raw.get("boundary_metrics", {}).items())),
        }
    summary = {
        "benchmark": "result_review_decision",
        "scenarios_evaluated": len(per_scenario),
        "accuracy": round(accuracy, 3),
        "expected_decision_counts": dict(sorted(expected_counts.items())),
        "observed_decision_counts": dict(sorted(observed_counts.items())),
        "decision_confusion": {
            expected: dict(sorted(observed.items()))
            for expected, observed in sorted(confusion.items())
        },
        "level_stats": rendered_level_stats,
        "failing_scenarios": failing_scenarios,
        "per_scenario": per_scenario,
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def result_review_benchmark_count_rows(summary: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Build normalized decision-count rows from one benchmark summary."""

    expected_counts = summary.get("expected_decision_counts", {})
    observed_counts = summary.get("observed_decision_counts", {})
    labels = sorted(
        {
            *(expected_counts.keys() if isinstance(expected_counts, dict) else []),
            *(observed_counts.keys() if isinstance(observed_counts, dict) else []),
        }
    )
    rows: list[dict[str, Any]] = []
    for label in labels:
        expected = expected_counts.get(label, 0) if isinstance(expected_counts, dict) else 0
        observed = observed_counts.get(label, 0) if isinstance(observed_counts, dict) else 0
        rows.append(
            {
                "decision": label,
                "expected_count": int(expected),
                "observed_count": int(observed),
                "delta": int(observed) - int(expected),
            }
        )
    return tuple(rows)


def result_review_benchmark_counts_to_markdown(summary: dict[str, Any]) -> str:
    """Render only the decision-count table for manuscript-friendly reuse."""

    rows = result_review_benchmark_count_rows(summary)
    lines = [
        "# Result Review Decision Counts",
        "",
        "| Decision | Expected | Observed | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| `{row['decision']}` | {row['expected_count']} | {row['observed_count']} | {row['delta']} |"
        )
    return "\n".join(lines).strip() + "\n"


def result_review_benchmark_difficulty_to_markdown(summary: dict[str, Any]) -> str:
    """Render per-level difficulty results for manuscript-facing discussion."""

    level_stats = summary.get("level_stats", {})
    failing = summary.get("failing_scenarios", [])
    lines = [
        "# Result Review Difficulty Curve",
        "",
        f"- Scenarios evaluated: `{summary.get('scenarios_evaluated', 0)}`",
        f"- Accuracy: `{float(summary.get('accuracy', 0.0)):.3f}`",
        "",
        "| Level | Scenarios | Accuracy |",
        "| --- | ---: | ---: |",
    ]
    if isinstance(level_stats, dict) and level_stats:
        for level, payload in sorted(level_stats.items(), key=lambda item: _level_sort_key(item[0])):
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{level}` | {int(payload.get('scenarios_evaluated', 0))} | {float(payload.get('accuracy', 0.0)):.3f} |"
            )
    else:
        lines.append("| `unavailable` | 0 | 0.000 |")
    if isinstance(failing, list) and failing:
        lines.extend(
            [
                "",
                "## Current Failure Catalog",
                "",
            ]
        )
        for row in failing:
            if not isinstance(row, dict):
                continue
            lines.append(
                "- "
                f"`{row.get('name', '')}` "
                f"({row.get('level', '')}, {row.get('boundary_metric', '')}): "
                f"expected `{row.get('expected_decision', '')}`, observed `{row.get('observed_decision', '')}`"
            )
    return "\n".join(lines).strip() + "\n"


def result_review_benchmark_to_markdown(summary: dict[str, Any]) -> str:
    """Render a benchmark summary as Markdown."""

    lines = [
        "# Result Review Decision Benchmark",
        "",
        f"- Scenarios evaluated: `{summary.get('scenarios_evaluated', 0)}`",
        f"- Accuracy: `{float(summary.get('accuracy', 0.0)):.3f}`",
        "",
        "## Per-Level Summary",
        "",
        "| Level | Scenarios | Accuracy |",
        "| --- | ---: | ---: |",
    ]
    level_stats = summary.get("level_stats", {})
    if isinstance(level_stats, dict) and level_stats:
        for level, payload in sorted(level_stats.items(), key=lambda item: _level_sort_key(item[0])):
            if not isinstance(payload, dict):
                continue
            lines.append(
                f"| `{level}` | {int(payload.get('scenarios_evaluated', 0))} | {float(payload.get('accuracy', 0.0)):.3f} |"
            )
    else:
        lines.append("| `unavailable` | 0 | 0.000 |")
    lines.extend(
        [
            "",
        "## Decision Counts",
        "",
        ]
    )
    expected_counts = summary.get("expected_decision_counts", {})
    observed_counts = summary.get("observed_decision_counts", {})
    if isinstance(expected_counts, dict):
        lines.append("Expected decisions:")
        for name, count in sorted(expected_counts.items()):
            lines.append(f"- `{name}`: `{count}`")
        lines.append("")
    if isinstance(observed_counts, dict):
        lines.append("Observed decisions:")
        for name, count in sorted(observed_counts.items()):
            lines.append(f"- `{name}`: `{count}`")
    confusion = summary.get("decision_confusion", {})
    if isinstance(confusion, dict) and confusion:
        lines.extend(
            [
                "",
                "## Decision Confusion",
                "",
                "| Expected | Observed | Count |",
                "| --- | --- | ---: |",
            ]
        )
        for expected_name, observed_map in sorted(confusion.items()):
            if not isinstance(observed_map, dict):
                continue
            for observed_name, count in sorted(observed_map.items()):
                lines.append(f"| `{expected_name}` | `{observed_name}` | {count} |")
    failing = summary.get("failing_scenarios", [])
    if isinstance(failing, list) and failing:
        lines.extend(
            [
                "",
                "## Mismatched Scenarios",
                "",
            ]
        )
        for row in failing:
            if not isinstance(row, dict):
                continue
            lines.extend(
                [
                    f"### `{row.get('name', '')}`",
                    "",
                    f"- Level: `{row.get('level', '')}`",
                    f"- Boundary metric: `{row.get('boundary_metric', '')}`",
                    f"- Expected: `{row.get('expected_decision', '')}`",
                    f"- Observed: `{row.get('observed_decision', '')}`",
                    f"- Rationale: {row.get('rationale', '')}",
                    "",
                ]
            )
    lines.extend(
        [
            "",
            "## Per Scenario",
        "",
        ]
    )
    for row in summary.get("per_scenario", []):
        if not isinstance(row, dict):
            continue
        lines.extend(
            [
                f"### `{row.get('name', '')}`",
                "",
                f"- Analysis type: `{row.get('analysis_type', '')}`",
                f"- Expected: `{row.get('expected_decision', '')}`",
                f"- Observed: `{row.get('observed_decision', '')}`",
                f"- Passed: `{bool(row.get('passed', False))}`",
                f"- Rationale: {row.get('rationale', '')}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _level_sort_key(label: str) -> tuple[int, str]:
    """Return a stable sort key for difficulty-band labels."""

    text = str(label or "").strip().upper()
    if text.startswith("L") and text[1:].isdigit():
        return int(text[1:]), text
    return 999, text


__all__ = [
    "DEFAULT_BENCHMARK_ROOT",
    "ResultReviewBenchmarkScenario",
    "benchmark_scenarios",
    "result_review_benchmark_count_rows",
    "result_review_benchmark_counts_to_markdown",
    "result_review_benchmark_difficulty_to_markdown",
    "result_review_benchmark_to_markdown",
    "review_benchmark_scenario",
    "run_result_review_benchmark",
]
