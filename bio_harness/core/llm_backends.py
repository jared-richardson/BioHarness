from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx
import ollama


def _message_to_dict(message: Any) -> dict[str, str]:
    if isinstance(message, dict):
        role = str(message.get("role", "user") or "user")
        content = str(message.get("content", "") or "")
        return {"role": role, "content": content}
    role = str(getattr(message, "role", "user") or "user")
    content = str(getattr(message, "content", "") or "")
    return {"role": role, "content": content}


def normalize_messages(messages: list[Any]) -> list[dict[str, str]]:
    """Convert heterogeneous message objects to ``{"role": ..., "content": ...}`` dicts.

    Strips blank messages and normalises Ollama/OpenAI message formats.
    """
    out: list[dict[str, str]] = []
    for message in messages or []:
        row = _message_to_dict(message)
        if not row.get("content", "").strip():
            continue
        out.append(row)
    return out


def _extract_openai_content(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "") or "").strip().lower()
            if item_type in {"text", "output_text"}:
                text = str(item.get("text", "") or "")
                if text:
                    parts.append(text)
        return "".join(parts)
    return str(message or "")


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        try:
            return value.model_dump()
        except Exception:
            return value
    return value


class ChatBackend(Protocol):
    backend_name: str
    transport_name: str
    host: str
    timeout_seconds: float

    def chat(
        self,
        *,
        model_name: str,
        messages: list[Any],
        temperature: float,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        format_spec: Any = None,
    ) -> str:
        ...

    def list_models(self) -> list[str]:
        ...

    def is_connectivity_error(self, exc: Exception) -> bool:
        ...

    def prewarm(self, *, model_name: str, mode: str, timeout_seconds: float) -> tuple[bool, str]:
        ...

    def diagnostics(self) -> dict[str, Any]:
        ...


def normalize_backend_name(name: str | None) -> str:
    """Canonicalise a backend name string to one of the supported values."""
    raw = str(name or "ollama").strip().lower()
    if raw == "openai_compatible":
        return "openai_compatible"
    if raw in {"openai", "openai-compatible", "openai_compat"}:
        return "openai_compatible"
    if raw in {"vllm", "vllm_openai"}:
        return "vllm"
    if raw in {"mlx", "mlx_lm", "mlx-openai", "mlx_openai"}:
        return "mlx"
    if raw in {"ollama_openai", "ollama-v1", "ollama_openai_compatible"}:
        return "ollama_openai"
    if raw != "ollama":
        return "ollama"
    return raw


def backend_transport_name(name: str | None) -> str:
    """Return ``'ollama'`` or ``'openai_compatible'`` for the given backend."""
    normalized = normalize_backend_name(name)
    if normalized == "ollama":
        return "ollama"
    return "openai_compatible"


def backend_label(name: str | None) -> str:
    """Human-readable label for the backend (e.g. ``'Ollama'``)."""
    normalized = normalize_backend_name(name)
    if normalized == "ollama":
        return "Ollama"
    if normalized == "ollama_openai":
        return "Ollama OpenAI-compatible API"
    if normalized == "mlx":
        return "MLX OpenAI-compatible server"
    if normalized == "vllm":
        return "vLLM OpenAI-compatible server"
    return "OpenAI-compatible backend"


def backend_host_env_var(name: str | None) -> str:
    """Return the environment variable name for the backend's host URL."""
    normalized = normalize_backend_name(name)
    if normalized == "ollama":
        return "BIO_HARNESS_OLLAMA_HOST"
    if normalized == "mlx":
        return "BIO_HARNESS_MLX_BASE_URL"
    if normalized == "vllm":
        return "BIO_HARNESS_VLLM_BASE_URL"
    if normalized == "ollama_openai":
        return "BIO_HARNESS_OLLAMA_OPENAI_BASE_URL"
    return "BIO_HARNESS_OPENAI_BASE_URL"


def backend_default_host(name: str | None) -> str:
    """Return the default host URL for the given backend."""
    normalized = normalize_backend_name(name)
    if normalized in {"ollama", "ollama_openai"}:
        return "http://127.0.0.1:11434"
    if normalized == "mlx":
        return "http://127.0.0.1:8080"
    if normalized == "vllm":
        return "http://127.0.0.1:8000"
    return "http://127.0.0.1:8000/v1"


