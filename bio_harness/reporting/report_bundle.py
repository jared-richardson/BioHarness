"""Build opt-in researcher-facing report bundles for completed runs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bio_harness.analysis.figure_factory import render_horizontal_bar, set_figure_style
from bio_harness.core.error_diagnosis import ErrorDiagnosis
from bio_harness.core.failure_reporting import build_failure_diagnosis
from bio_harness.core.in_run_quality_monitor import in_run_quality_summary_to_markdown
from bio_harness.core.output_catalog import build_output_catalog, catalog_to_json, catalog_to_markdown
from bio_harness.core.preflight_summary import (
    PreflightSummary,
    build_preflight_summary,
    preflight_summary_to_json,
    preflight_summary_to_markdown,
)
from bio_harness.core.result_interpreter import InterpretationResult
from bio_harness.core.result_review import result_review_to_json, result_review_to_markdown, review_run_results
from bio_harness.core.tool_env import requirement_available, which_with_pixi
from bio_harness.reporting.run_context import (
    build_artifact_inventory,
    final_plan_steps,
    resolve_run_context,
    run_context_to_json,
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_markdown(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _run_optional_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, check=False)
    except Exception as exc:
        return {"attempted": True, "ok": False, "error": str(exc)}
    return {
        "attempted": True,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-2000:],
        "stderr_tail": (result.stderr or "")[-2000:],
    }


def _planner_attempt_count(raw_attempts: Any) -> int:
    """Normalize planner attempt metadata across historical result formats."""
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


def _build_generic_artifact_inventory(source_dir: Path, *, exclude_dir: Path | None = None) -> list[dict[str, Any]]:
    """Inventory a generic artifact directory that is not a completed run bundle.

    Args:
        source_dir: Directory containing user-visible artifacts.
        exclude_dir: Optional generated report directory to exclude from the
            inventory when it lives inside ``source_dir``.

    Returns:
        A list of artifact rows compatible with the run-report summary schema.
    """
    rows: list[dict[str, Any]] = []
    excluded = exclude_dir.resolve() if exclude_dir is not None else None
    for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
        if excluded is not None and (path == excluded or excluded in path.parents):
            continue
        rows.append(
            {
                "category": "final_output",
                "path": str(path),
                "relative_to_selected_dir": str(path.relative_to(source_dir)),
                "size_bytes": int(path.stat().st_size),
            }
        )
    return rows


def _build_summary(context, *, in_run_quality_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    inventory = build_artifact_inventory(context)
    final_outputs = [row for row in inventory if row["category"] == "final_output"]
    return {
        "context_mode": str(getattr(context, "resolution_mode", "") or "result_json"),
        "selected_dir": str(context.selected_dir),
        "run_dir": str(context.run_dir),
        "status": str(context.result.get("status", "") or ""),
        "benchmark_policy": str(context.result.get("benchmark_policy", "") or ""),
        "auto_repair_history_count": int(context.result.get("auto_repair_history_count", 0) or 0),
        "planner_attempts": _planner_attempt_count(context.result.get("planning_attempts", 0)),
        "final_plan_steps": len(final_plan_steps(context)),
        "final_output_count": len(final_outputs),
        "final_outputs": final_outputs,
        "validator_log": str(context.validator_log_path) if context.validator_log_path else "",
        "harness_log": str(context.harness_log_path) if context.harness_log_path else "",
        "in_run_quality": _summarize_in_run_quality(in_run_quality_summary),
    }


def _build_generic_summary(
    source_dir: Path,
    final_outputs: list[dict[str, Any]],
    *,
    in_run_quality_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a report summary for a generic artifact directory.

    Args:
        source_dir: Directory being summarized.
        final_outputs: Inventory rows for files discovered under ``source_dir``.

    Returns:
        Summary payload aligned with completed-run report summaries.
    """
    return {
        "context_mode": "artifact_directory_only",
        "selected_dir": str(source_dir),
        "run_dir": str(source_dir),
        "status": "artifact_directory",
        "benchmark_policy": "",
        "auto_repair_history_count": 0,
        "planner_attempts": 0,
        "final_plan_steps": 0,
        "final_output_count": len(final_outputs),
        "final_outputs": final_outputs,
        "validator_log": "",
        "harness_log": "",
        "in_run_quality": _summarize_in_run_quality(in_run_quality_summary),
    }


