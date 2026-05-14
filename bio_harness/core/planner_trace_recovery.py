"""Helpers for recovering partial hierarchical planner traces.

This module parses planner trace artifacts emitted during hierarchical planning
so the supervisor can reuse completed model output after a timeout instead of
discarding it and restarting from scratch.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bio_harness.core.hierarchical_planning import normalize_step_execution_spec


@dataclass(frozen=True)
class HierarchicalTraceRecoveryState:
    """Recovered hierarchical planner trace state for one supervisor attempt.

    Attributes:
        workflow_spec: Parsed workflow skeleton returned by the model.
        completed_step_specs_by_id: Normalized expanded steps keyed by workflow
            step ID.
        missing_workflow_steps: Workflow steps that do not yet have a completed
            expansion in the trace.
        supervisor_attempt: Supervisor attempt number associated with the trace.
        planner_pid: Planner process PID associated with the trace.
        workflow_trace_file: Structured-success event file that supplied the
            recovered workflow skeleton.
    """

    workflow_spec: dict[str, Any]
    completed_step_specs_by_id: dict[int, dict[str, Any]]
    missing_workflow_steps: list[dict[str, Any]]
    supervisor_attempt: int
    planner_pid: int
    workflow_trace_file: str


def _load_json_object(path: Path) -> dict[str, Any] | None:
    """Return a JSON object from ``path`` when possible."""

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _load_trace_payload(event_file: Path) -> dict[str, Any] | None:
    """Return the parsed event payload from a planner trace event file."""

    payload = _load_json_object(event_file)
    if not isinstance(payload, dict):
        return None
    return payload


def _load_raw_trace_content(payload: dict[str, Any], event_file: Path) -> dict[str, Any] | None:
    """Return the parsed raw planner content referenced by an event payload."""

    raw_path = Path(str(payload.get("raw_content_file", "") or "").strip())
    if raw_path.is_file():
        parsed = _load_json_object(raw_path)
        if isinstance(parsed, dict):
            return parsed

    excerpt = payload.get("raw_excerpt")
    if isinstance(excerpt, str) and excerpt.strip():
        try:
            parsed = json.loads(excerpt)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return None


def load_hierarchical_trace_recovery_state(
    planner_trace_dir: str | Path,
) -> HierarchicalTraceRecoveryState | None:
    """Recover the latest hierarchical workflow skeleton and completed steps.

    Args:
        planner_trace_dir: Planner trace directory for the run.

    Returns:
        Parsed recovery state for the newest workflow skeleton in the trace, or
        ``None`` when no usable hierarchical trace could be recovered.
    """

    base_dir = Path(planner_trace_dir).expanduser()
    try:
        if not base_dir.is_dir():
            return None
    except Exception:
        return None

    event_files = sorted(base_dir.glob("*structured_success.json"))
    if not event_files:
        return None

    workflow_event_file: Path | None = None
    workflow_event_payload: dict[str, Any] | None = None
    workflow_spec: dict[str, Any] | None = None
    for event_file in event_files:
        payload = _load_trace_payload(event_file)
        if not isinstance(payload, dict):
            continue
        stage = str(((payload.get("payload") or {}) if isinstance(payload.get("payload"), dict) else {}).get("stage", "")).strip()
        if stage != "workflow_skeleton":
            continue
        raw_workflow = _load_raw_trace_content(payload, event_file)
        workflow_list = raw_workflow.get("workflow", []) if isinstance(raw_workflow, dict) else []
        if not isinstance(workflow_list, list) or not workflow_list:
            continue
        workflow_event_file = event_file
        workflow_event_payload = payload
        workflow_spec = raw_workflow

    if workflow_event_file is None or workflow_event_payload is None or workflow_spec is None:
        return None

    workflow_steps = workflow_spec.get("workflow", []) if isinstance(workflow_spec.get("workflow"), list) else []
    if not workflow_steps:
        return None

    trace_context = workflow_event_payload.get("trace_context", {})
    attempt_num = 0
    planner_pid = 0
    try:
        attempt_num = int(((trace_context or {}) if isinstance(trace_context, dict) else {}).get("supervisor_attempt", 0) or 0)
    except Exception:
        attempt_num = 0
    try:
        planner_pid = int(workflow_event_payload.get("pid", 0) or 0)
    except Exception:
        planner_pid = 0

    workflow_index = event_files.index(workflow_event_file)
    completed_step_specs_by_id: dict[int, dict[str, Any]] = {}
    workflow_step_by_id: dict[int, dict[str, Any]] = {}
    for workflow_step in workflow_steps:
        if not isinstance(workflow_step, dict):
            continue
        try:
            sid = int(workflow_step.get("step_id", 0))
        except Exception:
            continue
        workflow_step_by_id[sid] = workflow_step

    for event_file in event_files[workflow_index + 1 :]:
        payload = _load_trace_payload(event_file)
        if not isinstance(payload, dict):
            continue
        payload_context = payload.get("trace_context", {})
        try:
            payload_attempt = int(((payload_context or {}) if isinstance(payload_context, dict) else {}).get("supervisor_attempt", 0) or 0)
        except Exception:
            payload_attempt = 0
        try:
            payload_pid = int(payload.get("pid", 0) or 0)
        except Exception:
            payload_pid = 0
        if payload_attempt != attempt_num or payload_pid != planner_pid:
            continue
        stage = str(((payload.get("payload") or {}) if isinstance(payload.get("payload"), dict) else {}).get("stage", "")).strip()
        if stage != "step_expansion":
            continue
        raw_step = _load_raw_trace_content(payload, event_file)
        if not isinstance(raw_step, dict):
            continue
        try:
            step_id = int(raw_step.get("step_id", 0) or 0)
        except Exception:
            continue
        workflow_step = workflow_step_by_id.get(step_id)
        if not isinstance(workflow_step, dict):
            continue
        expected_tool_name = str(workflow_step.get("tool_name", "") or "").strip()
        completed_step_specs_by_id[step_id] = normalize_step_execution_spec(
            raw_step,
            expected_step_id=step_id,
            expected_tool_name=expected_tool_name,
        )

    missing_workflow_steps = [
        workflow_step
        for workflow_step in workflow_steps
        if isinstance(workflow_step, dict)
        and int(workflow_step.get("step_id", 0) or 0) not in completed_step_specs_by_id
    ]
    return HierarchicalTraceRecoveryState(
        workflow_spec=workflow_spec,
        completed_step_specs_by_id=completed_step_specs_by_id,
        missing_workflow_steps=missing_workflow_steps,
        supervisor_attempt=attempt_num,
        planner_pid=planner_pid,
        workflow_trace_file=workflow_event_file.name,
    )
