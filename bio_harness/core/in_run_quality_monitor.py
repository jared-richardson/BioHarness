"""Lightweight in-run quality monitoring helpers.

This module provides summarize-only artifact checks that can run during
executor heartbeats without changing execution control flow. It is intended to
surface suspicious output emergence, not to gate or repair runs directly.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_ZERO_BYTE_EXCLUDE_SUFFIXES = {".log", ".txt", ".json", ".jsonl", ".md"}
_OUTPUT_ARG_PREFIX = "output_"


@dataclass(frozen=True)
class InRunQualityEvent:
    """One summarize-only in-run quality event.

    Attributes:
        key: Stable deduplication key for the event.
        category: Stable machine-readable event category.
        severity: Event severity. This is reporting-only in the current phase.
        path: Run-relative artifact path when applicable.
        active_step_id: Active step identifier when known.
        tool_name: Active tool name when known.
        message: Human-readable event explanation.
        size_bytes: Observed artifact size when applicable.
    """

    key: str
    category: str
    severity: str
    path: str
    active_step_id: int | None
    tool_name: str
    message: str
    size_bytes: int


@dataclass(frozen=True)
class InRunQualitySummary:
    """Summarize-only heartbeat quality summary for a running plan.

    Attributes:
        active_step_id: Active step identifier when known.
        tool_name: Active tool name when known.
        recent_output_count: Number of recent outputs observed in the current
            artifact tier.
        new_output_count: Number of newly observed outputs relative to previous
            heartbeat state.
        expected_output_count: Number of explicit expected outputs for the
            active step when known.
        expected_outputs_present: Expected outputs already present on disk.
        expected_outputs_missing: Expected outputs not yet present on disk.
        zero_byte_outputs: Recent zero-byte outputs considered suspicious.
        suspicious_event_count: Number of newly emitted suspicious events.
        latest_output_mtime: Latest observed artifact mtime in epoch seconds.
        scanned_files: Number of scanned files reported by the artifact tier.
    """

    active_step_id: int | None
    tool_name: str
    recent_output_count: int
    new_output_count: int
    expected_output_count: int
    expected_outputs_present: tuple[str, ...]
    expected_outputs_missing: tuple[str, ...]
    zero_byte_outputs: tuple[str, ...]
    suspicious_event_count: int
    latest_output_mtime: float
    scanned_files: int


def update_in_run_quality_state(
    run: dict[str, Any],
    *,
    selected_dir: Path,
    artifact_tier: Mapping[str, Any],
    active_step_id: int | None,
    active_tool_name: str = "",
    run_files: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Update in-run quality state and persist summarize-only artifacts.

    Args:
        run: Mutable run state dictionary.
        selected_dir: Selected output directory for the run.
        artifact_tier: Recent artifact observation payload from the heartbeat.
        active_step_id: Currently active step identifier when known.
        active_tool_name: Currently active tool name when known.
        run_files: Optional run-files mapping used for durable writes.

    Returns:
        A tuple of the JSON-friendly summary payload and any newly emitted
        event payloads.
    """

    previous_seen = _coerce_seen_map(run.get("in_run_quality_seen_files", {}))
    previous_keys = _coerce_emitted_keys(run.get("in_run_quality_emitted_event_keys", []))
    summary, events, current_seen = assess_in_run_quality(
        selected_dir=selected_dir,
        artifact_tier=artifact_tier,
        plan=run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {},
        active_step_id=active_step_id,
        active_tool_name=active_tool_name,
        previous_seen_files=previous_seen,
        emitted_event_keys=previous_keys,
    )
    summary_payload = in_run_quality_summary_to_json(summary)
    event_payloads = tuple(in_run_quality_event_to_json(event) for event in events)
    run["in_run_quality_summary"] = summary_payload
    run["in_run_quality_seen_files"] = current_seen
    run["in_run_quality_emitted_event_keys"] = sorted({*previous_keys, *(event["key"] for event in event_payloads)})
    recent = list(run.get("in_run_quality_recent_events", []) or [])
    recent.extend(event_payloads)
    run["in_run_quality_recent_events"] = recent[-12:]
    if isinstance(run_files, Mapping):
        _persist_in_run_quality_artifacts(run_files, summary_payload, event_payloads)
    return summary_payload, event_payloads


