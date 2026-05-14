"""Trace, backend lifecycle, and reachability helpers for ``BioLLM``."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bio_harness.core.llm_backends import (
    build_chat_backend,
    is_loopback_host,
    is_loopback_permission_error,
)

logger = logging.getLogger(__name__)


class LLMTraceMixin:
    """Provides backend lifecycle, reachability, and planner trace helpers."""

    def _is_loopback_blocked_error(self, exc: Exception) -> bool:
        return is_loopback_permission_error(self.host, exc)

    def _is_supervisor_timeout_error(self, exc: Exception) -> bool:
        text = str(exc or "")
        return "supervisor wall-clock limit" in text.lower()

    def _new_backend(self) -> Any:
        backend_factory = getattr(self, "_build_chat_backend", build_chat_backend)
        return backend_factory(
            backend_name=self.backend_name,
            host=str(self.host or "").strip(),
            timeout_seconds=float(self.request_timeout_seconds),
            api_key=self.api_key,
        )

    def configure_planner_trace(
        self,
        planner_trace_dir: str | Path | None,
        planner_trace_context: dict[str, Any] | None = None,
    ) -> None:
        self._planner_trace_dir = None
        if planner_trace_dir:
            try:
                trace_dir = Path(planner_trace_dir).expanduser().resolve()
                trace_dir.mkdir(parents=True, exist_ok=True)
                self._planner_trace_dir = trace_dir
            except Exception:
                self._planner_trace_dir = None
        self._planner_trace_context = dict(planner_trace_context or {})

    def _planner_trace(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        raw_content: str = "",
    ) -> None:
        if self._planner_trace_dir is None:
            return
        with self._planner_trace_lock:
            self._planner_trace_counter += 1
            trace_idx = self._planner_trace_counter
        stamp = int(time.time() * 1000)
        base = f"{stamp}_{os.getpid()}_{trace_idx:04d}_{event_type.lower()}"
        event_path = self._planner_trace_dir / f"{base}.json"
        text_path = self._planner_trace_dir / f"{base}.txt"
        event_payload = {
            "event_type": event_type,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pid": os.getpid(),
            "model_name": self.model_name,
            "host": self.host or "",
            "trace_context": dict(self._planner_trace_context),
            "payload": payload,
        }
        if raw_content:
            event_payload["raw_content_file"] = str(text_path)
            excerpt = str(raw_content[: self.trace_excerpt_chars])
            event_payload["raw_excerpt"] = excerpt
            event_payload["raw_content_len"] = len(raw_content)
            try:
                text_path.write_text(raw_content, encoding="utf-8")
            except Exception:
                event_payload["raw_content_file_error"] = "write_failed"
        try:
            event_path.write_text(json.dumps(event_payload, indent=2, ensure_ascii=True), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write planner trace event.")

    def backend_reachable(self, timeout_seconds: float = 0.5) -> bool:
        """Return whether the configured backend host accepts a TCP connection."""

        host_text = str(self.host or "").strip()
        if not host_text:
            return False
        parsed = urlparse(host_text if "://" in host_text else f"http://{host_text}")
        hostname = str(parsed.hostname or "").strip()
        if not hostname:
            return False
        port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
        try:
            with socket.create_connection((hostname, port), timeout=float(timeout_seconds)):
                return True
        except OSError:
            return False

    def _diagnostics_base(self) -> dict[str, Any]:
        """Return the standard backend diagnostic payload."""

        diag = self._backend.diagnostics()
        diag["model_name"] = self.model_name
        diag["heavy_model_name"] = self.heavy_model_name
        diag["dual_model_active"] = self.heavy_model_name != self.model_name
        diag["backend_name"] = self.backend_name
        diag["backend_label"] = self.backend_label
        diag["transport_name"] = self.transport_name
        diag["loopback_host"] = is_loopback_host(self.host)
        return diag
