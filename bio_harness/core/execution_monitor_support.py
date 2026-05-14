"""Execution-monitor support helpers for the end-to-end harness.

These helpers keep low-level execution-monitor state transitions separate from
the higher-level execution loop so the CLI runner remains focused on orchestration.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable

from bio_harness.core.artifact_inspectors import scan_existing_step_outputs
from bio_harness.core.step_completion import (
    check_completion_manifest,
    find_completion_manifest,
    resolved_step_outputs_for_completion,
)
from scripts.run_agent_e2e_support import (
    _ExecutionMonitorState,
    _all_steps_completed,
    _extract_pid_from_line,
    _extract_step_command_from_line,
    _extract_step_context_from_line,
    _is_pid_live,
)


def active_step_completion_evidence(
    state: _ExecutionMonitorState,
    *,
    plan: dict[str, Any],
    selected_dir: Path,
) -> dict[str, object]:
    """Return completion evidence for the currently active step, if any."""

    active_step_id = int(getattr(state, "active_step_id", 0) or 0)
    steps = plan.get("plan", []) if isinstance(plan.get("plan", []), list) else []
    if active_step_id <= 0 or active_step_id > len(steps):
        return {"has_evidence": False, "why": "active_step_unavailable"}
    step = steps[active_step_id - 1]
    if not isinstance(step, dict):
        return {"has_evidence": False, "why": "active_step_not_dict"}
    tool_name = str(step.get("tool_name", "") or "").strip()
    step_args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
    manifest_path = find_completion_manifest(
        step_args,
        tool_name=tool_name,
        cwd=selected_dir,
    )
    if manifest_path is not None:
        expected_outputs = resolved_step_outputs_for_completion(
            tool_name=tool_name,
            step_arguments=step_args,
            cwd=selected_dir,
        )
        manifest_check = check_completion_manifest(
            manifest_path.parent,
            tool_name,
            expected_outputs=expected_outputs,
        )
        if manifest_check.completed:
            return {
                "has_evidence": True,
                "source": "completion_manifest",
                "manifest_path": str(manifest_path),
                "active_step_id": active_step_id,
                "tool_name": tool_name,
            }
    outputs = scan_existing_step_outputs(selected_dir, plan, active_step_id - 1)
    if outputs and all(bool(row.get("valid", False)) for row in outputs.values()):
        return {
            "has_evidence": True,
            "source": "expected_outputs",
            "outputs": outputs,
            "active_step_id": active_step_id,
            "tool_name": tool_name,
        }
    return {"has_evidence": False, "why": "no_completion_evidence", "outputs": outputs}


def startup_phase_grace_seconds(
    state: _ExecutionMonitorState,
    *,
    stall_timeout_seconds: int,
    adaptive_live_process_grace_seconds: Callable[..., int],
    prestep_execution_phases: frozenset[str],
) -> int:
    """Return a bounded grace window for pre-PID execution startup."""

    if not bool(getattr(state, "first_pid_observed", False)):
        phase = str(getattr(state, "active_phase", "") or "").strip().lower()
        if phase in prestep_execution_phases:
            return max(int(stall_timeout_seconds), 120)
        adaptive = 0
        if str(getattr(state, "active_tool_name", "") or "").strip() or str(
            getattr(state, "active_command", "") or ""
        ).strip():
            adaptive = int(
                adaptive_live_process_grace_seconds(
                    active_tool_name=state.active_tool_name,
                    active_command=state.active_command,
                )
            )
        return max(
            int(stall_timeout_seconds),
            min(300, max(180, adaptive)),
        )
    return int(stall_timeout_seconds)


def has_live_executor_process(active_pid: int | None, process_monitor_last: dict[str, Any]) -> bool:
    """Return whether the active executor process or monitored tree is still live."""

    return _is_pid_live(active_pid) or bool(
        (process_monitor_last or {}).get("alive", False)
    )


def should_drain_completed_execution(
    *,
    step_statuses: list[str],
    has_live_process: bool,
    now_ts: float,
    last_progress_ts: float,
    drain_seconds: int,
) -> bool:
    """Return whether a completed plan has drained long enough to stop polling."""

    return (
        _all_steps_completed(step_statuses)
        and not has_live_process
        and int(max(0, now_ts - last_progress_ts)) >= int(drain_seconds)
    )


def update_active_execution_context(
    line_text: str,
    state: _ExecutionMonitorState,
    *,
    now_ts: float | None = None,
) -> None:
    """Update current step, command, and PID hints from one executor line."""

    current_ts = float(now_ts if now_ts is not None else time.time())
    stripped = str(line_text or "").strip()
    generic_phase_match = re.match(
        r"^\[status\]\s+phase=([a-z_]+)(?:\s+tool=([A-Za-z0-9_.-]+))?",
        stripped,
    )
    if generic_phase_match:
        state.active_phase = str(generic_phase_match.group(1) or "").strip().lower()
        tool_name = str(generic_phase_match.group(2) or "").strip()
        if tool_name:
            state.active_tool_name = tool_name
        state.active_phase_started_ts = current_ts
        if state.active_step_id is None:
            state.active_step_started_ts = current_ts

    step_ctx_id, step_ctx_tool = _extract_step_context_from_line(line_text)
    if step_ctx_id is not None:
        state.active_pid = None
        state.active_step_id = step_ctx_id
        state.active_tool_name = step_ctx_tool or state.active_tool_name
        state.active_step_started_ts = current_ts
        state.active_phase = "step_announced"
        state.active_phase_started_ts = current_ts
        state.first_pid_observed = False
        state.saw_runner_start = False

    phase_match = re.match(
        r"^\[Step\s+(\d+)\s+Output\]\s+\[status\]\s+phase=([a-z_]+)(?:\s+tool=([A-Za-z0-9_.-]+))?",
        str(line_text or "").strip(),
    )
    if phase_match:
        previous_step_id = state.active_step_id
        try:
            state.active_step_id = int(phase_match.group(1))
        except Exception:
            pass
        if state.active_step_id != previous_step_id:
            state.active_pid = None
            state.active_step_started_ts = current_ts
            state.first_pid_observed = False
            state.saw_runner_start = False
        state.active_phase = str(phase_match.group(2) or "").strip().lower()
        tool_name = str(phase_match.group(3) or "").strip()
        if tool_name:
            state.active_tool_name = tool_name
        state.active_phase_started_ts = current_ts

    cmd_step_id, cmd_text = _extract_step_command_from_line(line_text)
    if cmd_step_id is not None:
        state.active_step_id = cmd_step_id
        state.active_command = cmd_text or state.active_command
        state.active_phase = "runner_dispatch"
        state.active_phase_started_ts = current_ts
        state.saw_runner_start = True

    if "[status] starting command" in stripped:
        state.active_phase = "spawning_process"
        state.active_phase_started_ts = current_ts
        state.saw_runner_start = True

    pid = _extract_pid_from_line(line_text)
    if pid is not None:
        state.active_pid = pid
        state.first_pid_observed = True
        state.active_phase = "running_process"
        state.active_phase_started_ts = current_ts


def reset_execution_run_state(run: dict[str, Any]) -> None:
    """Reset transient execution fields at the start of one execution cycle."""

    now_ts = time.time()
    run["status"] = "running"
    run["execution_started"] = True
    run["error"] = ""
    run["missing_tools_detected"] = []
    run["missing_reference_detected"] = []
    run["missing_sample_groups"] = []
    run["missing_sample_group_signals"] = []
    run["observed_sample_groups"] = []
    run["observed_sample_group_sources"] = {}
    run["no_fastq_found"] = False
    run["empty_bams_detected"] = []
    run["policy_block_detected"] = False
    run["validation_block_detected"] = False
    run["stale_tmp_cache_detected"] = False
    run["format_input_error_detected"] = False
    run["execution_stalled_detected"] = False
    run["failure_signatures"] = []
    run["stall_event_emitted"] = False
    run["last_executor_event_ts"] = now_ts
    run["last_queue_activity_ts"] = now_ts
    run["stream_counters"] = {"stdout_lines": 0, "stderr_lines": 0, "live_lines": 0}
    run["recent_stream_markers"] = []
    run["last_artifact_probe"] = {}


__all__ = [
    "active_step_completion_evidence",
    "has_live_executor_process",
    "reset_execution_run_state",
    "should_drain_completed_execution",
    "startup_phase_grace_seconds",
    "update_active_execution_context",
]
