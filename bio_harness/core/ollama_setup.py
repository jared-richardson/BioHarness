"""Safe Ollama setup operations for first-run onboarding.

The first-run UI needs to help users start a local Ollama server and pull a
recommended model without exposing a generic shell command surface. This module
keeps those operations deterministic and reusable by the CLI, API, and tests.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$")
_SUCCESS_STATUSES = {"success", "pull complete"}


class OllamaPullCancelled(RuntimeError):
    """Raised by progress callbacks to cancel an active Ollama pull."""


@dataclass(frozen=True)
class OllamaServerStatus:
    """Reachability state for a local Ollama server.

    Attributes:
        cli_available: Whether the ``ollama`` command is on PATH.
        host: Host URL that was checked.
        reachable: Whether the API responded successfully.
        error: Short diagnostic error when the server is not reachable.
    """

    cli_available: bool
    host: str
    reachable: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "cli_available": self.cli_available,
            "host": self.host,
            "reachable": self.reachable,
            "error": self.error,
        }


@dataclass(frozen=True)
class OllamaPullEvent:
    """One structured progress event from an Ollama model pull.

    Attributes:
        status: Ollama-reported status text.
        digest: Layer digest when present.
        total_bytes: Total bytes for the active layer when present.
        completed_bytes: Completed bytes for the active layer when present.
        percent: Percent complete for the active layer, when computable.
        done: Whether the event indicates a terminal success.
        error: Ollama-reported error text when present.
    """

    status: str
    digest: str = ""
    total_bytes: int = 0
    completed_bytes: int = 0
    percent: float | None = None
    done: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return {
            "status": self.status,
            "digest": self.digest,
            "total_bytes": self.total_bytes,
            "completed_bytes": self.completed_bytes,
            "percent": self.percent,
            "done": self.done,
            "error": self.error,
        }


def normalize_ollama_host(host: str | None = None) -> str:
    """Normalize an Ollama host URL.

    Args:
        host: Optional host URL.

    Returns:
        A host URL without a trailing slash.
    """
    resolved = str(host or DEFAULT_OLLAMA_HOST).strip() or DEFAULT_OLLAMA_HOST
    return resolved.rstrip("/")


def validate_ollama_model_name(model_name: str) -> str:
    """Validate a model name before sending it to Ollama.

    Args:
        model_name: User or catalog supplied model identifier.

    Returns:
        The stripped model name.

    Raises:
        ValueError: If the model name is empty or contains shell-like syntax.
    """
    resolved = str(model_name or "").strip()
    if not resolved or not _MODEL_NAME_PATTERN.fullmatch(resolved):
        raise ValueError(f"invalid Ollama model name: {model_name!r}")
    return resolved


def is_ollama_cli_available() -> bool:
    """Return whether the Ollama CLI is available on PATH."""
    return shutil.which("ollama") is not None


def check_ollama_server(
    *,
    host: str | None = None,
    timeout_seconds: float = 3.0,
) -> OllamaServerStatus:
    """Check whether the local Ollama server is reachable.

    Args:
        host: Optional Ollama host URL.
        timeout_seconds: HTTP timeout in seconds.

    Returns:
        Structured server status.
    """
    resolved_host = normalize_ollama_host(host)
    cli_available = is_ollama_cli_available()
    try:
        response = httpx.get(f"{resolved_host}/api/tags", timeout=timeout_seconds)
        if response.status_code == 200:
            return OllamaServerStatus(
                cli_available=cli_available,
                host=resolved_host,
                reachable=True,
            )
        return OllamaServerStatus(
            cli_available=cli_available,
            host=resolved_host,
            reachable=False,
            error=f"Ollama returned HTTP {response.status_code}",
        )
    except httpx.HTTPError as exc:
        return OllamaServerStatus(
            cli_available=cli_available,
            host=resolved_host,
            reachable=False,
            error=str(exc),
        )


def start_ollama_server(
    *,
    host: str | None = None,
    log_path: str | Path | None = None,
    wait_seconds: float = 12.0,
    poll_interval_seconds: float = 0.5,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    status_checker: Callable[..., OllamaServerStatus] = check_ollama_server,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Start ``ollama serve`` when the local server is not already reachable.

    Args:
        host: Optional Ollama host URL.
        log_path: Optional log path for server stdout/stderr.
        wait_seconds: Maximum time to wait for API reachability.
        poll_interval_seconds: Poll interval while waiting.
        popen_factory: Injectable process factory for tests.
        status_checker: Injectable reachability checker for tests.
        sleep_fn: Injectable sleep function for tests.

    Returns:
        JSON-serializable start result.
    """
    resolved_host = normalize_ollama_host(host)
    initial = status_checker(host=resolved_host)
    if initial.reachable:
        return {
            "attempted": False,
            "succeeded": True,
            "already_running": True,
            "host": resolved_host,
            "pid": None,
            "log_path": "",
            "status": initial.to_dict(),
            "error": "",
        }
    if not initial.cli_available:
        return {
            "attempted": False,
            "succeeded": False,
            "already_running": False,
            "host": resolved_host,
            "pid": None,
            "log_path": "",
            "status": initial.to_dict(),
            "error": "Ollama CLI is not available on PATH.",
        }

    target_log = Path(
        log_path or PROJECT_ROOT / "workspace" / "setup_reports" / "ollama_server.log"
    )
    target_log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = target_log.open("ab")
    try:
        proc = popen_factory(
            ["ollama", "serve"],
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        log_handle.close()
        return {
            "attempted": True,
            "succeeded": False,
            "already_running": False,
            "host": resolved_host,
            "pid": None,
            "log_path": str(target_log),
            "status": initial.to_dict(),
            "error": str(exc),
        }

    deadline = time.monotonic() + max(0.0, wait_seconds)
    latest = initial
    while time.monotonic() <= deadline:
        latest = status_checker(host=resolved_host)
        if latest.reachable:
            break
        sleep_fn(max(0.05, poll_interval_seconds))

    log_handle.close()
    return {
        "attempted": True,
        "succeeded": latest.reachable,
        "already_running": False,
        "host": resolved_host,
        "pid": int(getattr(proc, "pid", 0) or 0),
        "log_path": str(target_log),
        "status": latest.to_dict(),
        "error": "" if latest.reachable else latest.error or "Ollama did not become reachable.",
    }


def parse_ollama_pull_event(payload: dict[str, Any]) -> OllamaPullEvent:
    """Convert an Ollama pull JSON event into a stable progress record.

    Args:
        payload: One JSON object from the Ollama pull stream.

    Returns:
        Normalized progress event.
    """
    status = str(payload.get("status", "") or "").strip()
    digest = str(payload.get("digest", "") or "").strip()
    total = int(payload.get("total", 0) or 0)
    completed = int(payload.get("completed", 0) or 0)
    error = str(payload.get("error", "") or "").strip()
    percent = round((completed / total) * 100.0, 2) if total > 0 else None
    done = status.lower() in _SUCCESS_STATUSES and not error
    return OllamaPullEvent(
        status=status,
        digest=digest,
        total_bytes=total,
        completed_bytes=completed,
        percent=percent,
        done=done,
        error=error,
    )


def _stream_ollama_pull_api(
    *,
    model_name: str,
    host: str,
    timeout_seconds: float | None,
) -> Iterable[dict[str, Any]]:
    """Yield raw JSON objects from the Ollama pull API."""
    timeout = None if timeout_seconds is None else httpx.Timeout(timeout_seconds)
    with (
        httpx.Client(timeout=timeout) as client,
        client.stream(
            "POST",
            f"{host}/api/pull",
            json={"name": model_name, "stream": True},
        ) as response,
    ):
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line
            yield json.loads(text)


def _write_progress_event(progress_path: Path, event: dict[str, Any]) -> None:
    """Append one progress event to a JSONL progress file."""
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with progress_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def pull_ollama_model(
    *,
    model_name: str,
    host: str | None = None,
    progress_path: str | Path | None = None,
    timeout_seconds: float | None = None,
    stream_factory: Callable[..., Iterable[dict[str, Any]]] = _stream_ollama_pull_api,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Pull an Ollama model through the local API with structured progress.

    Args:
        model_name: Ollama model identifier to pull.
        host: Optional Ollama host URL.
        progress_path: Optional JSONL file path for progress events.
        timeout_seconds: Optional request timeout. ``None`` disables the HTTP
            read timeout for long downloads.
        stream_factory: Injectable raw event stream for tests.
        progress_callback: Optional callback invoked for each normalized event.

    Returns:
        JSON-serializable pull result.
    """
    resolved_model = validate_ollama_model_name(model_name)
    resolved_host = normalize_ollama_host(host)
    target_progress = Path(progress_path).expanduser().resolve() if progress_path else None
    events: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        for raw_event in stream_factory(
            model_name=resolved_model,
            host=resolved_host,
            timeout_seconds=timeout_seconds,
        ):
            event = parse_ollama_pull_event(raw_event).to_dict()
            event["created_at"] = datetime.now(timezone.utc).isoformat()
            events.append(event)
            if target_progress is not None:
                _write_progress_event(target_progress, event)
            if progress_callback is not None:
                progress_callback(event)
            if event.get("error"):
                break
    except OllamaPullCancelled as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "attempted": True,
            "succeeded": False,
            "canceled": True,
            "model_name": resolved_model,
            "host": resolved_host,
            "started_at": started_at,
            "finished_at": finished_at,
            "events": events[-20:],
            "event_count": len(events),
            "progress_path": str(target_progress) if target_progress else "",
            "error": str(exc) or "Ollama pull canceled.",
        }
    except (httpx.HTTPError, json.JSONDecodeError, OSError, ValueError) as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        return {
            "attempted": True,
            "succeeded": False,
            "canceled": False,
            "model_name": resolved_model,
            "host": resolved_host,
            "started_at": started_at,
            "finished_at": finished_at,
            "events": events[-20:],
            "event_count": len(events),
            "progress_path": str(target_progress) if target_progress else "",
            "error": str(exc),
        }

    succeeded = bool(events and events[-1].get("done") and not events[-1].get("error"))
    return {
        "attempted": True,
        "succeeded": succeeded,
        "canceled": False,
        "model_name": resolved_model,
        "host": resolved_host,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "events": events[-20:],
        "event_count": len(events),
        "progress_path": str(target_progress) if target_progress else "",
        "error": "" if succeeded else _pull_failure_message(events),
    }


def _pull_failure_message(events: list[dict[str, Any]]) -> str:
    """Return a compact failure message for a finished pull stream."""
    if not events:
        return "Ollama returned no pull progress events."
    latest_error = str(events[-1].get("error", "") or "").strip()
    if latest_error:
        return latest_error
    latest_status = str(events[-1].get("status", "") or "").strip()
    return f"Ollama pull ended before success: {latest_status or 'unknown status'}"
