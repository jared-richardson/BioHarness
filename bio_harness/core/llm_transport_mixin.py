"""Backend transport helpers for ``BioLLM``."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
from typing import Any

import httpx
import ollama

logger = logging.getLogger(__name__)


class LLMTransportMixin:
    """Provides backend availability checks and raw chat transport helpers."""

    @staticmethod
    def _is_connectivity_error(exc: Exception) -> bool:
        msg = str(exc or "").lower()
        if isinstance(exc, httpx.ConnectError):
            return True
        return (
            "failed to connect to ollama" in msg
            or "connection refused" in msg
            or "connecterror" in msg
            or "temporarily unavailable" in msg
            or "failed to connect" in msg
        )

    def is_available(self) -> tuple[bool, str]:
        """Check backend connectivity and model availability."""

        try:
            models = {str(model) for model in self._backend.list_models() if str(model).strip()}
            model_found = any(
                model == self.model_name or str(model).startswith(f"{self.model_name}:")
                for model in models
                if model
            )
            if not model_found:
                if self.backend_name == "ollama":
                    return False, (
                        f"Ollama reachable, but model '{self.model_name}' is not pulled. "
                        f"Run: ollama pull {self.model_name}"
                    )
                return False, (
                    f"{self.backend_label} reachable, but model '{self.model_name}' is not listed at "
                    f"{self.host.rstrip('/')}/models"
                )
            if self.heavy_model_name != self.model_name:
                heavy_found = any(
                    model == self.heavy_model_name or str(model).startswith(f"{self.heavy_model_name}:")
                    for model in models
                    if model
                )
                if not heavy_found:
                    return False, (
                        f"Heavy model '{self.heavy_model_name}' not found. "
                        "Pull it or set BIO_HARNESS_MODEL_HEAVY to an available model."
                    )
                return True, (
                    f"Connected to {self.backend_label}. "
                    f"Fast model: {self.model_name}, Heavy model: {self.heavy_model_name}"
                )
            return True, f"Connected to {self.backend_label}. Model ready: {self.model_name}"
        except Exception as exc:  # pragma: no cover - connectivity dependent
            if self._is_loopback_blocked_error(exc):
                return False, (
                    f"Local loopback access to {self.backend_label} at {self.host} is blocked by the current runtime. "
                    "Run the harness with localhost network permission or outside the sandbox."
                )
            return False, f"Cannot connect to {self.backend_label}: {exc}"

    def prewarm(self, *, mode: str, timeout_seconds: float) -> tuple[bool, str]:
        """Forward backend prewarm requests."""

        return self._backend.prewarm(
            model_name=self.model_name,
            mode=mode,
            timeout_seconds=float(timeout_seconds),
        )

    def diagnostics(self) -> dict[str, Any]:
        """Return the full backend diagnostic payload."""

        return self._diagnostics_base()

    @staticmethod
    def _strip_code_fences(raw_content: str) -> str:
        text = str(raw_content or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                return "\n".join(lines[1:-1]).strip()
        return text

    def _extract_json_candidate(self, raw_content: str) -> str:
        text = self._strip_code_fences(raw_content)
        if not text:
            return ""
        candidates: list[str] = [text]
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])
        for candidate in candidates:
            snippet = candidate.strip()
            if snippet.startswith("{") and snippet.endswith("}"):
                return snippet
        return text.strip()

    def _chat_json_raw(
        self,
        messages: list[ollama.Message],
        num_ctx: int | None = None,
        num_predict: int | None = None,
        format_spec: Any = "json",
        model_override: str | None = None,
    ) -> dict[str, Any]:
        ctx = int(num_ctx or self.default_num_ctx)
        predict = int(num_predict or self.default_num_predict)
        effective_model = model_override or self.model_name
        response = None
        last_exc: Exception | None = None
        transport = self.transport_name
        for attempt in range(1, int(self.connect_retries) + 1):
            try:
                response = self._backend.chat(
                    model_name=effective_model,
                    messages=messages,
                    format_spec=format_spec,
                    temperature=0.0,
                    num_ctx=ctx,
                    num_predict=predict,
                )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if (not self._is_connectivity_error(exc)) or attempt >= int(self.connect_retries):
                    break
                self._backend = self._new_backend()
                delay = float(self.connect_retry_delay_seconds) * float(attempt)
                time.sleep(delay)
        if response is None:
            if (
                last_exc is not None
                and self.enable_subprocess_bridge
                and self.backend_name == "ollama"
                and self._is_connectivity_error(last_exc)
            ):
                bridged = self._chat_json_via_subprocess_bridge(
                    messages=messages,
                    num_ctx=ctx,
                    num_predict=predict,
                    format_spec=format_spec,
                    model_override=effective_model,
                )
                if isinstance(bridged, dict):
                    bridged["transport"] = "subprocess_bridge"
                    return bridged
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("LLM chat returned no response.")
        raw_content = str(response or "")
        return {
            "raw_content": raw_content,
            "transport": str(self.backend_name or transport),
            "num_ctx": ctx,
            "num_predict": predict,
        }

    def _chat_json(
        self,
        messages: list[ollama.Message],
        num_ctx: int | None = None,
        num_predict: int | None = None,
        format_spec: Any = "json",
        model_override: str | None = None,
    ) -> dict[str, Any]:
        raw = self._chat_json_raw(
            messages,
            num_ctx=num_ctx,
            num_predict=num_predict,
            format_spec=format_spec,
            model_override=model_override,
        )
        content = str(raw.get("raw_content", "") or "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        return json.loads(content)

    def _chat_json_via_subprocess_bridge(
        self,
        *,
        messages: list[ollama.Message],
        num_ctx: int,
        num_predict: int,
        format_spec: Any = "json",
        model_override: str | None = None,
    ) -> dict[str, Any] | None:
        payload = {
            "model": model_override or self.model_name,
            "host": self.host or "",
            "timeout_seconds": float(self.request_timeout_seconds),
            "messages": messages,
            "num_ctx": int(num_ctx),
            "num_predict": int(num_predict),
            "format": format_spec,
        }
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "bio_harness.core.ollama_bridge"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=max(10.0, float(self.request_timeout_seconds) + 15.0),
                check=False,
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(str(proc.stdout or "").strip() or "{}")
        except Exception:
            return None
        if not isinstance(data, dict) or not bool(data.get("ok", False)):
            return None
        content = str(data.get("content", "") or "").strip()
        if not content:
            return None
        return {
            "raw_content": content,
            "transport": "subprocess_bridge",
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        }
