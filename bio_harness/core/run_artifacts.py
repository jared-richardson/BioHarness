from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import re
from datetime import datetime
from pathlib import Path
from secrets import token_hex
from typing import Any, Dict

from bio_harness.core.schemas import (
    ARTIFACT_SCHEMA_VERSION,
    RunEventSchema,
    RunExitSchema,
    RunManifestSchema,
    RunStateSchema,
)


def slugify_task(text: str, max_len: int = 28) -> str:
    """Convert task text to a filesystem-safe slug.

    Args:
        text: Raw task description text.
        max_len: Maximum slug length.

    Returns:
        Lowercased slug with non-alphanumeric characters replaced by underscores.
    """
    raw = (text or "task").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if not slug:
        slug = "task"
    return slug[:max_len]


def make_run_id(task_text: str) -> str:
    """Generate a unique run ID from timestamp, slugified task text, and random hex.

    Args:
        task_text: Raw task description to include in the ID.

    Returns:
        String like '20260314_120000_align_reads_a1b2'.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{slugify_task(task_text)}_{token_hex(2)}"


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Write a dict as pretty-printed JSON to a file."""
    payload = dict(data)
    payload.setdefault("schema_version", ARTIFACT_SCHEMA_VERSION)
    path.write_text(
        json.dumps(payload, indent=2, default=_json_safe_default),
        encoding="utf-8",
    )


def _json_safe_default(value: Any) -> Any:
    """Return a JSON representation for common run-artifact helper objects."""

    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def init_run_artifacts(
    workspace_root: Path,
    task_text: str,
    *,
    initial_exit_status: str = "running",
) -> Dict[str, Any]:
    """Create the directory structure and initial files for a new run.

    Args:
        workspace_root: Path to the workspace root directory.
        task_text: Description of the task for run ID generation.
        initial_exit_status: Initial status to write into ``exit.json``.

    Returns:
        Dict mapping artifact names to their Path objects, including 'run_id'.
    """
    runs_root = workspace_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    run_id = make_run_id(task_text)
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    files = {
        "run_dir": run_dir,
        "run_id": run_id,
        "state": run_dir / "state.json",
        "events": run_dir / "events.jsonl",
        "stdout": run_dir / "stdout.log",
        "stderr": run_dir / "stderr.log",
        "exec": run_dir / "execution.log",
        "exit": run_dir / "exit.json",
        "manifest": run_dir / "manifest.json",
        "assistance_manifest": run_dir / "assistance_manifest.json",
        "summary": run_dir / "summary.md",
        "path_decisions": run_dir / "path_decisions.json",
        "executor_runtime": run_dir / "executor.json",
        "preflight_summary": run_dir / "preflight_summary.json",
        "preflight_summary_md": run_dir / "preflight_summary.md",
        "completed_run_context": run_dir / "completed_run_context.json",
        "in_run_quality_events": run_dir / "in_run_quality_events.jsonl",
        "in_run_quality_summary": run_dir / "in_run_quality_summary.json",
        "literature_planning_support_json": run_dir / "literature_planning_support.json",
        "literature_planning_support_md": run_dir / "literature_planning_support.md",
        "planner": run_dir / "planner",
    }

    _write_json(
        files["state"],
        RunStateSchema(run_id=run_id, status="initialized").model_dump(mode="json"),
    )
    files["events"].write_text("", encoding="utf-8")
    files["stdout"].write_text("", encoding="utf-8")
    files["stderr"].write_text("", encoding="utf-8")
    files["exec"].write_text("", encoding="utf-8")
    files["in_run_quality_events"].write_text("", encoding="utf-8")
    _write_json(files["in_run_quality_summary"], {})
    files["planner"].mkdir(parents=True, exist_ok=True)
    _write_json(
        files["exit"],
        RunExitSchema(
            run_id=run_id,
            status=str(initial_exit_status),
        ).model_dump(mode="json"),
    )
    _write_json(
        files["manifest"],
        RunManifestSchema(
            run_id=run_id,
            created_at=datetime.now().isoformat(),
        ).model_dump(mode="json"),
    )
    _write_json(files["assistance_manifest"], {"run_id": run_id, "created_at": datetime.now().isoformat()})
    files["summary"].write_text("# Run Summary\n\nRun in progress.\n", encoding="utf-8")
    _write_json(files["path_decisions"], {
        "user_requested_root": "",
        "resolved_root": "",
        "resolution_reason": "",
        "rejected_candidates": [],
    })

    return files


def append_event(
    events_path: Path,
    *,
    run_id: str,
    step_id: int | None,
    agent: str,
    event_type: str,
    severity: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Append a timestamped event to the JSONL events log.

    Args:
        events_path: Path to the events.jsonl file.
        run_id: Unique run identifier.
        step_id: Step index (or None for run-level events).
        agent: Name of the agent emitting the event.
        event_type: Event category string.
        severity: Severity level (e.g. 'info', 'error').
        payload: Event-specific data dict.

    Returns:
        The event dict that was written.
    """
    event = {
        "ts": datetime.now().isoformat(),
        "run_id": run_id,
        "step_id": step_id,
        "agent": agent,
        "event_type": event_type,
        "severity": severity,
        "payload": payload,
    }
    event = RunEventSchema.model_validate(event).model_dump(mode="json")
    with events_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=True) + "\n")
    return event


def append_line(path: Path, line: str) -> None:
    """Append a single line of text to a log file."""
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def write_state(path: Path, state: Dict[str, Any]) -> None:
    """Persist the current run state as JSON."""
    payload = RunStateSchema.model_validate(state).model_dump(mode="json")
    _write_json(path, payload)


def write_exit(path: Path, data: Dict[str, Any]) -> None:
    """Write the final exit status JSON for a run."""
    payload = RunExitSchema.model_validate(data).model_dump(mode="json")
    _write_json(path, payload)


def write_manifest(path: Path, data: Dict[str, Any]) -> None:
    """Write the run manifest JSON."""
    payload = RunManifestSchema.model_validate(data).model_dump(mode="json")
    _write_json(path, payload)


def write_path_decisions(
    path: Path,
    *,
    user_requested_root: str,
    resolved_root: str,
    resolution_reason: str,
    rejected_candidates: list[Dict[str, Any]],
) -> None:
    """Record path resolution decisions for debugging and audit.

    Args:
        path: Path to the path_decisions.json file.
        user_requested_root: The root path the user originally requested.
        resolved_root: The path that was actually resolved.
        resolution_reason: Why this resolution was chosen.
        rejected_candidates: List of candidates that were tried and rejected.
    """
    _write_json(
        path,
        {
            "user_requested_root": user_requested_root,
            "resolved_root": resolved_root,
            "resolution_reason": resolution_reason,
            "rejected_candidates": rejected_candidates,
        },
    )