def assess_in_run_quality(
    *,
    selected_dir: Path,
    artifact_tier: Mapping[str, Any],
    plan: dict[str, Any],
    active_step_id: int | None,
    active_tool_name: str,
    previous_seen_files: Mapping[str, int] | None = None,
    emitted_event_keys: set[str] | None = None,
) -> tuple[InRunQualitySummary, tuple[InRunQualityEvent, ...], dict[str, int]]:
    """Assess one heartbeat artifact tier for summarize-only quality signals.

    Args:
        selected_dir: Selected output directory for the run.
        artifact_tier: Recent artifact observation payload.
        plan: Structured execution plan for the run.
        active_step_id: Currently active step identifier when known.
        active_tool_name: Currently active tool name when known.
        previous_seen_files: Previously observed file-size map used to detect
            new outputs.
        emitted_event_keys: Previously emitted event keys used to deduplicate
            repeated warnings.

    Returns:
        The heartbeat summary, any newly emitted events, and the updated
        observed-file map.
    """

    selected_root = Path(selected_dir).expanduser().resolve(strict=False)
    recent_files = artifact_tier.get("recent_files", [])
    previous_seen = dict(previous_seen_files or {})
    emitted_keys = set(emitted_event_keys or set())
    observed = _coerce_recent_files(recent_files)
    current_seen = {**previous_seen, **{row["path"]: int(row["size_bytes"]) for row in observed}}
    new_outputs = [
        row
        for row in observed
        if row["path"] not in previous_seen
    ]

    zero_byte_paths: list[str] = []
    events: list[InRunQualityEvent] = []
    for row in observed:
        rel_path = str(row["path"])
        size_bytes = int(row["size_bytes"])
        if size_bytes != 0 or _should_ignore_zero_byte(rel_path):
            continue
        zero_byte_paths.append(rel_path)
        event_key = f"zero_byte_output:{rel_path}"
        if event_key in emitted_keys:
            continue
        events.append(
            InRunQualityEvent(
                key=event_key,
                category="zero_byte_output",
                severity="warning",
                path=rel_path,
                active_step_id=active_step_id,
                tool_name=str(active_tool_name or ""),
                message="Recent output file is zero bytes and may be incomplete or still being written.",
                size_bytes=size_bytes,
            )
        )
        emitted_keys.add(event_key)

    expected_outputs = _expected_outputs_for_active_step(
        selected_root,
        plan=plan,
        active_step_id=active_step_id,
    )
    expected_present = tuple(path for path in expected_outputs if (selected_root / path).exists())
    expected_missing = tuple(path for path in expected_outputs if not (selected_root / path).exists())
    summary = InRunQualitySummary(
        active_step_id=active_step_id,
        tool_name=str(active_tool_name or ""),
        recent_output_count=len(observed),
        new_output_count=len(new_outputs),
        expected_output_count=len(expected_outputs),
        expected_outputs_present=expected_present,
        expected_outputs_missing=expected_missing,
        zero_byte_outputs=tuple(sorted(set(zero_byte_paths))),
        suspicious_event_count=len(events),
        latest_output_mtime=float(artifact_tier.get("latest_mtime", 0.0) or 0.0),
        scanned_files=int(artifact_tier.get("scanned_files", 0) or 0),
    )
    return summary, tuple(events), current_seen


def in_run_quality_summary_to_json(summary: InRunQualitySummary) -> dict[str, Any]:
    """Serialize an in-run quality summary into JSON-friendly primitives."""

    return asdict(summary)


def in_run_quality_summary_to_markdown(summary: InRunQualitySummary | Mapping[str, Any]) -> str:
    """Render a scientist-facing Markdown summary for one in-run quality snapshot.

    Args:
        summary: In-memory summary dataclass or JSON-like summary payload.

    Returns:
        Markdown text describing the latest persisted heartbeat quality state.
    """

    payload = (
        in_run_quality_summary_to_json(summary)
        if isinstance(summary, InRunQualitySummary)
        else _coerce_summary_payload(summary)
    )
    if not payload:
        return "# In-Run Quality Summary\n\n- No in-run quality summary was available."
    zero_byte_outputs = list(payload.get("zero_byte_outputs", []) or [])
    expected_present = list(payload.get("expected_outputs_present", []) or [])
    expected_missing = list(payload.get("expected_outputs_missing", []) or [])
    lines = [
        "# In-Run Quality Summary",
        "",
        "This is a reporting-only snapshot from the latest persisted execution heartbeat.",
        "",
        f"- Active step: `{payload.get('active_step_id')}`",
        f"- Tool: `{payload.get('tool_name', '')}`",
        f"- Recent outputs observed: `{payload.get('recent_output_count', 0)}`",
        f"- New outputs observed: `{payload.get('new_output_count', 0)}`",
        f"- Explicit expected outputs: `{payload.get('expected_output_count', 0)}`",
        f"- New suspicious events: `{payload.get('suspicious_event_count', 0)}`",
        f"- Files scanned: `{payload.get('scanned_files', 0)}`",
    ]
    lines.append(
        "- Suspicious zero-byte outputs: "
        + (", ".join(f"`{path}`" for path in zero_byte_outputs) if zero_byte_outputs else "none")
    )
    lines.append(
        "- Expected outputs present: "
        + (", ".join(f"`{path}`" for path in expected_present) if expected_present else "none")
    )
    lines.append(
        "- Expected outputs missing: "
        + (", ".join(f"`{path}`" for path in expected_missing) if expected_missing else "none")
    )
    return "\n".join(lines)