def _build_markdown_summary(summary: dict[str, Any]) -> str:
    in_run_quality = summary.get("in_run_quality", {}) if isinstance(summary.get("in_run_quality", {}), dict) else {}
    lines = [
        "# Bio-Harness Run Report",
        "",
        "## Summary",
        "",
        f"- Context mode: `{summary.get('context_mode', 'artifact_directory_only')}`",
        f"- Selected dir: `{summary['selected_dir']}`",
        f"- Run dir: `{summary['run_dir']}`",
        f"- Status: `{summary['status']}`",
        f"- Benchmark policy: `{summary['benchmark_policy']}`",
        f"- Planner attempts: `{summary['planner_attempts']}`",
        f"- Final plan steps: `{summary['final_plan_steps']}`",
        f"- Auto repairs: `{summary['auto_repair_history_count']}`",
        f"- Final outputs: `{summary['final_output_count']}`",
    ]
    if bool(in_run_quality.get("available")):
        lines.append(
            "- In-run quality: "
            f"`{in_run_quality.get('suspicious_event_count', 0)}` suspicious heartbeat events, "
            f"`{in_run_quality.get('zero_byte_output_count', 0)}` zero-byte outputs"
        )
    lines.extend(
        [
            "",
            "## Final Outputs",
            "",
        ]
    )
    for row in summary["final_outputs"]:
        lines.append(f"- `{row['relative_to_selected_dir'] or row['path']}` ({row['size_bytes']} bytes)")
    if not summary["final_outputs"]:
        lines.append("- No final outputs discovered.")
    lines.extend(
        [
            "",
            "## Reproducible Assets",
            "",
            *(
                ["- `completed_run_context.json`"]
                if summary.get("context_mode", "") != "artifact_directory_only"
                else []
            ),
            "- `summary.json`",
            "- `summary.md`",
            "- `output_catalog.json`",
            "- `output_catalog.md`",
            "- `preflight_summary.json`",
            "- `preflight_summary.md`",
            *(
                [
                    "- `in_run_quality_summary.json`",
                    "- `in_run_quality_summary.md`",
                ]
                if bool(in_run_quality.get("available"))
                else []
            ),
            "- `interpretation.json`",
            "- `interpretation.md`",
            "- `result_review.json`",
            "- `result_review.md`",
            "- `report.qmd`",
            "- `figures/run_overview.png`",
            "- `figures/run_overview.svg`",
        ]
    )
    return "\n".join(lines)


def _build_quarto_text(summary: dict[str, Any]) -> str:
    output_rows = "\n".join(
        f"| `{row['relative_to_selected_dir'] or row['path']}` | {row['size_bytes']} |"
        for row in summary["final_outputs"]
    )
    if not output_rows:
        output_rows = "| No final outputs discovered | 0 |"
    return f"""---
title: "Bio-Harness Run Report"
format:
  html:
    toc: true
  pdf: default
  docx: default
---

# Summary

- Selected dir: `{summary['selected_dir']}`
- Run dir: `{summary['run_dir']}`
- Context mode: `{summary.get('context_mode', 'artifact_directory_only')}`
- Status: `{summary['status']}`
- Benchmark policy: `{summary['benchmark_policy']}`
- Planner attempts: `{summary['planner_attempts']}`
- Final plan steps: `{summary['final_plan_steps']}`
- Auto repairs: `{summary['auto_repair_history_count']}`

![](figures/run_overview.png)

# Final Outputs

| Output | Size (bytes) |
| --- | ---: |
{output_rows}

# Notes

This report bundle was generated by the Bio-Harness opt-in reporting layer. It
does not affect the default benchmark or harness execution path.
"""


def _analysis_type_for_context(context) -> str:
    """Return the best available analysis type from a run context."""

    state = context.state if isinstance(getattr(context, "state", {}), dict) else {}
    analysis_spec = state.get("analysis_spec", {}) if isinstance(state.get("analysis_spec", {}), dict) else {}
    return str(
        analysis_spec.get("analysis_type", "")
        or context.result.get("analysis_type", "")
        or ""
    ).strip()


