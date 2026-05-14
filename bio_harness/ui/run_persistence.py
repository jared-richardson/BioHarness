"""Persistent run-artifact helpers for the Streamlit UI.

These helpers centralize filesystem-backed run state used by the UI so that
``app.py`` can focus on interaction flow instead of artifact bookkeeping.
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import logging

from bio_harness.core.executor_runtime import executor_runtime_is_live
from bio_harness.core.run_artifacts import init_run_artifacts, write_exit, write_state
from bio_harness.core.schemas import (
    TERMINAL_RUN_STATUSES,
    safe_parse_manifest,
    safe_parse_run_event,
    safe_parse_run_state,
)
from bio_harness.ui.bioagentbench_ui_support import ui_benchmark_policy
from bio_harness.ui.chat_sessions import build_chat_session_id

_logger = logging.getLogger(__name__)


def _normalize_auto_repair_promotions(promotions: Any) -> list[dict[str, Any]]:
    """Normalize promotion notes into the schema-backed dict form.

    Older UI runs persisted promotions as plain strings. Keep those runs
    readable and writable by upgrading legacy entries into ``{"note": ...}``
    records on load and before persistence.
    """
    normalized: list[dict[str, Any]] = []
    if not isinstance(promotions, list):
        return normalized
    for item in promotions:
        if isinstance(item, dict):
            record = {str(key): value for key, value in item.items() if str(key).strip()}
            if record:
                normalized.append(record)
            continue
        note = str(item or "").strip()
        if note:
            normalized.append({"note": note})
    return normalized


def init_run_files(run: dict[str, Any], workspace_root: Path) -> dict[str, str]:
    """Create run artifacts and attach them to one UI run dictionary.

    Args:
        run: Mutable UI run state dictionary.
        workspace_root: Workspace root used for run artifact creation.

    Returns:
        Mapping of run artifact names to string paths.
    """
    files = init_run_artifacts(workspace_root, run.get("user_request", "task"))
    run_id = str(files["run_id"])
    run_dir = str(files["run_dir"])
    run["run_uid"] = run_id
    run["run_dir"] = run_dir
    run["run_files"] = {key: str(value) for key, value in files.items()}
    run["recent_events"] = []
    run["live_tail"] = deque(maxlen=4000)
    run["stdout_tail"] = deque(maxlen=4000)
    run["stderr_tail"] = deque(maxlen=4000)
    run["events_tail"] = deque(maxlen=100)
    run["script_exports"] = []
    run["step_updates_posted"] = []
    run["rmats_failed_detected"] = False
    run["policy_block_detected"] = False
    run["validation_block_detected"] = False
    run["stale_tmp_cache_detected"] = False
    run["format_input_error_detected"] = False
    run["recovery_verification_required"] = False
    run["execution_options"] = run.get("execution_options", {})
    run["auto_repair_attempts"] = {}
    run["auto_repair_history"] = []
    run["auto_repair_promotions"] = []
    run["auto_repair_last_class"] = ""
    run["plan_contract"] = run.get("plan_contract", {})
    run["contract_validation"] = {}
    run["process_tracker"] = {}
    run["process_order"] = []
    run["async_status"] = "running"
    run["last_process_update_ts"] = 0.0
    run["last_executor_event_ts"] = 0.0
    run["last_queue_activity_ts"] = time.time()
    run["stall_event_emitted"] = False
    run["last_reconcile_at"] = 0.0
    run["last_chat_result_signature"] = ""
    return run["run_files"]


def persist_run_state(run: dict[str, Any]) -> None:
    """Write one run's current state JSON to disk.

    Args:
        run: Mutable UI run state dictionary.
    """
    files = run.get("run_files", {})
    if not files:
        return
    state = {
        "run_id": run.get("run_uid", ""),
        "status": run.get("status", "unknown"),
        "chat_session_id": run.get("chat_session_id", ""),
        "error": run.get("error", ""),
        "next_step_idx": run.get("next_step_idx", 0),
        "step_statuses": run.get("step_statuses", []),
        "auto_repair_attempts": run.get("auto_repair_attempts", {}),
        "auto_repair_last_class": run.get("auto_repair_last_class", ""),
        "auto_repair_history": run.get("auto_repair_history", []),
        "auto_repair_promotions": _normalize_auto_repair_promotions(
            run.get("auto_repair_promotions", [])
        ),
        "plan_contract": run.get("plan_contract", {}),
        "contract_validation": run.get("contract_validation", {}),
        "execution_options": run.get("execution_options", {}),
        "benchmark_policy": run.get("benchmark_policy", ui_benchmark_policy()),
        "recovery_verification_required": bool(run.get("recovery_verification_required", False)),
        "policy_block_detected": bool(run.get("policy_block_detected", False)),
        "validation_block_detected": bool(run.get("validation_block_detected", False)),
        "stale_tmp_cache_detected": bool(run.get("stale_tmp_cache_detected", False)),
        "format_input_error_detected": bool(run.get("format_input_error_detected", False)),
        "planner_status": str(run.get("planner_status", "")).strip(),
        "planning_started_at": str(run.get("planning_started_at", "")).strip(),
        "planning_finished_at": str(run.get("planning_finished_at", "")).strip(),
        "planner_error": str(run.get("planner_error", "")).strip(),
        "requested_data_root": str(run.get("requested_data_root", "")).strip(),
        "selected_dir": str(run.get("selected_dir", "")).strip(),
        "updated_at": datetime.now().isoformat(),
    }
    write_state(Path(files["state"]), state)


def write_terminal_artifacts_if_needed(run: dict[str, Any]) -> None:
    """Write exit and summary files once a UI run reaches a terminal state.

    Args:
        run: Mutable UI run state dictionary.
    """
    run_files = run.get("run_files", {})
    if not run_files:
        return
    status = str(run.get("status", "")).strip().lower()
    if status not in TERMINAL_RUN_STATUSES:
        return
    exit_path = Path(run_files["exit"])
    current_status = ""
    if exit_path.exists():
        try:
            current_status = str(json.loads(exit_path.read_text(encoding="utf-8")).get("status", "")).strip().lower()
        except Exception:
            current_status = ""
    if current_status == status:
        return
    write_exit(
        exit_path,
        {
            "run_id": run.get("run_uid", ""),
            "status": run.get("status", ""),
            "error": run.get("error", ""),
            "finished_at": datetime.now().isoformat(),
        },
    )
    Path(run_files["summary"]).write_text(
        (
            "# Run Summary\n\n"
            f"- Run ID: {run.get('run_uid', '')}\n"
            f"- Status: {run.get('status', '')}\n"
            f"- Error: {run.get('error', '') or 'none'}\n"
            f"- Updated: {datetime.now().isoformat()}\n"
        ),
        encoding="utf-8",
    )


def load_recent_events(run: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    """Load the most recent structured events for one run.

    Args:
        run: Mutable UI run state dictionary.
        limit: Maximum number of events to return.

    Returns:
        Parsed event dictionaries ordered from oldest to newest.
    """
    events_path = _resolve_events_path(run)
    if events_path is None or not events_path.exists():
        return []
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    events: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        parsed = safe_parse_run_event(payload)
        events.append(parsed.model_dump(mode="json") if parsed is not None else payload)
    return events


def load_all_events(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Load every structured event for one run.

    Args:
        run: Mutable UI run state dictionary.

    Returns:
        Parsed event dictionaries.
    """
    events_path = _resolve_events_path(run)
    if events_path is None or not events_path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        for raw in events_path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            parsed = safe_parse_run_event(payload)
            events.append(parsed.model_dump(mode="json") if parsed is not None else payload)
    except Exception:
        return []
    return events