def _openai_path_prefixes(host: str) -> list[str]:
    path = str(urlsplit(str(host or "")).path or "").rstrip("/")
    if path.endswith("/v1"):
        return [""]
    return ["/v1", ""]


def _openai_path(prefix: str, suffix: str) -> str:
    clean_prefix = str(prefix or "").rstrip("/")
    clean_suffix = str(suffix or "").strip()
    if not clean_suffix.startswith("/"):
        clean_suffix = f"/{clean_suffix}"
    return f"{clean_prefix}{clean_suffix}" if clean_prefix else clean_suffix


def _iter_exception_chain(exc: Exception | None) -> list[Exception]:
    chain: list[Exception] = []
    seen: set[int] = set()
    current: Exception | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        next_exc = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        current = next_exc if isinstance(next_exc, Exception) else None
    return chain


def is_loopback_host(host: str | None) -> bool:
    hostname = str(urlsplit(str(host or "")).hostname or "").strip().lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def is_permission_denied_network_error(exc: Exception | None) -> bool:
    for item in _iter_exception_chain(exc):
        text = str(item or "").lower()
        if "operation not permitted" in text or "permission denied" in text:
            return True
        errno_value = getattr(item, "errno", None)
        if errno_value in {1, 13}:
            return True
    return False


def is_loopback_permission_error(host: str | None, exc: Exception | None) -> bool:
    return is_loopback_host(host) and is_permission_denied_network_error(exc)


