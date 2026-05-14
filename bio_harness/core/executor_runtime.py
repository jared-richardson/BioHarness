"""Durable executor runtime tracking for UI/backend reconnects."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import psutil

from bio_harness.core.schemas import (
    ExecutorRuntimeSchema,
    safe_parse_executor_runtime,
)


def executor_runtime_path(run_files: Mapping[str, Any]) -> Path:
    """Return the runtime-tracking file path for one run."""

    raw = str(run_files.get("executor_runtime", "") or "").strip()
    if raw:
        return Path(raw)
    run_dir = Path(str(run_files.get("run_dir", "") or "")).expanduser()
    return run_dir / "executor.json"


def load_executor_runtime(run_files: Mapping[str, Any]) -> dict[str, Any]:
    """Load the current executor runtime payload."""

    path = executor_runtime_path(run_files)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    parsed = safe_parse_executor_runtime(payload)
    return parsed.model_dump(mode="json") if parsed is not None else payload


def write_executor_runtime(run_files: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist one executor runtime payload."""

    path = executor_runtime_path(run_files)
    runtime = ExecutorRuntimeSchema.model_validate(dict(payload))
    path.write_text(runtime.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return runtime.model_dump(mode="json")


def start_executor_runtime(
    run_files: Mapping[str, Any],
    *,
    run_id: str,
    pid: int | None = None,
) -> dict[str, Any]:
    """Record that execution has started for one run."""

    now = datetime.now().isoformat()
    runtime = load_executor_runtime(run_files)
    payload = {
        "run_id": run_id,
        "pid": int(pid or os.getpid()),
        "status": "running",
        "started_at": str(runtime.get("started_at", "") or now),
        "updated_at": now,
        "finished_at": None,
        "error": "",
        "last_event_type": str(runtime.get("last_event_type", "") or ""),
        "last_step_id": runtime.get("last_step_id"),
        "last_tool_name": str(runtime.get("last_tool_name", "") or ""),
    }
    return write_executor_runtime(run_files, payload)


def heartbeat_executor_runtime(
    run_files: Mapping[str, Any],
    *,
    run_id: str,
    event_type: str = "",
    step_id: int | None = None,
    tool_name: str = "",
) -> dict[str, Any]:
    """Refresh the executor heartbeat for one run."""

    current = load_executor_runtime(run_files)
    now = datetime.now().isoformat()
    payload = {
        "run_id": run_id,
        "pid": int(current.get("pid", os.getpid()) or os.getpid()),
        "status": str(current.get("status", "running") or "running"),
        "started_at": str(current.get("started_at", "") or now),
        "updated_at": now,
        "finished_at": current.get("finished_at"),
        "error": str(current.get("error", "") or ""),
        "last_event_type": event_type or str(current.get("last_event_type", "") or ""),
        "last_step_id": step_id if step_id is not None else current.get("last_step_id"),
        "last_tool_name": tool_name or str(current.get("last_tool_name", "") or ""),
    }
    return write_executor_runtime(run_files, payload)


def finish_executor_runtime(
    run_files: Mapping[str, Any],
    *,
    run_id: str,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    """Mark one executor runtime as completed or failed."""

    current = load_executor_runtime(run_files)
    now = datetime.now().isoformat()
    payload = {
        "run_id": run_id,
        "pid": int(current.get("pid", os.getpid()) or os.getpid()),
        "status": status,
        "started_at": str(current.get("started_at", "") or now),
        "updated_at": now,
        "finished_at": now,
        "error": error,
        "last_event_type": str(current.get("last_event_type", "") or ""),
        "last_step_id": current.get("last_step_id"),
        "last_tool_name": str(current.get("last_tool_name", "") or ""),
    }
    return write_executor_runtime(run_files, payload)


def executor_runtime_is_live(
    run_files: Mapping[str, Any],
    *,
    stale_after_seconds: int = 90,
) -> bool:
    """Return whether a persisted executor runtime still appears live."""

    runtime = safe_parse_executor_runtime(load_executor_runtime(run_files))
    if runtime is None:
        return False
    if str(runtime.status).strip().lower() != "running":
        return False
    try:
        process = psutil.Process(int(runtime.pid))
        if not process.is_running():
            return False
        if str(process.status()).lower() in {"zombie", "dead"}:
            return False
    except Exception:
        return False
    updated_text = runtime.updated_at or runtime.started_at
    try:
        updated_at = datetime.fromisoformat(updated_text)
    except Exception:
        return False
    age_seconds = (datetime.now() - updated_at).total_seconds()
    return age_seconds <= max(15, int(stale_after_seconds))
