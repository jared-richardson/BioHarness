"""Failure-report enrichment helpers for Bio-Harness runs.

This module keeps failure diagnosis logic separate from the runner entrypoints.
It derives one stable failed-step summary from persisted run state and uses the
standalone error-diagnosis helpers to produce JSON-friendly payloads.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from bio_harness.core.error_diagnosis import diagnose_step_failure
from bio_harness.core.recovery_policy import classify_failure


def build_failure_diagnosis(
    run: dict[str, Any],
    *,
    llm: Any | None = None,
) -> dict[str, Any]:
    """Build a stable failure-diagnosis payload from run state.

    Args:
        run: Mutable run-state dictionary or a result/state-like payload.
        llm: Optional LLM object exposing ``summarize_text`` for fallback
            diagnosis when heuristic matching does not identify a root cause.

    Returns:
        JSON-friendly diagnosis payload. Returns an empty dictionary when the
        run is not failed or when no failed-step context can be derived.
    """

    if str(run.get("status", "") or "").strip().lower() != "failed":
        return {}

    plan = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    steps = [step for step in plan.get("plan", []) if isinstance(step, dict)]
    failure_class = classify_failure(run)
    failed_step_number = _failed_step_number(run, total_steps=len(steps))
    failed_step = _step_for_number(steps, failed_step_number)
    tool_name = str((failed_step or {}).get("tool_name", "") or "")
    step_arguments = (
        dict(failed_step.get("arguments", {}))
        if isinstance(failed_step, dict) and isinstance(failed_step.get("arguments", {}), dict)
        else {}
    )
    stderr_text = _read_run_stream(run, key="stderr")
    stdout_text = _read_run_stream(run, key="stdout")
    exit_code = _infer_exit_code(run, stderr_text=stderr_text, stdout_text=stdout_text)
    diagnosis = diagnose_step_failure(
        tool_name=tool_name,
        failure_class=failure_class,
        exit_code=exit_code,
        stderr=stderr_text,
        stdout=stdout_text,
        step_arguments=step_arguments,
        llm=llm,
    )
    return {
        "failure_class": failure_class,
        "failed_step_number": failed_step_number,
        "tool_name": tool_name,
        "exit_code": exit_code,
        "root_cause": diagnosis.root_cause,
        "suggested_fix": diagnosis.suggested_fix,
        "confidence": diagnosis.confidence,
        "diagnosed_by": diagnosis.diagnosed_by,
    }


def _failed_step_number(run: dict[str, Any], *, total_steps: int) -> int:
    """Infer the failed step number from run state."""

    step_statuses = run.get("step_statuses", [])
    if isinstance(step_statuses, list):
        for index, status in enumerate(step_statuses, start=1):
            if str(status or "").strip().lower() == "failed":
                return index
    next_step_idx = int(run.get("next_step_idx", 0) or 0)
    if total_steps > 0 and 0 < next_step_idx <= total_steps:
        return next_step_idx
    if total_steps > 0:
        return min(total_steps, max(1, next_step_idx + 1))
    return 0


def _step_for_number(steps: list[dict[str, Any]], step_number: int) -> dict[str, Any] | None:
    """Return the plan step for a 1-based step number."""

    if step_number <= 0:
        return None
    if step_number <= len(steps):
        return steps[step_number - 1]
    return None


def _read_run_stream(run: dict[str, Any], *, key: str) -> str:
    """Read one persisted stdout/stderr stream for a run."""

    run_files = run.get("run_files", {}) if isinstance(run.get("run_files", {}), dict) else {}
    raw_path = str(run_files.get(key, "") or "").strip()
    if not raw_path:
        raw_path = str(run.get(f"{key}_file", "") or "").strip()
    if not raw_path:
        return ""
    try:
        return Path(raw_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _infer_exit_code(
    run: dict[str, Any],
    *,
    stderr_text: str,
    stdout_text: str,
) -> int:
    """Infer one process exit code from run state and captured output."""

    error_text = str(run.get("error", "") or "")
    for candidate in (
        error_text,
        stderr_text[-4000:],
        stdout_text[-4000:],
    ):
        match = re.search(r"exit code (\d+)", candidate, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
        marker = re.search(r"\[exit_code=(\d+)\]", candidate, re.IGNORECASE)
        if marker:
            try:
                return int(marker.group(1))
            except ValueError:
                continue
    return 1


__all__ = ["build_failure_diagnosis"]
