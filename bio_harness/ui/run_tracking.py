"""Run-tracking helpers for the Streamlit UI.

This module keeps lightweight execution-tracking logic out of ``app.py``.
The helpers here only manage in-memory UI run state derived from executor log
lines; they do not own planning or execution policy.
"""

from __future__ import annotations

import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from bio_harness.core.constants import PID_STATUS_RE


def append_tail(chunks: deque[str], line: str, max_bytes: int = 65536) -> None:
    """Append one line to a bounded UTF-8 tail buffer.

    Args:
        chunks: Existing in-memory chunk deque.
        line: Text to append.
        max_bytes: Maximum encoded byte size to retain.
    """
    chunks.append(line)
    total = sum(len(chunk.encode("utf-8", errors="ignore")) for chunk in chunks)
    while chunks and total > max_bytes:
        dropped = chunks.popleft()
        total -= len(dropped.encode("utf-8", errors="ignore"))


def append_run_log(run: dict[str, Any], line: str, *, max_lines: int = 2500) -> None:
    """Append one UI-visible log line to a run buffer.

    Args:
        run: Mutable run state dictionary.
        line: Log line to append.
        max_lines: Maximum number of in-memory lines to retain.
    """
    logs = run.setdefault("logs", [])
    logs.append(line)
    if len(logs) > max_lines:
        del logs[: len(logs) - max_lines]


def summarize_command_for_ui(command: str) -> str:
    """Summarize one shell command into a user-facing progress label.

    Args:
        command: Raw shell command text.

    Returns:
        Short human-readable status text.
    """
    command_text = (command or "").strip()
    lowered = command_text.lower()
    if "genomegenerate" in lowered and "star" in lowered:
        match = re.search(r"--genomeDir\s+([^\s]+)", command_text)
        genome_dir = match.group(1) if match else "STAR genome index"
        return f"Producing STAR index in {genome_dir}"
    if "star" in lowered and "--readfilesin" in lowered:
        match = re.search(r"--readFilesIn\s+\"?([^\s\"]+)\"?", command_text)
        read_one = Path(match.group(1)).name if match else "sample"
        return f"Aligning reads for {read_one} with STAR"
    if "rmats.py" in lowered or re.search(r"\brmats\b", lowered):
        return "Running rMATS differential splicing analysis"
    if "fastqc" in lowered:
        return "Running FastQC quality checks"
    if "find" in lowered and ("fastq" in lowered or ".fq" in lowered):
        return "Scanning input FASTQ files and building manifest"
    if "grep -e" in lowered or ("grep" in lowered and ("_s1_" in lowered or "_s6_" in lowered)):
        return "Selecting control and treatment sample lists"
    first_token = command_text.split()[0] if command_text else "command"
    return f"Running `{first_token}`"


def maybe_progress_update(command: str, line: str) -> str:
    """Translate raw tool output into a cleaner UI progress note.

    Args:
        command: Active shell command for the step.
        line: One stdout/stderr/status line.

    Returns:
        Optional human-readable progress message.
    """
    line_text = (line or "").strip()
    lowered = line_text.lower()
    if not line_text:
        return ""
    if "Approx " in line_text and "% complete for" in line_text:
        return f"FastQC progress: {line_text}"
    if "starting to generate Genome files" in line_text:
        return "STAR: generating genome files"
    if "starting to sort Suffix Array" in line_text:
        return "STAR: sorting suffix array"
    if "writing Genome to disk" in line_text or "writing SAindex to disk" in line_text:
        return "STAR: writing index files to disk"
    if "finished successfully" in line_text:
        return "Step finished successfully"
    if "command not found" in lowered:
        return f"Missing dependency detected: {line_text}"
    if "could not open file" in lowered or "empty filename" in lowered:
        return f"Input validation issue: {line_text}"
    if "running pid=" in lowered and command:
        return summarize_command_for_ui(command)
    return ""