def _data_root_for_context(context) -> Path | None:
    """Return the best available data root from one run context."""

    state = context.state if isinstance(getattr(context, "state", {}), dict) else {}
    analysis_spec = state.get("analysis_spec", {}) if isinstance(state.get("analysis_spec", {}), dict) else {}
    candidate = str(
        analysis_spec.get("data_root", "")
        or context.result.get("data_root", "")
        or ""
    ).strip()
    if not candidate:
        return None
    return Path(candidate).expanduser().resolve(strict=False)


def _persisted_input_quality_for_context(context) -> dict[str, Any] | None:
    """Return stored input-quality state from one run context when available."""

    result_payload = context.result.get("input_quality", {})
    if isinstance(result_payload, dict) and result_payload:
        return result_payload
    state_payload = context.state.get("input_quality", {}) if isinstance(context.state, dict) else {}
    if isinstance(state_payload, dict) and state_payload:
        return state_payload
    return None


def _persisted_in_run_quality_for_context(context) -> dict[str, Any] | None:
    """Return stored in-run quality state from one run context when available."""

    result_payload = context.result.get("in_run_quality_summary", {})
    if isinstance(result_payload, dict) and result_payload:
        return dict(result_payload)
    state_payload = context.state.get("in_run_quality_summary", {}) if isinstance(context.state, dict) else {}
    if isinstance(state_payload, dict) and state_payload:
        return dict(state_payload)
    summary_path = context.run_dir / "in_run_quality_summary.json"
    if not summary_path.exists():
        return None
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(payload) if isinstance(payload, dict) and payload else None