@dataclass
class OllamaChatBackend:
    host: str
    timeout_seconds: float
    provider_name: str = "ollama"

    backend_name: str = field(init=False)
    transport_name: str = field(init=False, default="ollama")
    _last_operation: str = field(init=False, default="")
    _last_error: str = field(init=False, default="")
    _last_model_count: int = field(init=False, default=-1)

    def __post_init__(self) -> None:
        self.backend_name = normalize_backend_name(self.provider_name)
        kwargs = {"timeout": float(self.timeout_seconds)}
        self._client = ollama.Client(host=self.host, **kwargs) if self.host else ollama.Client(**kwargs)

    def chat(
        self,
        *,
        model_name: str,
        messages: list[Any],
        temperature: float,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        format_spec: Any = None,
    ) -> str:
        try:
            # Disable thinking mode for models that support it (e.g. Qwen 3.5).
            # Thinking tokens consume token budget and wall-clock time without
            # contributing to the JSON output we need.
            think_enabled = str(os.getenv("BIO_HARNESS_LLM_THINK", "false") or "false").strip().lower()
            think_flag: bool | None = None if think_enabled in {"1", "true", "yes", "on"} else False
            response_obj = self._client.chat(
                model=model_name,
                messages=normalize_messages(messages),
                format=("json" if format_spec is None else format_spec),
                think=think_flag,
                options=ollama.Options(
                    temperature=float(temperature),
                    num_ctx=int(num_ctx or 8192),
                    num_predict=int(num_predict or 1024),
                ),
            )
        except Exception as exc:
            self._last_operation = "chat"
            self._last_error = str(exc)
            raise
        response = _model_dump(response_obj)
        self._last_operation = "chat"
        self._last_error = ""
        if isinstance(response, dict):
            return str(((response or {}).get("message") or {}).get("content") or "")
        message = getattr(response_obj, "message", None)
        return str(getattr(message, "content", "") or "")

    def list_models(self) -> list[str]:
        try:
            available_obj = self._client.list()
        except Exception as exc:
            self._last_operation = "list_models"
            self._last_error = str(exc)
            raise
        available = _model_dump(available_obj)
        models = []
        rows = available.get("models", []) if isinstance(available, dict) else getattr(available_obj, "models", [])
        for row in rows or []:
            if isinstance(row, dict):
                name = row.get("model") or row.get("name")
            else:
                name = getattr(row, "model", None) or getattr(row, "name", None)
            if name:
                models.append(str(name))
        self._last_operation = "list_models"
        self._last_model_count = len(models)
        self._last_error = ""
        return models

    def list_models_with_metadata(self) -> list[dict[str, Any]]:
        """Return models with rich metadata (parameter count, family, size).

        Uses the Ollama ``/api/tags`` response which includes
        ``details.parameter_size`` and ``details.family`` for each model.

        Returns:
            List of dicts with keys ``name``, ``parameter_count_b``,
            ``family``, ``size_gb``.
        """
        from bio_harness.core.model_router import _parse_parameter_size as _parse_param_size

        try:
            available_obj = self._client.list()
        except Exception as exc:
            self._last_operation = "list_models_with_metadata"
            self._last_error = str(exc)
            raise
        available = _model_dump(available_obj)
        rows = available.get("models", []) if isinstance(available, dict) else getattr(available_obj, "models", [])
        results: list[dict[str, Any]] = []
        for row in rows or []:
            if isinstance(row, dict):
                name = str(row.get("model") or row.get("name") or "").strip()
                if not name:
                    continue
                details = row.get("details", {}) if isinstance(row.get("details"), dict) else {}
                param_size = _parse_param_size(str(details.get("parameter_size", "") or ""))
                family = str(details.get("family", "") or "").strip()
                size_gb = float(row.get("size", 0) or 0) / 1e9
            else:
                name = str(getattr(row, "model", None) or getattr(row, "name", None) or "").strip()
                if not name:
                    continue
                details = getattr(row, "details", None)
                if details is not None:
                    details = _model_dump(details)
                    if isinstance(details, dict):
                        param_size = _parse_param_size(str(details.get("parameter_size", "") or ""))
                        family = str(details.get("family", "") or "").strip()
                    else:
                        param_size = 0.0
                        family = ""
                else:
                    param_size = 0.0
                    family = ""
                size_gb = float(getattr(row, "size", 0) or 0) / 1e9
            results.append({
                "name": name,
                "parameter_count_b": param_size,
                "family": family,
                "size_gb": size_gb,
            })
        self._last_operation = "list_models_with_metadata"
        self._last_model_count = len(results)
        self._last_error = ""
        return results

    def is_connectivity_error(self, exc: Exception) -> bool:
        msg = str(exc or "").lower()
        return isinstance(exc, httpx.ConnectError) or any(
            token in msg
            for token in (
                "failed to connect to ollama",
                "connection refused",
                "connecterror",
                "temporarily unavailable",
            )
        )

    def prewarm(self, *, model_name: str, mode: str, timeout_seconds: float) -> tuple[bool, str]:
        kwargs = {"timeout": float(timeout_seconds)}
        client = ollama.Client(host=self.host, **kwargs) if self.host else ollama.Client(**kwargs)
        if mode == "chat":
            client.chat(
                model=model_name,
                messages=[{"role": "user", "content": "Return JSON: {\"warmup\":true}"}],
                format="json",
                options={"temperature": 0.0, "num_predict": 32, "num_ctx": 1024},
            )
            return True, "chat_prewarm_ok"
        models = {
            str(name)
            for name in self.list_models()
            if str(name).strip()
        }
        ok = any(name == model_name or name.startswith(f"{model_name}:") for name in models)
        return ok, ("model_listed" if ok else f"Model not listed during prewarm: {model_name}")

    def diagnostics(self) -> dict[str, Any]:
        diag: dict[str, Any] = {
            "backend": self.backend_name,
            "transport_name": self.transport_name,
            "host": self.host or "",
            "last_operation": self._last_operation,
            "last_error": self._last_error,
            "last_model_count": self._last_model_count,
        }
        probe_host = str(self.host or "http://127.0.0.1:11434").strip()
        try:
            resp = httpx.get(probe_host.rstrip("/") + "/api/tags", timeout=2.0)
            diag["direct_probe_ok"] = True
            diag["direct_probe_status"] = int(resp.status_code)
            try:
                rows = (resp.json() or {}).get("models", [])
                diag["probe_model_count"] = len(rows) if isinstance(rows, list) else -1
                if isinstance(rows, list):
                    diag["probe_model_names"] = [
                        str((row or {}).get("model") or (row or {}).get("name") or "").strip()
                        for row in rows[:10]
                        if isinstance(row, dict)
                        and str((row or {}).get("model") or (row or {}).get("name") or "").strip()
                    ]
            except Exception:
                diag["probe_model_count"] = -1
        except Exception as exc:
            diag["direct_probe_ok"] = False
            diag["direct_probe_error"] = str(exc)
        try:
            version_resp = httpx.get(probe_host.rstrip("/") + "/api/version", timeout=2.0)
            diag["version_probe_ok"] = True
            diag["version_probe_status"] = int(version_resp.status_code)
            if int(version_resp.status_code) < 400:
                try:
                    diag["ollama_version"] = str((version_resp.json() or {}).get("version", "") or "")
                except Exception:
                    pass
        except Exception as exc:
            diag["version_probe_ok"] = False
            diag["version_probe_error"] = str(exc)
        return diag