def parse_event_epoch(ts_text: str) -> float:
    """Convert an ISO timestamp into epoch seconds.

    Args:
        ts_text: ISO-formatted timestamp text.

    Returns:
        Unix timestamp as a float, or ``0.0`` on parse failure.
    """
    if not ts_text:
        return 0.0
    try:
        return datetime.fromisoformat(ts_text).timestamp()
    except Exception:
        return 0.0


def tail_items(seq: object, count: int) -> list[Any]:
    """Return the last ``count`` items from an iterable-like object.

    Args:
        seq: Iterable-like object.
        count: Number of items to keep from the tail.

    Returns:
        Tail items as a list.
    """
    if count <= 0:
        return []
    try:
        return list(seq)[-count:]
    except Exception:
        return []


def read_text_tail(path: Path, max_chars: int = 65536) -> str:
    """Read the tail of a UTF-8 text file.

    Args:
        path: File to read.
        max_chars: Maximum number of characters to return.

    Returns:
        Trailing text content, or an empty string on failure.
    """
    try:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        return text[-max_chars:]
    except Exception:
        return ""


def merge_recent_persisted_runs(
    existing_runs: list[dict[str, Any]],
    *,
    workspace_root: Path,
    limit: int = 12,
) -> tuple[list[dict[str, Any]], int | None]:
    """Merge recent persisted runs from disk into the in-memory run list.

    Args:
        existing_runs: Current in-memory session runs.
        workspace_root: Workspace root that contains the ``runs/`` directory.
        limit: Maximum number of recent run directories to inspect.

    Returns:
        Tuple of ``(merged_runs, suggested_active_run_id)``.
    """
    runs_root = workspace_root / "runs"
    if not runs_root.exists():
        return existing_runs, None

    merged_runs = list(existing_runs)
    run_by_uid = {
        str(run.get("run_uid", "")).strip(): run
        for run in merged_runs
        if str(run.get("run_uid", "")).strip()
    }
    next_id = max((int(run.get("id", 0) or 0) for run in merged_runs), default=0) + 1
    suggested_active: tuple[float, int] | None = None

    run_dirs = sorted(
        [path for path in runs_root.iterdir() if path.is_dir()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: max(1, int(limit))]

    for run_dir in run_dirs:
        manifest = _read_json_file(run_dir / "manifest.json")
        state = _read_json_file(run_dir / "state.json")
        run_uid = str(state.get("run_id", "") or manifest.get("run_id", "")).strip()
        if not run_uid:
            continue
        # Validate against shared schemas; warn on drift.
        if manifest and safe_parse_manifest(manifest) is None:
            _logger.warning("Manifest for run %s in %s failed schema validation.", run_uid, run_dir.name)
        if state and safe_parse_run_state(state) is None:
            _logger.warning("State for run %s in %s failed schema validation.", run_uid, run_dir.name)

        existing = run_by_uid.get(run_uid)
        if existing is None:
            existing = _build_rehydrated_run(next_id, run_dir=run_dir, manifest=manifest, state=state)
            merged_runs.append(existing)
            run_by_uid[run_uid] = existing
            next_id += 1
        else:
            _update_run_from_disk(existing, run_dir=run_dir, manifest=manifest, state=state)

        candidate_ts = parse_event_epoch(str(state.get("updated_at", "")).strip())
        if candidate_ts <= 0:
            candidate_ts = run_dir.stat().st_mtime
        candidate = (candidate_ts, int(existing.get("id", 0) or 0))
        if not _is_stale_planning_candidate(state, run_dir=run_dir):
            if suggested_active is None or candidate > suggested_active:
                suggested_active = candidate

    return merged_runs, (suggested_active[1] if suggested_active is not None else None)


def _resolve_events_path(run: dict[str, Any]) -> Path | None:
    events_path = run.get("run_files", {}).get("events")
    if not events_path:
        return None
    return Path(events_path)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _run_files_for_dir(run_dir: Path) -> dict[str, str]:
    return {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "state": str(run_dir / "state.json"),
        "events": str(run_dir / "events.jsonl"),
        "stdout": str(run_dir / "stdout.log"),
        "stderr": str(run_dir / "stderr.log"),
        "exec": str(run_dir / "execution.log"),
        "exit": str(run_dir / "exit.json"),
        "manifest": str(run_dir / "manifest.json"),
        "assistance_manifest": str(run_dir / "assistance_manifest.json"),
        "summary": str(run_dir / "summary.md"),
        "path_decisions": str(run_dir / "path_decisions.json"),
        "executor_runtime": str(run_dir / "executor.json"),
        "planner": str(run_dir / "planner"),
    }


def _build_rehydrated_run(
    run_id: int,
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    run_files = _run_files_for_dir(run_dir)
    executor_live = executor_runtime_is_live(run_files)
    run = {
        "id": int(run_id),
        "user_request": str(manifest.get("user_request", "") or manifest.get("task_text", "") or run_dir.name),
        "plan": None,
        "plan_kind": str(manifest.get("plan_kind", "executable") or "executable"),
        "adjustments": [],
        "status": str(state.get("status", "draft") or "draft"),
        "logs": [],
        "error": str(state.get("error", "")).strip(),
        "next_step_idx": int(state.get("next_step_idx", 0) or 0),
        "step_statuses": list(state.get("step_statuses", []) or []),
        "eta_notes": [],
        "conversation": [],
        "context_snapshots": [],
        "model_traces": [],
        "missing_tools_detected": [],
        "remediation_attempted_tools": [],
        "no_fastq_found": False,
        "missing_reference_detected": [],
        "missing_sample_groups": [],
        "empty_bams_detected": [],
        "policy_block_detected": bool(state.get("policy_block_detected", False)),
        "validation_block_detected": bool(state.get("validation_block_detected", False)),
        "stale_tmp_cache_detected": bool(state.get("stale_tmp_cache_detected", False)),
        "format_input_error_detected": bool(state.get("format_input_error_detected", False)),
        "recovery_verification_required": bool(state.get("recovery_verification_required", False)),
        "execution_options": dict(state.get("execution_options", {}) or {}),
        "last_error_feedback": "",
        "run_uid": str(state.get("run_id", "") or manifest.get("run_id", "") or run_dir.name),
        "run_dir": str(run_dir),
        "run_files": run_files,
        "script_exports": [],
        "last_script_export": {},
        "step_updates_posted": [],
        "rmats_failed_detected": False,
        "auto_repair_attempts": dict(state.get("auto_repair_attempts", {}) or {}),
        "auto_repair_history": list(state.get("auto_repair_history", []) or []),
        "auto_repair_promotions": _normalize_auto_repair_promotions(
            state.get("auto_repair_promotions", []) or []
        ),
        "auto_repair_last_class": str(state.get("auto_repair_last_class", "")).strip(),
        "plan_contract": dict(state.get("plan_contract", {}) or {}),
        "contract_validation": dict(state.get("contract_validation", {}) or {}),
        "live_tail": deque(maxlen=4000),
        "stdout_tail": deque(maxlen=4000),
        "stderr_tail": deque(maxlen=4000),
        "events_tail": deque(load_recent_events({"run_files": _run_files_for_dir(run_dir)}, limit=100), maxlen=100),
        "auto_recovered_incomplete_plan": False,
        "last_heartbeat_event_ts": 0.0,
        "process_tracker": {},
        "process_order": [],
        "async_status": "running" if executor_live else "idle",
        "last_process_update_ts": 0.0,
        "last_executor_event_ts": 0.0,
        "last_queue_activity_ts": 0.0,
        "stall_event_emitted": False,
        "last_reconcile_at": 0.0,
        "last_chat_result_signature": "",
        "chat_session_id": str(state.get("chat_session_id", "") or manifest.get("chat_session_id", "")).strip()
        or build_chat_session_id(int(run_id)),
        "benchmark_policy": str(state.get("benchmark_policy", "") or manifest.get("benchmark_policy", "")).strip(),
        "planner_status": str(state.get("planner_status", "")).strip(),
        "planning_started_at": str(state.get("planning_started_at", "")).strip(),
        "planning_finished_at": str(state.get("planning_finished_at", "")).strip(),
        "planner_error": str(state.get("planner_error", "")).strip(),
        "requested_data_root": str(state.get("requested_data_root", "") or manifest.get("requested_data_root", "")).strip(),
        "selected_dir": str(state.get("selected_dir", "") or manifest.get("selected_dir", "")).strip(),
    }
    return run


def _update_run_from_disk(
    run: dict[str, Any],
    *,
    run_dir: Path,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> None:
    run["run_uid"] = str(state.get("run_id", "") or manifest.get("run_id", "") or run.get("run_uid", "")).strip()
    run["run_dir"] = str(run_dir)
    run["run_files"] = _run_files_for_dir(run_dir)
    executor_live = executor_runtime_is_live(run["run_files"])
    run["status"] = str(state.get("status", run.get("status", "draft")) or run.get("status", "draft"))
    run["error"] = str(state.get("error", run.get("error", ""))).strip()
    run["step_statuses"] = list(state.get("step_statuses", run.get("step_statuses", [])) or [])
    run["next_step_idx"] = int(state.get("next_step_idx", run.get("next_step_idx", 0)) or 0)
    run["auto_repair_attempts"] = dict(state.get("auto_repair_attempts", run.get("auto_repair_attempts", {})) or {})
    run["auto_repair_history"] = list(state.get("auto_repair_history", run.get("auto_repair_history", [])) or [])
    run["auto_repair_promotions"] = _normalize_auto_repair_promotions(
        state.get("auto_repair_promotions", run.get("auto_repair_promotions", [])) or []
    )
    run["auto_repair_last_class"] = str(state.get("auto_repair_last_class", run.get("auto_repair_last_class", ""))).strip()
    run["plan_contract"] = dict(state.get("plan_contract", run.get("plan_contract", {})) or {})
    run["contract_validation"] = dict(state.get("contract_validation", run.get("contract_validation", {})) or {})
    run["benchmark_policy"] = str(state.get("benchmark_policy", run.get("benchmark_policy", "")) or run.get("benchmark_policy", "")).strip()
    run["chat_session_id"] = str(state.get("chat_session_id", "") or manifest.get("chat_session_id", "") or run.get("chat_session_id", "")).strip()
    run["planner_status"] = str(state.get("planner_status", run.get("planner_status", ""))).strip()
    run["planning_started_at"] = str(state.get("planning_started_at", run.get("planning_started_at", ""))).strip()
    run["planning_finished_at"] = str(state.get("planning_finished_at", run.get("planning_finished_at", ""))).strip()
    run["planner_error"] = str(state.get("planner_error", run.get("planner_error", ""))).strip()
    run["requested_data_root"] = str(state.get("requested_data_root", "") or manifest.get("requested_data_root", "") or run.get("requested_data_root", "")).strip()
    run["selected_dir"] = str(state.get("selected_dir", "") or manifest.get("selected_dir", "") or run.get("selected_dir", "")).strip()
    run["events_tail"] = deque(load_recent_events(run, limit=100), maxlen=100)
    run["async_status"] = "running" if executor_live else run.get("async_status", "idle")


def _is_stale_planning_candidate(state: dict[str, Any], *, run_dir: Path) -> bool:
    """Return whether one persisted run is a legacy/orphaned planning stub.

    These runs predate the durable planner status files and can otherwise
    dominate reconnect selection forever because they remain nonterminal.
    """
    status = str(state.get("status", "")).strip().lower()
    if status not in {"planning", "planned"}:
        return False
    planner_status = str(state.get("planner_status", "")).strip().lower()
    if planner_status:
        return False
    if str(state.get("planning_started_at", "")).strip():
        return False
    if str(state.get("planning_finished_at", "")).strip():
        return False
    if list(state.get("step_statuses", []) or []):
        return False
    planner_dir = run_dir / "planner"
    if (planner_dir / "status.json").exists():
        return False
    if (planner_dir / "result.json").exists():
        return False
    return True