def _summarize_in_run_quality(in_run_quality_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact summary block for persisted in-run quality state."""

    if not isinstance(in_run_quality_summary, dict) or not in_run_quality_summary:
        return {"available": False}
    zero_byte_outputs = in_run_quality_summary.get("zero_byte_outputs", [])
    if not isinstance(zero_byte_outputs, list):
        zero_byte_outputs = list(zero_byte_outputs or [])
    return {
        "available": True,
        "suspicious_event_count": int(in_run_quality_summary.get("suspicious_event_count", 0) or 0),
        "zero_byte_output_count": len([str(item).strip() for item in zero_byte_outputs if str(item).strip()]),
        "active_step_id": in_run_quality_summary.get("active_step_id"),
        "tool_name": str(in_run_quality_summary.get("tool_name", "") or ""),
    }


def _write_interpretation_bundle(
    report_dir: Path,
    *,
    interpretation: InterpretationResult,
) -> None:
    """Write deterministic run-interpretation artifacts into a report bundle."""

    payload = asdict(interpretation)
    _write_json(report_dir / "interpretation.json", payload)
    lines = [
        "# Result Interpretation",
        "",
        f"- Analysis type: `{interpretation.analysis_type}`",
        f"- Model used: `{interpretation.model_used}`",
        "",
        interpretation.interpretation,
        "",
        "## Concerns",
        "",
    ]
    if interpretation.concerns:
        lines.extend(f"- {item}" for item in interpretation.concerns)
    else:
        lines.append("- None")
    _write_markdown(report_dir / "interpretation.md", "\n".join(lines))


def _write_result_review_bundle(
    report_dir: Path,
    *,
    review,
    selected_dir: Path,
) -> None:
    """Write shared deterministic result-review artifacts into a report bundle."""

    _write_json(report_dir / "result_review.json", result_review_to_json(review))
    _write_markdown(
        report_dir / "result_review.md",
        result_review_to_markdown(review, selected_dir=selected_dir),
    )


def _write_preflight_summary_bundle(
    report_dir: Path,
    *,
    preflight_summary: PreflightSummary,
) -> None:
    """Write standardized preflight-summary artifacts into a report bundle."""

    _write_json(report_dir / "preflight_summary.json", preflight_summary_to_json(preflight_summary))
    _write_markdown(
        report_dir / "preflight_summary.md",
        preflight_summary_to_markdown(preflight_summary),
    )


def _write_in_run_quality_bundle(
    report_dir: Path,
    *,
    in_run_quality_summary: dict[str, Any],
) -> None:
    """Write standardized in-run quality artifacts into a report bundle."""

    if not in_run_quality_summary:
        return
    _write_json(report_dir / "in_run_quality_summary.json", in_run_quality_summary)
    _write_markdown(
        report_dir / "in_run_quality_summary.md",
        in_run_quality_summary_to_markdown(in_run_quality_summary),
    )


def _write_failure_diagnosis_bundle(
    report_dir: Path,
    *,
    diagnosis: dict[str, Any],
) -> None:
    """Write failure-diagnosis artifacts when a run failed."""

    if not diagnosis:
        return
    _write_json(report_dir / "failure_diagnosis.json", diagnosis)
    lines = [
        "# Failure Diagnosis",
        "",
        f"- Failure class: `{diagnosis.get('failure_class', '')}`",
        f"- Failed step: `{diagnosis.get('failed_step_number', 0)}`",
        f"- Tool: `{diagnosis.get('tool_name', '')}`",
        f"- Exit code: `{diagnosis.get('exit_code', 0)}`",
        f"- Confidence: `{diagnosis.get('confidence', '')}`",
        f"- Diagnosed by: `{diagnosis.get('diagnosed_by', '')}`",
        "",
        "## Root Cause",
        "",
        str(diagnosis.get("root_cause", "") or ""),
        "",
        "## Suggested Fix",
        "",
        str(diagnosis.get("suggested_fix", "") or ""),
    ]
    _write_markdown(report_dir / "failure_diagnosis.md", "\n".join(lines))


def _coerce_error_diagnoses(diagnosis: dict[str, Any]) -> tuple[ErrorDiagnosis, ...]:
    """Convert serialized failure-diagnosis payloads into typed diagnoses."""

    if not isinstance(diagnosis, dict) or not diagnosis:
        return ()
    return (
        ErrorDiagnosis(
            tool_name=str(diagnosis.get("tool_name", "") or ""),
            failure_class=str(diagnosis.get("failure_class", "") or "novel_unknown"),
            root_cause=str(diagnosis.get("root_cause", "") or ""),
            suggested_fix=str(diagnosis.get("suggested_fix", "") or ""),
            confidence=str(diagnosis.get("confidence", "") or "low"),
            diagnosed_by=str(diagnosis.get("diagnosed_by", "") or "heuristic"),
        ),
    )


def build_run_report_bundle(
    run_input: str | Path,
    output_dir: str | Path | None = None,
    *,
    run_multiqc: bool = False,
    render_quarto: bool = False,
) -> Path:
    """Create a Markdown/Quarto report bundle for a run or artifact directory.

    Args:
        run_input: Completed selected-dir path, ``result.json`` path, or a
            generic artifact directory such as a FastQC output folder.
        output_dir: Optional directory for the generated report bundle.
        run_multiqc: Whether to run MultiQC when the executable is available.
        render_quarto: Whether to render the generated Quarto report when the
            executable is available.

    Returns:
        The directory containing the generated report bundle.
    """
    raw_input = Path(run_input).expanduser().resolve()
    context = None
    source_dir = raw_input
    plan: dict[str, Any] = {"plan": [], "final_deliverables": []}
    step_statuses: list[str] | None = None
    analysis_type = ""
    report_dir: Path
    try:
        context = resolve_run_context(raw_input)
        source_dir = context.selected_dir
        plan = context.final_plan if isinstance(context.final_plan, dict) else plan
        step_statuses = context.state.get("step_statuses", []) if isinstance(context.state.get("step_statuses", []), list) else None
        analysis_type = _analysis_type_for_context(context)
        report_dir = Path(output_dir).expanduser().resolve() if output_dir else (source_dir / "reports" / "run_report")
    except (FileNotFoundError, ValueError):
        if not raw_input.is_dir():
            raise
        report_dir = Path(output_dir).expanduser().resolve() if output_dir else (raw_input / "reports" / "run_report")

    failure_diagnosis: dict[str, Any] = {}
    if context is not None:
        failure_diagnosis = (
            context.result.get("failure_diagnosis", {})
            if isinstance(context.result.get("failure_diagnosis", {}), dict)
            else {}
        )
        if not failure_diagnosis:
            failure_diagnosis = build_failure_diagnosis(context.state)

    output_catalog = build_output_catalog(
        source_dir,
        plan,
        step_statuses=step_statuses,
        analysis_type=analysis_type,
    )
    preflight_summary = build_preflight_summary(
        plan,
        selected_dir=source_dir,
        analysis_type=analysis_type,
        data_root=_data_root_for_context(context) if context is not None else None,
        persisted_input_quality=_persisted_input_quality_for_context(context) if context is not None else None,
    )
    in_run_quality_summary = _persisted_in_run_quality_for_context(context) if context is not None else None
    result_review = review_run_results(
        source_dir,
        analysis_type,
        plan,
        llm=None,
        diagnoses=_coerce_error_diagnoses(failure_diagnosis),
        step_statuses=step_statuses,
    )

    report_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = report_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    if context is not None:
        summary = _build_summary(context, in_run_quality_summary=in_run_quality_summary)
    else:
        summary = _build_generic_summary(
            source_dir,
            _build_generic_artifact_inventory(source_dir),
            in_run_quality_summary=in_run_quality_summary,
        )
    if context is not None:
        _write_json(report_dir / "completed_run_context.json", run_context_to_json(context))
    _write_json(report_dir / "summary.json", summary)
    _write_markdown(report_dir / "summary.md", _build_markdown_summary(summary))
    _write_markdown(report_dir / "report.qmd", _build_quarto_text(summary))
    _write_json(report_dir / "output_catalog.json", catalog_to_json(output_catalog))
    _write_markdown(report_dir / "output_catalog.md", catalog_to_markdown(output_catalog))
    _write_preflight_summary_bundle(
        report_dir,
        preflight_summary=preflight_summary,
    )
    if isinstance(in_run_quality_summary, dict) and in_run_quality_summary:
        _write_in_run_quality_bundle(
            report_dir,
            in_run_quality_summary=in_run_quality_summary,
        )
    _write_result_review_bundle(
        report_dir,
        review=result_review,
        selected_dir=source_dir,
    )
    _write_interpretation_bundle(
        report_dir,
        interpretation=result_review.interpretation,
    )
    if context is not None:
        _write_failure_diagnosis_bundle(report_dir, diagnosis=failure_diagnosis)

    set_figure_style()
    render_horizontal_bar(
        title="Run overview",
        labels=["Final outputs", "Plan steps", "Planner attempts", "Auto repairs"],
        values=[
            float(summary["final_output_count"]),
            float(summary["final_plan_steps"]),
            float(summary["planner_attempts"]),
            float(summary["auto_repair_history_count"]),
        ],
        output_path=figures_dir / "run_overview.svg",
        color="#0F766E",
        xlabel="Count",
        note="Generated from Bio-Harness run metadata.",
        wrap_width=18,
    )

    multiqc_bin = which_with_pixi("multiqc") or "multiqc"
    quarto_bin = which_with_pixi("quarto") or "quarto"
    tooling = {
        "multiqc_available": requirement_available("multiqc"),
        "quarto_available": requirement_available("quarto"),
        "multiqc": {"attempted": False, "ok": False},
        "quarto": {"attempted": False, "ok": False},
    }

    if run_multiqc and tooling["multiqc_available"]:
        multiqc_dir = report_dir / "multiqc"
        multiqc_dir.mkdir(parents=True, exist_ok=True)
        tooling["multiqc"] = _run_optional_command(
            [multiqc_bin, str(source_dir), "-o", str(multiqc_dir)],
            cwd=report_dir,
        )

    if render_quarto and tooling["quarto_available"]:
        tooling["quarto"] = _run_optional_command(
            [quarto_bin, "render", str(report_dir / "report.qmd")],
            cwd=report_dir,
        )

    _write_json(report_dir / "tooling_status.json", tooling)
    return report_dir