def init_process_tracker(run: dict[str, Any]) -> None:
    """Ensure process-tracker keys exist on one UI run object.

    Args:
        run: Mutable UI run state dictionary.
    """
    run.setdefault("process_tracker", {})
    run.setdefault("process_order", [])
    run.setdefault("async_status", "idle")
    run.setdefault("last_process_update_ts", 0.0)


def run_has_live_executor_process(run: dict[str, Any]) -> bool:
    """Return whether the run still has a live executor PID.

    Args:
        run: Mutable UI run state dictionary.

    Returns:
        True when at least one tracked executor process is still live.
    """
    for key in run.get("process_order", []):
        process = run.get("process_tracker", {}).get(key, {})
        if not isinstance(process, dict):
            continue
        if str(process.get("status", "")).lower() != "running":
            continue
        pid = process.get("active_pid")
        if not isinstance(pid, int):
            pid = extract_pid_from_status_text(process.get("status_text", ""))
        if isinstance(pid, int) and _is_pid_live(pid):
            return True
    return False


def update_process_tracker_from_log(run: dict[str, Any], line: str) -> None:
    """Update one UI run tracker from an execution log line.

    Args:
        run: Mutable UI run state dictionary.
        line: One emitted execution log line.
    """
    init_process_tracker(run)
    stripped = line.strip()

    start_match = re.match(r"--- Executing Step (\d+): ([^ ]+) ---", stripped)
    if start_match:
        step_id = int(start_match.group(1))
        tool_name = start_match.group(2)
        process = ensure_process(run, step_id, tool_name)
        process["status"] = "running"
        process["updated_at"] = datetime.now().isoformat()
        process["status_text"] = f"Step {step_id} started"
        run["async_status"] = "running"
        _set_step_status(run, step_id, "running")
        return

    finish_match = re.match(r"--- Step (\d+) \(([^)]+)\) finished ---", stripped)
    if finish_match:
        step_id = int(finish_match.group(1))
        process = ensure_process(run, step_id, finish_match.group(2))
        process["status"] = "completed"
        process["active_pid"] = None
        process["updated_at"] = datetime.now().isoformat()
        process["status_text"] = f"Step {step_id} completed"
        _set_step_status(run, step_id, "completed")
        if run.get("step_statuses") and 0 < step_id <= len(run["step_statuses"]):
            run["next_step_idx"] = min(step_id, len(run["step_statuses"]))
        return

    fail_match = re.match(r"Step (\d+) \(([^)]+)\) failed with exit code (\d+)", stripped)
    if fail_match:
        step_id = int(fail_match.group(1))
        process = ensure_process(run, step_id, fail_match.group(2))
        process["status"] = "failed"
        process["active_pid"] = None
        process["updated_at"] = datetime.now().isoformat()
        process["status_text"] = f"Step failed with exit code {fail_match.group(3)}"
        _set_step_status(run, step_id, "failed")
        run["status"] = "failed"
        run["error"] = f"Step {step_id} failed with exit code {fail_match.group(3)}"
        return

    policy_match = re.match(r"Step (\d+) \(([^)]+)\) blocked by policy with exit code (\d+)", stripped)
    if policy_match:
        step_id = int(policy_match.group(1))
        process = ensure_process(run, step_id, policy_match.group(2))
        process["status"] = "failed"
        process["active_pid"] = None
        process["updated_at"] = datetime.now().isoformat()
        process["status_text"] = f"Step blocked by policy (exit {policy_match.group(3)})"
        _set_step_status(run, step_id, "failed")
        run["status"] = "failed"
        run["error"] = f"Step {step_id} blocked by policy"
        run["policy_block_detected"] = True
        return

    blocked_match = re.match(r"Step (\d+) blocked by validation agent\.", stripped)
    if blocked_match:
        step_id = int(blocked_match.group(1))
        existing = run["process_tracker"].get(str(step_id))
        tool_name = existing["tool_name"] if isinstance(existing, dict) else "bash_run"
        process = ensure_process(run, step_id, tool_name)
        process["status"] = "failed"
        process["active_pid"] = None
        process["updated_at"] = datetime.now().isoformat()
        process["status_text"] = "Blocked by validation agent"
        _set_step_status(run, step_id, "failed")
        run["status"] = "failed"
        run["error"] = f"Step {step_id} blocked by validation agent"
        run["validation_block_detected"] = True
        return

    command_match = re.match(r"\[Step (\d+) Output\] \[command\] (.*)$", stripped)
    if command_match:
        step_id = int(command_match.group(1))
        existing = run["process_tracker"].get(str(step_id))
        tool_name = existing["tool_name"] if isinstance(existing, dict) else "bash_run"
        process = ensure_process(run, step_id, tool_name)
        command = command_match.group(2).strip()
        process["command"] = command
        process["title"] = summarize_command_for_ui(command)
        process["status_text"] = process["title"]
        process["updated_at"] = datetime.now().isoformat()
        return

    output_match = re.match(r"\[Step (\d+) Output\] \[(stdout|stderr|status)\] (.*)$", stripped)
    if not output_match:
        return

    step_id = int(output_match.group(1))
    channel = output_match.group(2)
    message = output_match.group(3)
    existing = run["process_tracker"].get(str(step_id))
    tool_name = existing["tool_name"] if isinstance(existing, dict) else "bash_run"
    process = ensure_process(run, step_id, tool_name)
    if channel == "stdout":
        append_tail(process["stdout_tail"], message + "\n", max_bytes=16384)
    elif channel == "stderr":
        append_tail(process["stderr_tail"], message + "\n", max_bytes=16384)
    elif channel == "status":
        process["last_heartbeat_ts"] = time.time()
        pid = extract_pid_from_status_text(message)
        if pid is not None:
            process["active_pid"] = pid
    progress_note = maybe_progress_update(process.get("command", ""), message)
    if progress_note:
        process["status_text"] = progress_note
    process["updated_at"] = datetime.now().isoformat()


