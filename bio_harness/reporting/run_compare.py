"""Compare two completed Bio-Harness runs without mutating either run."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bio_harness.reporting.quality_compare import compare_run_quality, quality_comparison_to_markdown
from bio_harness.reporting.run_context import build_artifact_inventory, final_plan_steps, resolve_run_context


def _planner_attempt_count(raw_attempts: Any) -> int:
    if isinstance(raw_attempts, list):
        return len(raw_attempts)
    if isinstance(raw_attempts, dict):
        return len(raw_attempts)
    if raw_attempts in (None, ""):
        return 0
    try:
        return int(raw_attempts)
    except (TypeError, ValueError):
        return 0


def _validator_verdict(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("BENCHMARK PASSED:"):
            return stripped
    return ""


def compare_runs(run_a: str | Path, run_b: str | Path) -> dict[str, Any]:
    """Build a compact diff summary for two completed runs."""
    context_a = resolve_run_context(run_a)
    context_b = resolve_run_context(run_b)
    quality = compare_run_quality(
        context_a.selected_dir,
        context_b.selected_dir,
        plan_a=context_a.final_plan,
        plan_b=context_b.final_plan,
    )
    outputs_a = {row["relative_to_selected_dir"] or row["path"] for row in build_artifact_inventory(context_a) if row["category"] == "final_output"}
    outputs_b = {row["relative_to_selected_dir"] or row["path"] for row in build_artifact_inventory(context_b) if row["category"] == "final_output"}
    steps_a = [str(step.get("tool_name", "") or "") for step in final_plan_steps(context_a)]
    steps_b = [str(step.get("tool_name", "") or "") for step in final_plan_steps(context_b)]

    return {
        "run_a": {
            "selected_dir": str(context_a.selected_dir),
            "status": str(context_a.result.get("status", "") or ""),
            "benchmark_policy": str(context_a.result.get("benchmark_policy", "") or ""),
            "auto_repair_history_count": int(context_a.result.get("auto_repair_history_count", 0) or 0),
            "planner_attempts": _planner_attempt_count(context_a.result.get("planning_attempts", 0)),
            "validator_verdict": _validator_verdict(context_a.validator_log_path),
            "final_plan_tools": steps_a,
            "final_outputs": sorted(outputs_a),
        },
        "run_b": {
            "selected_dir": str(context_b.selected_dir),
            "status": str(context_b.result.get("status", "") or ""),
            "benchmark_policy": str(context_b.result.get("benchmark_policy", "") or ""),
            "auto_repair_history_count": int(context_b.result.get("auto_repair_history_count", 0) or 0),
            "planner_attempts": _planner_attempt_count(context_b.result.get("planning_attempts", 0)),
            "validator_verdict": _validator_verdict(context_b.validator_log_path),
            "final_plan_tools": steps_b,
            "final_outputs": sorted(outputs_b),
        },
        "diff": {
            "status_changed": str(context_a.result.get("status", "") or "") != str(context_b.result.get("status", "") or ""),
            "auto_repair_delta": int(context_b.result.get("auto_repair_history_count", 0) or 0)
            - int(context_a.result.get("auto_repair_history_count", 0) or 0),
            "final_plan_length_delta": len(steps_b) - len(steps_a),
            "only_in_run_a_outputs": sorted(outputs_a - outputs_b),
            "only_in_run_b_outputs": sorted(outputs_b - outputs_a),
            "shared_outputs": sorted(outputs_a & outputs_b),
            "plan_tools_identical": steps_a == steps_b,
        },
        "quality_comparison": {
            "run_a_dir": quality.run_a_dir,
            "run_b_dir": quality.run_b_dir,
            "overall_winner": quality.overall_winner,
            "summary": quality.summary,
            "metric_comparisons": [asdict(item) for item in quality.metric_comparisons],
        },
    }


def _comparison_markdown(summary: dict[str, Any]) -> str:
    diff = summary["diff"]
    run_a = summary["run_a"]
    run_b = summary["run_b"]
    lines = [
        "# Bio-Harness Run Comparison",
        "",
        "## Run A",
        "",
        f"- Selected dir: `{run_a['selected_dir']}`",
        f"- Status: `{run_a['status']}`",
        f"- Auto repairs: `{run_a['auto_repair_history_count']}`",
        f"- Planner attempts: `{run_a['planner_attempts']}`",
        f"- Validator: `{run_a['validator_verdict']}`",
        "",
        "## Run B",
        "",
        f"- Selected dir: `{run_b['selected_dir']}`",
        f"- Status: `{run_b['status']}`",
        f"- Auto repairs: `{run_b['auto_repair_history_count']}`",
        f"- Planner attempts: `{run_b['planner_attempts']}`",
        f"- Validator: `{run_b['validator_verdict']}`",
        "",
        "## Differences",
        "",
        f"- Status changed: `{diff['status_changed']}`",
        f"- Auto-repair delta: `{diff['auto_repair_delta']}`",
        f"- Final plan length delta: `{diff['final_plan_length_delta']}`",
        f"- Plan tools identical: `{diff['plan_tools_identical']}`",
        "",
        "## Output Differences",
        "",
    ]
    for label, values in (
        ("Only in run A", diff["only_in_run_a_outputs"]),
        ("Only in run B", diff["only_in_run_b_outputs"]),
        ("Shared outputs", diff["shared_outputs"]),
    ):
        lines.append(f"### {label}")
        lines.append("")
        if values:
            lines.extend(f"- `{value}`" for value in values)
        else:
            lines.append("- None")
        lines.append("")
    return "\n".join(lines)


def write_run_comparison(
    run_a: str | Path,
    run_b: str | Path,
    output_dir: str | Path | None = None,
) -> Path:
    """Write JSON and Markdown comparison artifacts for two completed runs."""
    summary = compare_runs(run_a, run_b)
    quality = compare_run_quality(
        Path(summary["run_a"]["selected_dir"]),
        Path(summary["run_b"]["selected_dir"]),
        plan_a=resolve_run_context(run_a).final_plan,
        plan_b=resolve_run_context(run_b).final_plan,
    )
    target = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else Path(summary["run_a"]["selected_dir"]).resolve() / "reports" / "run_compare"
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / "comparison.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (target / "comparison.md").write_text(_comparison_markdown(summary).strip() + "\n", encoding="utf-8")
    (target / "quality_comparison.json").write_text(
        json.dumps(summary["quality_comparison"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (target / "quality_comparison.md").write_text(
        quality_comparison_to_markdown(quality).strip() + "\n",
        encoding="utf-8",
    )
    return target
