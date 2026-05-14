"""Helpers for durable planning-phase UI runs.

These helpers persist planning state to disk before execution starts and keep
one process-wide registry of active planner jobs so browser refreshes do not
silently orphan work that already moved off the main chat request path.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping

import logging

from bio_harness.core.run_artifacts import (
    append_event,
    init_run_artifacts,
    write_exit,
    write_manifest,
)
from bio_harness.core.schemas import (
    ARTIFACT_SCHEMA_VERSION,
    PlannerResultSchema,
    PlannerStatusSchema,
    safe_parse_planner_result,
    safe_parse_planner_status,
)

_logger = logging.getLogger(__name__)


PLANNER_HEARTBEAT_SECONDS = 10


@dataclass
class _PlannerJob:
    """Track one in-process background planner job."""

    run_uid: str
    thread: threading.Thread
    started_at: float
    updated_at: float
    timeout_seconds: int
    cancel_event: threading.Event
    status: str = "planning"
    error: str = ""
    result_ready: bool = False


_PLANNER_JOBS: dict[str, _PlannerJob] = {}
_PLANNER_LOCK = threading.Lock()


def ensure_planning_run_initialized(
    run: MutableMapping[str, Any],
    *,
    workspace_root: Path,
    selected_dir: str,
    requested_data_root: str,
    execution_options: Mapping[str, Any],
    benchmark_policy: str,
) -> dict[str, str]:
    """Create or refresh one planning-phase run artifact bundle.

    Args:
        run: Mutable run state dict stored in Streamlit session state.
        workspace_root: Workspace root used for run artifact creation.
        selected_dir: Current selected directory shown in the UI.
        requested_data_root: Current resolved data root for the run.
        execution_options: Execution options that will later be used by the run.
        benchmark_policy: Active benchmark policy for the run.

    Returns:
        Mapping of run artifact names to string paths.
    """
    run_files = run.get("run_files", {})
    if not run_files:
        files = init_run_artifacts(
            workspace_root,
            str(run.get("user_request", "task")),
            initial_exit_status="planning",
        )
        run["run_uid"] = str(files["run_id"])
        run["run_dir"] = str(files["run_dir"])
        run_files = {key: str(value) for key, value in files.items()}
        run["run_files"] = run_files

    planner_started_at = str(run.get("planning_started_at", "")).strip() or datetime.now().isoformat()
    run["planning_started_at"] = planner_started_at
    run["planner_status"] = "planning"
    run["planner_error"] = ""

    manifest_path = Path(run_files["manifest"])
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        if isinstance(existing, dict):
            manifest.update(existing)

    manifest.update(
        {
            "run_id": run.get("run_uid", ""),
            "plan_id": run.get("id"),
            "plan_kind": run.get("plan_kind", "executable"),
            "user_request": str(run.get("user_request", "")).strip(),
            "workspace_root": str(workspace_root),
            "selected_dir": str(selected_dir),
            "requested_data_root": str(requested_data_root),
            "execution_options": dict(execution_options),
            "benchmark_policy": str(benchmark_policy),
            "chat_session_id": str(run.get("chat_session_id", "")).strip(),
            "planning_started_at": planner_started_at,
        }
    )
    manifest.setdefault("created_at", datetime.now().isoformat())
    write_manifest(manifest_path, manifest)

    write_planner_status(
        run_files,
        {
            "run_id": run.get("run_uid", ""),
            "status": "planning",
            "started_at": planner_started_at,
            "updated_at": datetime.now().isoformat(),
            "error": "",
            "timeout_seconds": int(run.get("planning_timeout_seconds", 0) or 0),
            "result_ready": planner_result_path(run_files).exists(),
        },
    )
    write_exit(
        Path(run_files["exit"]),
        {
            "run_id": run.get("run_uid", ""),
            "status": "planning",
            "started_at": planner_started_at,
        },
    )

    if not bool(run.get("planning_event_written", False)):
        append_event(
            Path(run_files["events"]),
            run_id=str(run.get("run_uid", "")),
            step_id=None,
            agent="PlannerAgent",
            event_type="PLAN_STARTED",
            severity="info",
            payload={"message": "Execution planning started from chat UI."},
        )
        run["planning_event_written"] = True

    return run_files


def planner_status_path(run_files: Mapping[str, Any]) -> Path:
    """Return the planner status JSON path for one run-files mapping."""
    return Path(str(run_files["planner"])) / "status.json"


def planner_result_path(run_files: Mapping[str, Any]) -> Path:
    """Return the persisted planner result JSON path for one run-files mapping."""
    return Path(str(run_files["planner"])) / "result.json"


def write_planner_status(run_files: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    """Write the planner status payload for one run."""
    rendered = PlannerStatusSchema.model_validate(dict(payload)).model_dump(mode="json")
    planner_status_path(run_files).write_text(
        json.dumps(rendered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_planner_status(run_files: Mapping[str, Any]) -> dict[str, Any]:
    """Load the planner status payload for one run.

    Args:
        run_files: Run artifact mapping.

    Returns:
        Planner status payload, or ``{}`` when it does not exist.
    """
    path = planner_status_path(run_files)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    # Validate against shared schema; warn on drift but don't block.
    parsed = safe_parse_planner_status(payload)
    if parsed is None and payload.get("status"):
        _logger.warning(
            "Planner status for run %s failed schema validation; proceeding with raw dict.",
            payload.get("run_id", "?"),
        )
    return payload


def write_planner_result(run_files: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    """Persist one completed planner result payload."""
    raw_payload = dict(payload)
    parsed = safe_parse_planner_result(raw_payload)
    if parsed is not None:
        rendered = parsed.model_dump(mode="json")
    else:
        _logger.warning(
            "Planner result for run %s failed schema validation; persisting raw payload.",
            raw_payload.get("run_id", "?"),
        )
        rendered = dict(raw_payload)
        rendered.setdefault("schema_version", ARTIFACT_SCHEMA_VERSION)
    planner_result_path(run_files).write_text(
        json.dumps(rendered, indent=2, sort_keys=True, default=_json_safe_default) + "\n",
        encoding="utf-8",
    )


def load_planner_result(run_files: Mapping[str, Any]) -> dict[str, Any]:
    """Load one planner result payload from disk."""
    path = planner_result_path(run_files)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    parsed = safe_parse_planner_result(payload)
    return parsed.model_dump(mode="json") if parsed is not None else payload


def launch_planner_job(
    run: MutableMapping[str, Any],
    *,
    planning_fn: Callable[[], Mapping[str, Any]],
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Start a background planner job for one UI run.

    Args:
        run: Mutable UI run mapping containing ``run_uid`` and ``run_files``.
        planning_fn: Callable that builds the executable planning payload.
        timeout_seconds: Maximum planning time before marking the job failed.
        cancel_event: Optional threading event set on timeout or explicit
            cancellation.  When provided, the inner planning callable can
            check ``cancel_event.is_set()`` to exit early.  If omitted a
            fresh event is created automatically.

    Returns:
        ``True`` when a new planner job was started, otherwise ``False`` when
        an existing live job already owns the run.
    """
    run_uid = str(run.get("run_uid", "")).strip()
    run_files = run.get("run_files", {})
    if not run_uid or not run_files:
        return False

    with _PLANNER_LOCK:
        existing = _PLANNER_JOBS.get(run_uid)
        if existing is not None and existing.thread.is_alive():
            return False

    _cancel = cancel_event if cancel_event is not None else threading.Event()

    def _worker() -> None:
        started_at = datetime.now().isoformat()
        write_planner_status(
            run_files,
            {
                "run_id": run_uid,
                "status": "planning",
                "started_at": started_at,
                "updated_at": started_at,
                "error": "",
                "timeout_seconds": int(timeout_seconds),
                "result_ready": False,
            },
        )
        append_event(
            Path(run_files["events"]),
            run_id=run_uid,
            step_id=None,
            agent="PlannerAgent",
            event_type="PLANNER_STARTED",
            severity="info",
            payload={"timeout_seconds": int(timeout_seconds)},
        )

        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def _inner() -> None:
            try:
                result_queue.put(("ok", dict(planning_fn())))
            except Exception as exc:  # pragma: no cover - exercised via outer surface
                result_queue.put(("err", exc))

        inner = threading.Thread(target=_inner, daemon=True)
        inner.start()
        started_ts = time.time()
        next_heartbeat_ts = started_ts + PLANNER_HEARTBEAT_SECONDS
        timed_out = False

        while inner.is_alive():
            remaining = max(0.1, (started_ts + float(timeout_seconds)) - time.time())
            inner.join(timeout=min(1.0, remaining))
            now = time.time()
            _update_planner_job(run_uid, status="planning", updated_at=now)
            if inner.is_alive() and now >= next_heartbeat_ts:
                heartbeat_iso = datetime.now().isoformat()
                write_planner_status(
                    run_files,
                    {
                        "run_id": run_uid,
                        "status": "planning",
                        "started_at": started_at,
                        "updated_at": heartbeat_iso,
                        "error": "",
                        "timeout_seconds": int(timeout_seconds),
                        "result_ready": False,
                    },
                )
                append_event(
                    Path(run_files["events"]),
                    run_id=run_uid,
                    step_id=None,
                    agent="PlannerAgent",
                    event_type="PLANNER_HEARTBEAT",
                    severity="info",
                    payload={"status": "planning"},
                )
                next_heartbeat_ts = now + PLANNER_HEARTBEAT_SECONDS
            if _cancel.is_set():
                timed_out = True
                break
            if inner.is_alive() and now >= (started_ts + float(timeout_seconds)):
                _cancel.set()
                timed_out = True
                break

        finished_at = datetime.now().isoformat()
        if timed_out:
            error = (
                f"Planning timed out after {int(timeout_seconds)}s. "
                "Retry with a shorter request or raise BIO_HARNESS_UI_PLAN_TIMEOUT_SECONDS."
            )
            write_planner_status(
                run_files,
                {
                    "run_id": run_uid,
                    "status": "planning_timed_out",
                    "started_at": started_at,
                    "updated_at": finished_at,
                    "finished_at": finished_at,
                    "error": error,
                    "timeout_seconds": int(timeout_seconds),
                    "result_ready": False,
                },
            )
            append_event(
                Path(run_files["events"]),
                run_id=run_uid,
                step_id=None,
                agent="PlannerAgent",
                event_type="PLANNER_FAILED",
                severity="error",
                payload={"error": error, "reason": "timeout"},
            )
            _update_planner_job(
                run_uid,
                status="planning_timed_out",
                error=error,
                updated_at=time.time(),
            )
            return

        if result_queue.empty():
            error = "Planning failed without returning a result."
            write_planner_status(
                run_files,
                {
                    "run_id": run_uid,
                    "status": "planning_failed",
                    "started_at": started_at,
                    "updated_at": finished_at,
                    "finished_at": finished_at,
                    "error": error,
                    "timeout_seconds": int(timeout_seconds),
                    "result_ready": False,
                },
            )
            append_event(
                Path(run_files["events"]),
                run_id=run_uid,
                step_id=None,
                agent="PlannerAgent",
                event_type="PLANNER_FAILED",
                severity="error",
                payload={"error": error, "reason": "empty_result"},
            )
            _update_planner_job(run_uid, status="planning_failed", error=error, updated_at=time.time())
            return

        kind, payload = result_queue.get_nowait()
        if kind == "err":
            error = str(payload).strip() or payload.__class__.__name__
            write_planner_status(
                run_files,
                {
                    "run_id": run_uid,
                    "status": "planning_failed",
                    "started_at": started_at,
                    "updated_at": finished_at,
                    "finished_at": finished_at,
                    "error": error,
                    "timeout_seconds": int(timeout_seconds),
                    "result_ready": False,
                },
            )
            append_event(
                Path(run_files["events"]),
                run_id=run_uid,
                step_id=None,
                agent="PlannerAgent",
                event_type="PLANNER_FAILED",
                severity="error",
                payload={"error": error, "reason": "exception"},
            )
            _update_planner_job(run_uid, status="planning_failed", error=error, updated_at=time.time())
            return

        write_planner_result(run_files, payload)
        write_planner_status(
            run_files,
            {
                "run_id": run_uid,
                "status": "planned",
                "started_at": started_at,
                "updated_at": finished_at,
                "finished_at": finished_at,
                "error": "",
                "timeout_seconds": int(timeout_seconds),
                "result_ready": True,
            },
        )
        append_event(
            Path(run_files["events"]),
            run_id=run_uid,
            step_id=None,
            agent="PlannerAgent",
            event_type="PLANNER_FINISHED",
            severity="info",
            payload={"result_ready": True},
        )
        _update_planner_job(run_uid, status="planned", updated_at=time.time(), result_ready=True)

    thread = threading.Thread(target=_worker, daemon=True)
    now = time.time()
    with _PLANNER_LOCK:
        _PLANNER_JOBS[run_uid] = _PlannerJob(
            run_uid=run_uid,
            thread=thread,
            started_at=now,
            updated_at=now,
            timeout_seconds=int(timeout_seconds),
            cancel_event=_cancel,
            status="planning",
        )
    thread.start()
    return True