@dataclass
class OpenAICompatibleChatBackend:
    host: str
    timeout_seconds: float
    api_key: str
    provider_name: str = "openai_compatible"

    backend_name: str = field(init=False)
    transport_name: str = field(init=False, default="openai_compatible")
    _path_prefixes: list[str] = field(init=False, default_factory=list)
    _last_request_path: str = field(init=False, default="")
    _last_request_prefix: str = field(init=False, default="")
    _last_error: str = field(init=False, default="")
    _last_status_code: int | None = field(init=False, default=None)
    _last_model_count: int = field(init=False, default=-1)

    def __post_init__(self) -> None:
        self.backend_name = normalize_backend_name(self.provider_name)
        headers = {}
        token = str(self.api_key or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=self.host.rstrip("/"),
            timeout=float(self.timeout_seconds),
            headers=headers,
        )
        self._path_prefixes = _openai_path_prefixes(self.host)

    def _request(
        self,
        method: str,
        path_suffix: str,
        *,
        json_payload: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_response: httpx.Response | None = None
        for prefix in self._path_prefixes:
            path = _openai_path(prefix, path_suffix)
            self._last_request_prefix = prefix or "/"
            self._last_request_path = path
            response = self._client.request(method.upper(), path, json=json_payload)
            self._last_status_code = int(response.status_code)
            if int(response.status_code) < 400:
                self._last_error = ""
                return response
            last_response = response
            if int(response.status_code) != 404:
                break
        assert last_response is not None
        last_response.raise_for_status()
        return last_response

    @staticmethod
    def _payload_variants(payload: dict[str, Any]) -> list[dict[str, Any]]:
        variants: list[dict[str, Any]] = [dict(payload)]
        if "extra_body" in payload:
            variant = dict(payload)
            variant.pop("extra_body", None)
            variants.append(variant)
        if "response_format" in payload:
            variant = dict(payload)
            variant.pop("response_format", None)
            variants.append(variant)
        if "extra_body" in payload and "response_format" in payload:
            variant = dict(payload)
            variant.pop("extra_body", None)
            variant.pop("response_format", None)
            variants.append(variant)
        unique: list[dict[str, Any]] = []
        seen: set[tuple[tuple[str, str], ...]] = set()
        for variant in variants:
            marker = tuple(sorted((str(k), repr(v)) for k, v in variant.items()))
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(variant)
        return unique

    def _response_format_payload(self, format_spec: Any) -> dict[str, Any] | None:
        if format_spec in (None, "", False):
            return None
        if format_spec == "json":
            return {"type": "json_object"}
        if isinstance(format_spec, dict):
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "bioharness_response",
                    "schema": format_spec,
                },
            }
        return None

    def chat(
        self,
        *,
        model_name: str,
        messages: list[Any],
        temperature: float,
        num_ctx: int | None = None,
        num_predict: int | None = None,
        format_spec: Any = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": normalize_messages(messages),
            "temperature": float(temperature),
        }
        if num_predict:
            payload["max_tokens"] = int(num_predict)
        response_format = self._response_format_payload(format_spec)
        if response_format is not None:
            payload["response_format"] = response_format
        if num_ctx:
            payload["extra_body"] = {"num_ctx": int(num_ctx)}
        response: httpx.Response | None = None
        last_exc: Exception | None = None
        for variant in self._payload_variants(payload):
            try:
                response = self._request("POST", "/chat/completions", json_payload=variant)
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                self._last_error = str(exc)
                status = int(getattr(exc.response, "status_code", 0) or 0)
                if status not in {400, 404, 415, 422, 501}:
                    raise
                continue
            except Exception as exc:
                self._last_error = str(exc)
                raise
        if response is None:
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("OpenAI-compatible backend returned no response.")
        data = response.json()
        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message", {})
        return _extract_openai_content(message.get("content", ""))

    def list_models(self) -> list[str]:
        try:
            response = self._request("GET", "/models")
        except Exception as exc:
            self._last_error = str(exc)
            raise
        data = response.json()
        rows = data.get("data", []) if isinstance(data, dict) else []
        out: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = row.get("id") or row.get("model")
            if model_id:
                out.append(str(model_id))
        self._last_model_count = len(out)
        return out

    def is_connectivity_error(self, exc: Exception) -> bool:
        msg = str(exc or "").lower()
        return isinstance(exc, httpx.ConnectError) or any(
            token in msg
            for token in (
                "connection refused",
                "failed to connect",
                "connecterror",
                "nodename nor servname provided",
                "name or service not known",
            )
        )

    def prewarm(self, *, model_name: str, mode: str, timeout_seconds: float) -> tuple[bool, str]:
        headers = dict(self._client.headers)
        client = httpx.Client(base_url=self.host.rstrip("/"), timeout=float(timeout_seconds), headers=headers)
        if mode == "chat":
            response = client.post(
                "/chat/completions",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "reply with warmup"}],
                    "temperature": 0.0,
                    "max_tokens": 8,
                },
            )
            response.raise_for_status()
            return True, "chat_prewarm_ok"
        response = client.get("/models")
        response.raise_for_status()
        rows = response.json().get("data", [])
        models = {str((row or {}).get("id") or "") for row in rows if isinstance(row, dict)}
        ok = model_name in models
        return ok, ("model_listed" if ok else f"Model not listed during prewarm: {model_name}")

    def diagnostics(self) -> dict[str, Any]:
        diag: dict[str, Any] = {
            "backend": self.backend_name,
            "transport_name": self.transport_name,
            "host": self.host or "",
            "path_prefixes": list(self._path_prefixes),
            "last_request_path": self._last_request_path,
            "last_request_prefix": self._last_request_prefix,
            "last_status_code": self._last_status_code,
            "last_error": self._last_error,
            "last_model_count": self._last_model_count,
        }
        try:
            resp = self._request("GET", "/models")
            diag["direct_probe_ok"] = True
            diag["direct_probe_status"] = int(resp.status_code)
            try:
                rows = (resp.json() or {}).get("data", [])
                diag["probe_model_count"] = len(rows) if isinstance(rows, list) else -1
                if isinstance(rows, list):
                    diag["probe_model_names"] = [
                        str((row or {}).get("id") or (row or {}).get("model") or "").strip()
                        for row in rows[:10]
                        if isinstance(row, dict)
                        and str((row or {}).get("id") or (row or {}).get("model") or "").strip()
                    ]
            except Exception:
                diag["probe_model_count"] = -1
        except Exception as exc:
            diag["direct_probe_ok"] = False
            diag["direct_probe_error"] = str(exc)
        return diag


def build_chat_backend(
    *,
    backend_name: str,
    host: str,
    timeout_seconds: float,
    api_key: str = "",
) -> ChatBackend:
    """Construct a :class:`ChatBackend` for the given backend name and host."""
    normalized = normalize_backend_name(backend_name)
    if backend_transport_name(normalized) == "openai_compatible":
        return OpenAICompatibleChatBackend(
            host=host,
            timeout_seconds=timeout_seconds,
            api_key=api_key,
            provider_name=normalized,
        )
    return OllamaChatBackend(host=host, timeout_seconds=timeout_seconds, provider_name=normalized)