def in_run_quality_event_to_json(event: InRunQualityEvent) -> dict[str, Any]:
    """Serialize an in-run quality event into JSON-friendly primitives."""

    return asdict(event)


def _coerce_recent_files(raw_recent_files: Any) -> tuple[dict[str, Any], ...]:
    """Normalize recent artifact rows from heartbeat observations."""

    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_recent_files, Sequence):
        return ()
    for item in raw_recent_files:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("path", "") or "").strip()
        if not path:
            continue
        normalized.append(
            {
                "path": path,
                "size_bytes": int(item.get("size_bytes", 0) or 0),
                "mtime_epoch": float(item.get("mtime_epoch", 0.0) or 0.0),
            }
        )
    return tuple(normalized)


def _should_ignore_zero_byte(relative_path: str) -> bool:
    """Return whether a zero-byte file should be ignored for monitoring."""

    suffix = Path(relative_path).suffix.lower()
    return suffix in _ZERO_BYTE_EXCLUDE_SUFFIXES


def _expected_outputs_for_active_step(
    selected_dir: Path,
    *,
    plan: dict[str, Any],
    active_step_id: int | None,
) -> tuple[str, ...]:
    """Return explicit expected output paths for the active step when known."""

    if active_step_id is None:
        return ()
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return ()
    for step in steps:
        if not isinstance(step, dict):
            continue
        if int(step.get("step_id", 0) or 0) != int(active_step_id):
            continue
        return _extract_expected_outputs(selected_dir, step)
    return ()


def _extract_expected_outputs(selected_dir: Path, step: Mapping[str, Any]) -> tuple[str, ...]:
    """Extract explicit expected output file paths for one step."""

    collected: list[str] = []
    for key in ("expected_files", "deliverables"):
        value = step.get(key, [])
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                normalized = _normalize_expected_output_path(selected_dir, item)
                if normalized:
                    collected.append(normalized)

    arguments = step.get("arguments", {}) if isinstance(step.get("arguments", {}), Mapping) else {}
    for arg_name, arg_value in arguments.items():
        if not str(arg_name).startswith(_OUTPUT_ARG_PREFIX):
            continue
        normalized = _normalize_expected_output_path(selected_dir, arg_value)
        if normalized:
            collected.append(normalized)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in collected:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return tuple(ordered)


def _normalize_expected_output_path(selected_dir: Path, value: Any) -> str:
    """Normalize one expected output path to a selected-dir-relative file path."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            return str(candidate.resolve(strict=False).relative_to(selected_dir.resolve(strict=False)))
        except Exception:
            return ""
    if candidate.name != "." and candidate.suffix:
        return str(candidate)
    return ""


def _coerce_seen_map(payload: Any) -> dict[str, int]:
    """Normalize persisted seen-file maps into a stable structure."""

    if not isinstance(payload, Mapping):
        return {}
    normalized: dict[str, int] = {}
    for key, value in payload.items():
        path = str(key or "").strip()
        if not path:
            continue
        normalized[path] = int(value or 0)
    return normalized


def _coerce_emitted_keys(payload: Any) -> set[str]:
    """Normalize persisted emitted-event keys into a set."""

    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        return set()
    return {str(item).strip() for item in payload if str(item).strip()}


def _coerce_summary_payload(payload: Any) -> dict[str, Any]:
    """Normalize a stored in-run quality summary payload."""

    if not isinstance(payload, Mapping):
        return {}
    normalized = dict(payload)
    for key in (
        "zero_byte_outputs",
        "expected_outputs_present",
        "expected_outputs_missing",
    ):
        value = normalized.get(key, [])
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            normalized[key] = [str(item).strip() for item in value if str(item).strip()]
        else:
            normalized[key] = []
    return normalized


def _persist_in_run_quality_artifacts(
    run_files: Mapping[str, Any],
    summary_payload: dict[str, Any],
    event_payloads: Sequence[dict[str, Any]],
) -> None:
    """Persist in-run quality summary and event artifacts when paths exist."""

    summary_raw = str(run_files.get("in_run_quality_summary", "") or "").strip()
    events_raw = str(run_files.get("in_run_quality_events", "") or "").strip()
    if summary_raw:
        summary_path = Path(summary_raw).expanduser()
        summary_path.write_text(json.dumps(summary_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if events_raw and event_payloads:
        events_path = Path(events_raw).expanduser()
        with events_path.open("a", encoding="utf-8") as handle:
            for payload in event_payloads:
                handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