def planner_job_snapshot(run_uid: str) -> dict[str, Any]:
    """Return a serializable snapshot for one background planner job."""
    with _PLANNER_LOCK:
        job = _PLANNER_JOBS.get(str(run_uid).strip())
        if job is None:
            return {}
        return {
            "run_uid": job.run_uid,
            "status": job.status,
            "error": job.error,
            "started_at": job.started_at,
            "updated_at": job.updated_at,
            "timeout_seconds": job.timeout_seconds,
            "thread_alive": bool(job.thread.is_alive()),
            "result_ready": bool(job.result_ready),
        }


def cancel_planner_job(run_uid: str) -> bool:
    """Signal a running planner job to stop.

    Sets the cancellation event so that the inner planning thread can check
    ``cancel_event.is_set()`` and exit early.  Does **not** forcibly kill
    the thread — cooperative cancellation only.

    Returns:
        ``True`` if a live job was found and signalled, ``False`` otherwise.
    """
    with _PLANNER_LOCK:
        job = _PLANNER_JOBS.get(str(run_uid).strip())
        if job is None or not job.thread.is_alive():
            return False
        job.cancel_event.set()
        _logger.info("Cancellation signalled for planner job %s", run_uid)
        return True


def planning_is_orphaned(
    run_files: Mapping[str, Any],
    *,
    run_uid: str,
    orphan_after_seconds: int = 15,
) -> bool:
    """Return whether one persisted planning run no longer has a live planner."""
    snapshot = planner_job_snapshot(run_uid)
    if snapshot and snapshot.get("thread_alive", False):
        return False
    status_payload = load_planner_status(run_files)
    status = str(status_payload.get("status", "")).strip().lower()
    if status not in {"planning", ""}:
        return False
    updated_text = str(status_payload.get("updated_at", "")).strip() or str(status_payload.get("started_at", "")).strip()
    if not updated_text:
        state_path = Path(str(run_files.get("state", "")).strip())
        if state_path.exists():
            try:
                state_payload = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state_payload = {}
            updated_text = str(state_payload.get("planning_started_at", "")).strip() or str(
                state_payload.get("updated_at", "")
            ).strip()
    updated_ts = _parse_iso_epoch(updated_text)
    if updated_ts <= 0:
        return False
    return (time.time() - updated_ts) >= int(orphan_after_seconds)


def _update_planner_job(
    run_uid: str,
    *,
    status: str,
    updated_at: float,
    error: str = "",
    result_ready: bool | None = None,
) -> None:
    with _PLANNER_LOCK:
        job = _PLANNER_JOBS.get(run_uid)
        if job is None:
            return
        job.status = status
        job.updated_at = float(updated_at)
        job.error = error
        if result_ready is not None:
            job.result_ready = bool(result_ready)


def _parse_iso_epoch(text: str) -> float:
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def _json_safe_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat") and callable(getattr(value, "isoformat")):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return dict(value.__dict__)
        except Exception:
            pass
    return str(value)