def parse_log_channel(line: str) -> tuple[str, str]:
    """Return the normalized channel and body for one log line.

    Args:
        line: Raw execution log line.

    Returns:
        Tuple of ``(channel, body)`` where channel is one of ``stdout``,
        ``stderr``, or ``live``.
    """
    match = re.search(r"^\[Step\s+\d+\s+Output\]\s+(\[(stdout|stderr|status)\]\s+.*)$", line)
    body = match.group(1) if match else line
    if body.startswith("[stdout] "):
        return "stdout", body[len("[stdout] ") :]
    if body.startswith("[stderr] "):
        return "stderr", body[len("[stderr] ") :]
    return "live", body


def extract_pid_from_status_text(text: str) -> int | None:
    """Extract an executor PID from one status line.

    Args:
        text: Raw status text.

    Returns:
        Parsed PID when present, otherwise ``None``.
    """
    match = PID_STATUS_RE.search(str(text or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _is_pid_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return False
        status = str(process.status()).lower()
        return status not in {"zombie", "dead"}
    except Exception:
        return False


def ensure_process(run: dict[str, Any], step_id: int, tool_name: str) -> dict[str, Any]:
    """Return the tracked process record for one step, creating it if needed.

    Args:
        run: Mutable UI run state dictionary.
        step_id: Step identifier.
        tool_name: Tool label for the step.

    Returns:
        Mutable per-step process-tracker dictionary.
    """
    init_process_tracker(run)
    key = str(step_id)
    process = run["process_tracker"].get(key)
    if process is None:
        process = {
            "step_id": step_id,
            "tool_name": tool_name,
            "status": "pending",
            "title": f"Step {step_id}: {tool_name}",
            "command": "",
            "status_text": "",
            "active_pid": None,
            "last_heartbeat_ts": 0.0,
            "updated_at": datetime.now().isoformat(),
            "stdout_tail": deque(maxlen=600),
            "stderr_tail": deque(maxlen=600),
            "event_tail": deque(maxlen=120),
        }
        run["process_tracker"][key] = process
        run["process_order"].append(key)
    return process


def _set_step_status(run: dict[str, Any], step_id: int, status: str) -> None:
    if not run.get("step_statuses"):
        return
    if 0 < step_id <= len(run["step_statuses"]):
        run["step_statuses"][step_id - 1] = status
