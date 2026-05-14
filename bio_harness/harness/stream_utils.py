"""Stream and log parsing utilities for the harness execution loop."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from bio_harness.core.constants import PID_STATUS_RE
from bio_harness.harness.config import (
    STEP_COMMAND_RE,
    STEP_EXEC_START_RE,
)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _emit(msg: str, *, quiet: bool = False) -> None:
    if quiet:
        return
    print(f"[{_now()}] {msg}", flush=True)


def _all_steps_completed(step_statuses: list[Any]) -> bool:
    if not step_statuses:
        return False
    return all(str(status).strip().lower() == "completed" for status in step_statuses)


def _extract_pid_from_line(line: str) -> int | None:
    match = PID_STATUS_RE.search(str(line or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _extract_step_command_from_line(line: str) -> tuple[int | None, str]:
    m = STEP_COMMAND_RE.match(str(line or "").strip())
    if not m:
        return None, ""
    try:
        step_id = int(m.group(1))
    except Exception:
        step_id = None
    return step_id, str(m.group(2) or "").strip()


def _extract_step_context_from_line(line: str) -> tuple[int | None, str]:
    m = STEP_EXEC_START_RE.match(str(line or "").strip())
    if not m:
        return None, ""
    try:
        step_id = int(m.group(1))
    except Exception:
        step_id = None
    return step_id, str(m.group(2) or "").strip()


def _is_pid_live(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        status = str(proc.status()).lower()
        return status not in {"zombie", "dead"}
    except Exception:
        return False


def _append_recent_marker(run: dict[str, Any], marker: str, *, max_items: int = 24) -> None:
    token = str(marker or "").strip().upper()
    if not token:
        return
    recent = list(run.get("recent_stream_markers", []))
    recent.append(token)
    run["recent_stream_markers"] = recent[-max_items:]


def _stream_evidence(run: dict[str, Any], *, now_ts: float) -> dict[str, Any]:
    counters = dict(run.get("stream_counters", {}))
    last_ts = float(run.get("last_executor_event_ts", 0.0) or 0.0)
    return {
        "stdout_lines": int(counters.get("stdout_lines", 0)),
        "stderr_lines": int(counters.get("stderr_lines", 0)),
        "live_lines": int(counters.get("live_lines", 0)),
        "last_stream_seconds_ago": int(max(0.0, now_ts - last_ts)) if last_ts > 0 else -1,
        "recent_markers": list(run.get("recent_stream_markers", []))[-8:],
    }


def _parse_log_channel(line: str) -> tuple[str, str]:
    m = re.search(r"^\[Step\s+\d+\s+Output\]\s+(\[(stdout|stderr|status)\]\s+.*)$", line)
    body = m.group(1) if m else line
    if body.startswith("[stdout] "):
        return "stdout", body[len("[stdout] ") :]
    if body.startswith("[stderr] "):
        return "stderr", body[len("[stderr] ") :]
    return "live", body


def _extract_missing_tools_from_line(line: str) -> list[str]:
    found: list[str] = []
    m1 = re.search(r"Command not found\. Ensure '([^']+)' is in your PATH", line)
    if m1:
        found.append(m1.group(1).strip())
    m2 = re.search(r"\b([A-Za-z0-9._+-]+): command not found\b", line)
    if m2:
        found.append(m2.group(1).strip())
    for m in re.findall(r"__MISSING_TOOL__:([A-Za-z0-9._+-]+)", line):
        found.append(m.strip())
    return sorted({x for x in found if x})


def _extract_paths_from_text(text: str) -> list[str]:
    raw = re.findall(r"(/[^ \n\t,;\"')]+)", text or "")
    cleaned = []
    for token in raw:
        t = token.strip()
        if t:
            cleaned.append(t)
    return cleaned


def _normalize_contract_hint(token: str) -> str:
    t = (token or "").strip().lower().strip("`\"'()[]{}<>:;,")
    if not t:
        return ""
    if "/" in t or "\\" in t:
        t = Path(t).name.lower()
    if not t:
        return ""
    if re.search(r"[^a-z0-9_.-]", t):
        return ""
    if len(t) < 2:
        return ""
    stop = {
        "assuming",
        "and",
        "or",
        "the",
        "a",
        "an",
        "to",
        "for",
        "with",
        "without",
        "from",
        "then",
        "else",
        "if",
        "of",
        "on",
        "in",
        "at",
        "by",
        "gtf",
        "fasta",
        "fa",
        "mouse_gtf",
        "mouse_fasta",
        "mouse_fa",
    }
    if t in stop:
        return ""
    if t.endswith((".sh", ".py")):
        return t
    if t in {"bash", "sh", "python", "python3", "star", "rmats", "rmats.py", "samtools", "fastqc"}:
        return t
    return ""
