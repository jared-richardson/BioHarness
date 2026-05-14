"""Public ``BioLLM`` facade composed from focused planner mixins."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

from bio_harness.core.llm_backends import (
    backend_default_host,
    backend_host_env_var,
    backend_label,
    backend_transport_name,
    build_chat_backend,
    normalize_backend_name,
)
from bio_harness.core.llm_entrypoints_mixin import LLMEntrypointsMixin
from bio_harness.core.llm_prompt_mixin import LLMPromptMixin
from bio_harness.core.llm_structured_response_mixin import LLMStructuredResponseMixin
from bio_harness.core.llm_trace_mixin import LLMTraceMixin
from bio_harness.core.llm_transport_mixin import LLMTransportMixin
from bio_harness.core.llm_types import (
    AbstractPlanSchema,
    AbstractToolStep,
    BioHarnessError,
    LLMOutputSchema,
    ToolStep,
    _DEFAULT_FALLBACK_MODEL,
)
from bio_harness.core.llm_workflow_mixin import LLMWorkflowMixin

logger = logging.getLogger(__name__)


def _bounded_int_env(name: str, default: int, *, min_value: int, max_value: int) -> int:
    """Return a bounded integer environment variable value."""

    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    return max(int(min_value), min(int(max_value), value))


def _bounded_float_env(name: str, default: float, *, min_value: float, max_value: float) -> float:
    """Return a bounded float environment variable value."""

    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    return max(float(min_value), min(float(max_value), value))


def _normalized_mode_env(name: str, default: str, allowed: set[str]) -> str:
    """Return a normalized enum-like mode from the environment."""

    value = str(os.getenv(name, default) or default).strip().lower()
    if value not in allowed:
        return default
    return value


def _resolve_backend_host(backend_name: str, explicit_host: str | None) -> str:
    """Resolve the backend host from explicit and environment sources."""

    if explicit_host:
        return explicit_host
    host_candidates = [backend_host_env_var(backend_name)]
    if backend_name in {"mlx", "vllm", "ollama_openai"}:
        host_candidates.append("BIO_HARNESS_OPENAI_BASE_URL")
    if backend_name == "ollama_openai":
        host_candidates.append("BIO_HARNESS_OLLAMA_HOST")
    for env_name in host_candidates:
        candidate = str(os.getenv(env_name, "") or "").strip()
        if candidate:
            return candidate
    return backend_default_host(backend_name)


class BioLLM(
    LLMEntrypointsMixin,
    LLMWorkflowMixin,
    LLMStructuredResponseMixin,
    LLMPromptMixin,
    LLMTransportMixin,
    LLMTraceMixin,
):
    """Client for local LLM backends used by the harness."""

    def __init__(
        self,
        model_name: str | None = None,
        host: str | None = None,
        llm_backend: str | None = None,
        planner_trace_dir: str | Path | None = None,
        planner_trace_context: dict[str, Any] | None = None,
    ) -> None:
        backend_raw = (
            llm_backend
            or os.getenv("BIO_HARNESS_LLM_BACKEND", os.getenv("BIO_HARNESS_LLM_PROVIDER", "ollama"))
            or "ollama"
        )
        self.backend_name = normalize_backend_name(backend_raw)
        self.transport_name = backend_transport_name(self.backend_name)
        self.backend_label = backend_label(self.backend_name)
        self.host = _resolve_backend_host(self.backend_name, host)
        self._build_chat_backend = build_chat_backend

        explicit_model = model_name or str(os.getenv("BIO_HARNESS_MODEL", "") or "").strip()
        explicit_heavy = str(os.getenv("BIO_HARNESS_MODEL_HEAVY", "") or "").strip()
        if explicit_model:
            self.model_name = explicit_model
            self.heavy_model_name = explicit_heavy or self.model_name
        else:
            self.model_name = _DEFAULT_FALLBACK_MODEL
            self.heavy_model_name = self.model_name
            try:
                from bio_harness.core.model_router import discover_models, select_default_models

                tmp_backend = self._build_chat_backend(
                    backend_name=self.backend_name,
                    host=self.host,
                    timeout_seconds=10.0,
                    api_key=str(os.getenv("BIO_HARNESS_OPENAI_API_KEY", "") or "").strip(),
                )
                models = discover_models(tmp_backend)
                if models:
                    default_planner, default_executor = select_default_models(models)
                    if default_executor:
                        self.model_name = default_executor
                    if default_planner:
                        self.heavy_model_name = default_planner
                    logger.info(
                        "BioLLM: auto-selected models -> executor='%s', planner='%s'",
                        self.model_name,
                        self.heavy_model_name,
                    )
            except Exception as exc:
                logger.warning("BioLLM: model auto-selection failed, using default: %s", exc)
            if explicit_heavy:
                self.heavy_model_name = explicit_heavy

        self.api_key = str(os.getenv("BIO_HARNESS_OPENAI_API_KEY", "") or "").strip()
        timeout_raw = os.getenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", "900")
        try:
            timeout_seconds = float(timeout_raw)
        except Exception:
            timeout_seconds = 900.0
        if timeout_seconds <= 0:
            timeout_seconds = 900.0
        self.request_timeout_seconds = float(timeout_seconds)
        self.planner_prompt_style = _normalized_mode_env(
            "BIO_HARNESS_PLANNER_PROMPT_STYLE",
            "compact",
            {"compact", "full"},
        )
        self.max_desc_chars = _bounded_int_env(
            "BIO_HARNESS_PLANNER_SKILL_DESC_CHARS",
            120,
            min_value=80,
            max_value=600,
        )
        self.default_num_ctx = _bounded_int_env(
            "BIO_HARNESS_LLM_NUM_CTX",
            8192,
            min_value=2048,
            max_value=32768,
        )
        self.default_num_predict = _bounded_int_env(
            "BIO_HARNESS_LLM_NUM_PREDICT",
            2200,
            min_value=256,
            max_value=4096,
        )
        self.connect_retries = _bounded_int_env(
            "BIO_HARNESS_LLM_CONNECT_RETRIES",
            3,
            min_value=1,
            max_value=6,
        )
        self.connect_retry_delay_seconds = _bounded_float_env(
            "BIO_HARNESS_LLM_CONNECT_RETRY_DELAY_SECONDS",
            1.0,
            min_value=0.1,
            max_value=10.0,
        )
        bridge_raw = str(os.getenv("BIO_HARNESS_LLM_SUBPROCESS_BRIDGE", "1") or "1").strip().lower()
        self.enable_subprocess_bridge = bridge_raw not in {"0", "false", "no", "off"}
        self.two_stage_mode = _normalized_mode_env(
            "BIO_HARNESS_PLANNER_TWO_STAGE_MODE",
            "auto",
            {"auto", "off", "always"},
        )
        self.hierarchical_mode = _normalized_mode_env(
            "BIO_HARNESS_PLANNER_HIERARCHICAL_MODE",
            "auto",
            {"auto", "off", "always"},
        )
        self.hierarchical_max_workers = _bounded_int_env(
            "BIO_HARNESS_PLANNER_HIERARCHICAL_MAX_WORKERS",
            3,
            min_value=1,
            max_value=8,
        )
        self.trace_excerpt_chars = _bounded_int_env(
            "BIO_HARNESS_PLANNER_TRACE_EXCERPT_CHARS",
            800,
            min_value=120,
            max_value=4000,
        )
        self._backend = self._new_backend()
        self._planner_trace_counter = 0
        self._planner_trace_dir: Path | None = None
        self._planner_trace_context: dict[str, Any] = {}
        self._planner_trace_lock = threading.Lock()
        self.configure_planner_trace(planner_trace_dir, planner_trace_context)


__all__ = [
    "AbstractPlanSchema",
    "AbstractToolStep",
    "BioHarnessError",
    "BioLLM",
    "LLMOutputSchema",
    "ToolStep",
    "_DEFAULT_FALLBACK_MODEL",
]
