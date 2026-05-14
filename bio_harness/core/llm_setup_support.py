"""Deterministic setup guidance for local Bio-Harness LLM backends.

This module builds a user-facing setup report that answers the practical
questions non-technical operators tend to have:

- Is the selected backend reachable?
- Is the requested model present?
- Which commands should I run next?
- Which currently available models would Bio-Harness choose by default?

The report is designed for three surfaces:

1. a CLI helper script
2. deterministic help context available to the model
3. UI onboarding/status blocks when the runtime is not ready
"""

from __future__ import annotations

import os
import shutil
from typing import Any

_DEFAULT_SETUP_MODEL = "qwen3-coder-next:latest"
_BACKEND_DEFAULT_HOSTS = {
    "ollama": "http://127.0.0.1:11434",
    "ollama_openai": "http://127.0.0.1:11434",
    "mlx": "http://127.0.0.1:8080",
    "vllm": "http://127.0.0.1:8000",
    "openai_compatible": "http://127.0.0.1:8000/v1",
}
_BACKEND_LABELS = {
    "ollama": "Ollama",
    "ollama_openai": "Ollama OpenAI-compatible API",
    "mlx": "MLX OpenAI-compatible server",
    "vllm": "vLLM OpenAI-compatible server",
    "openai_compatible": "OpenAI-compatible backend",
}
_BACKEND_HOST_ENV_VARS = {
    "ollama": "BIO_HARNESS_OLLAMA_HOST",
    "ollama_openai": "BIO_HARNESS_OLLAMA_OPENAI_BASE_URL",
    "mlx": "BIO_HARNESS_MLX_BASE_URL",
    "vllm": "BIO_HARNESS_VLLM_BASE_URL",
    "openai_compatible": "BIO_HARNESS_OPENAI_BASE_URL",
}
_LLM_SETUP_QUERY_PHRASES = (
    "set up ollama",
    "setup ollama",
    "set up model",
    "setup model",
    "model setup",
    "backend setup",
    "pull the right model",
    "pull model",
    "model not available",
    "model unavailable",
    "get the model ready",
    "get ollama ready",
    "first run",
)


def normalize_setup_backend_name(name: str | None) -> str:
    """Canonicalize a backend name for setup reporting.

    Args:
        name: User-supplied backend name.

    Returns:
        One of the supported backend identifiers.
    """
    raw = str(name or "ollama").strip().lower()
    if raw in {"openai", "openai-compatible", "openai_compat"}:
        return "openai_compatible"
    if raw in {"vllm", "vllm_openai"}:
        return "vllm"
    if raw in {"mlx", "mlx_lm", "mlx-openai", "mlx_openai"}:
        return "mlx"
    if raw in {"ollama_openai", "ollama-v1", "ollama_openai_compatible"}:
        return "ollama_openai"
    return "ollama" if raw != "ollama" else raw


def backend_default_host_for_setup(backend_name: str) -> str:
    """Return the default host URL for the selected backend.

    Args:
        backend_name: Canonical backend identifier.

    Returns:
        Default host URL for that backend.
    """
    return _BACKEND_DEFAULT_HOSTS.get(backend_name, _BACKEND_DEFAULT_HOSTS["ollama"])


def backend_label_for_setup(backend_name: str) -> str:
    """Return a human-readable label for the selected backend.

    Args:
        backend_name: Canonical backend identifier.

    Returns:
        Human-readable backend label.
    """
    return _BACKEND_LABELS.get(backend_name, _BACKEND_LABELS["ollama"])


def backend_host_env_var_for_setup(backend_name: str) -> str:
    """Return the host environment variable for the selected backend.

    Args:
        backend_name: Canonical backend identifier.

    Returns:
        Environment variable name for the backend host URL.
    """
    return _BACKEND_HOST_ENV_VARS.get(backend_name, _BACKEND_HOST_ENV_VARS["ollama"])


def _command_available(command_name: str) -> bool:
    """Return whether a shell command is available on the current PATH."""
    return shutil.which(str(command_name).strip()) is not None


def looks_like_llm_setup_query(text: str) -> bool:
    """Return whether a user message is asking about local model setup.

    Args:
        text: Raw user message.

    Returns:
        True when the message is about making the local model backend ready.
    """
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if any(phrase in lowered for phrase in _LLM_SETUP_QUERY_PHRASES):
        return True
    token_hits = sum(
        1 for token in ("ollama", "model", "backend", "setup", "pull", "ready") if token in lowered
    )
    return token_hits >= 3


def _safe_import_backend_helpers() -> tuple[dict[str, Any] | None, str]:
    """Import backend helpers lazily so CLI ``--help`` stays clean.

    Returns:
        Tuple of ``(helpers, error_message)``. ``helpers`` is ``None`` when the
        runtime dependencies are unavailable.
    """
    try:
        from bio_harness.core.llm_backend_probe import probe_llm_backend
        from bio_harness.core.llm_backends import build_chat_backend
        from bio_harness.core.model_router import discover_models, select_default_models
    except (ImportError, ModuleNotFoundError) as exc:
        missing = str(getattr(exc, "name", "") or "").strip()
        if missing:
            return None, f"missing Python dependency `{missing}`"
        return None, str(exc) or "missing Python runtime dependency"
    return {
        "probe_llm_backend": probe_llm_backend,
        "build_chat_backend": build_chat_backend,
        "discover_models": discover_models,
        "select_default_models": select_default_models,
    }, ""


def _model_matches(requested_model: str, candidate_name: str) -> bool:
    """Return whether an available model name satisfies the requested model."""
    requested = str(requested_model).strip()
    candidate = str(candidate_name).strip()
    if not requested or not candidate:
        return False
    return candidate == requested or candidate.startswith(f"{requested}:")


def _discover_available_models(
    *,
    backend_name: str,
    host: str,
    helpers: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]], dict[str, str], str]:
    """List available models and select Bio-Harness defaults.

    Args:
        backend_name: Canonical backend name.
        host: Backend host URL.
        helpers: Helper callables from ``_safe_import_backend_helpers``.

    Returns:
        Tuple of ``(reachable, rows, defaults, error_message)``.
    """
    api_key = str(os.getenv("BIO_HARNESS_OPENAI_API_KEY", "") or "").strip()
    build_chat_backend = helpers["build_chat_backend"]
    discover_models = helpers["discover_models"]
    select_default_models = helpers["select_default_models"]
    try:
        backend = build_chat_backend(
            backend_name=backend_name,
            host=host,
            timeout_seconds=12.0,
            api_key=api_key,
        )
        discovered = discover_models(backend)
    except Exception as exc:
        return False, [], {"planner": "", "executor": ""}, str(exc)

    defaults = {"planner": "", "executor": ""}
    if discovered:
        planner, executor = select_default_models(discovered)
        defaults = {"planner": planner, "executor": executor}

    rows: list[dict[str, Any]] = []
    for model in discovered:
        rows.append(
            {
                "name": str(getattr(model, "name", "") or "").strip(),
                "family": str(getattr(model, "family", "") or "").strip(),
                "tier": str(getattr(model, "tier", "") or "").strip(),
                "parameter_count_b": float(getattr(model, "parameter_count_b", 0.0) or 0.0),
                "size_gb": float(getattr(model, "size_gb", 0.0) or 0.0),
            }
        )
    return True, rows, defaults, ""


def _ollama_pull_model(model_name: str, *, host: str | None = None) -> dict[str, Any]:
    """Attempt to pull an Ollama model and return a structured receipt."""
    from bio_harness.core.ollama_setup import pull_ollama_model

    result = pull_ollama_model(
        model_name=model_name,
        host=host,
        timeout_seconds=None,
    )
    latest_events = list(result.get("events", []) or [])
    latest_status = str(latest_events[-1].get("status", "") or "").strip() if latest_events else ""
    error = str(result.get("error", "") or "").strip()
    summary = "\n".join(part for part in [latest_status, error] if part).strip()
    return {
        "attempted": True,
        "succeeded": bool(result.get("succeeded", False)),
        "returncode": 0 if bool(result.get("succeeded", False)) else 1,
        "summary": summary[-1200:] if summary else "",
    }


def _build_env_commands(
    *,
    backend_name: str,
    host: str,
    model_name: str,
    planner_model: str,
) -> list[str]:
    """Build shell exports that pin the chosen backend/model selection."""
    host_env_var = backend_host_env_var_for_setup(backend_name)
    commands = [
        f"export BIO_HARNESS_LLM_BACKEND={backend_name}",
        f"export {host_env_var}={host}",
        f"export BIO_HARNESS_MODEL={model_name}",
    ]
    if planner_model and planner_model != model_name:
        commands.append(f"export BIO_HARNESS_MODEL_HEAVY={planner_model}")
    return commands


def _build_next_steps(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Build user-facing next steps and concrete commands."""
    backend_name = str(report.get("backend_name", "")).strip()
    backend_label = str(report.get("backend_label", "")).strip()
    model_name = str(report.get("model_name", "")).strip()
    host = str(report.get("host", "")).strip()
    command_available = (
        bool(report.get("ollama_command_available"))
        if backend_name in {"ollama", "ollama_openai"}
        else None
    )
    dependencies_ready = bool(report.get("python_runtime_ready", True))
    backend_reachable = bool(report.get("backend_reachable", False))
    model_present = bool(report.get("model_present", False))
    ready = bool(report.get("ready", False))
    defaults = dict(report.get("recommended_defaults", {}) or {})
    recommended_executor = str(defaults.get("executor", "")).strip()
    recommended_planner = str(defaults.get("planner", "")).strip()

    next_steps: list[str] = []
    commands: list[str] = []

    if not dependencies_ready:
        next_steps.append(
            "Bootstrap the Bio-Harness Python environment so the local helper "
            "and backend probes can run."
        )
        commands.append("python3 scripts/bootstrap_bioharness.py")

    if backend_name in {"ollama", "ollama_openai"} and command_available is False:
        next_steps.append(
            "Install Ollama first. Bio-Harness can talk to a local Ollama "
            "server, but it cannot start or pull models without the Ollama CLI."
        )
        commands.append("Open https://ollama.com/download")

    if backend_name in {"ollama", "ollama_openai"} and command_available and not backend_reachable:
        next_steps.append(
            f"Start the {backend_label} server on localhost before launching the harness."
        )
        commands.append("ollama serve")

    if backend_reachable and model_name and not model_present:
        next_steps.append(
            f"Expose or pull the requested model `{model_name}` so the selected backend can use it."
        )
        if backend_name in {"ollama", "ollama_openai"} and command_available:
            commands.append(f"ollama pull {model_name}")

    if backend_reachable and recommended_executor and recommended_executor != model_name:
        next_steps.append(
            f"Bio-Harness would currently default to `{recommended_executor}` "
            "from the models already available on this backend."
        )

    if ready:
        next_steps.append("The backend is ready. You can launch the UI or CLI workflows now.")
        commands.extend(
            _build_env_commands(
                backend_name=backend_name,
                host=host,
                model_name=model_name or recommended_executor or _DEFAULT_SETUP_MODEL,
                planner_model=recommended_planner,
            )
        )
        commands.append(".venv/bin/streamlit run app.py")
    else:
        commands.append(
            "python3 scripts/setup_llm_backend.py "
            f"--llm-backend {backend_name} --model-name {model_name or _DEFAULT_SETUP_MODEL}"
        )
        commands.append("python3 scripts/doctor_bioharness.py --probe-llm-backend")

    deduped_steps = list(dict.fromkeys(step.strip() for step in next_steps if step.strip()))
    deduped_commands = list(dict.fromkeys(cmd.strip() for cmd in commands if cmd.strip()))
    return deduped_steps, deduped_commands


def build_llm_setup_report(
    *,
    llm_backend: str | None = None,
    model_name: str | None = None,
    host: str | None = None,
    pull_if_missing: bool = False,
) -> dict[str, Any]:
    """Build a deterministic LLM-backend setup report.

    Args:
        llm_backend: Requested backend name.
        model_name: Requested model name.
        host: Optional explicit backend host.
        pull_if_missing: Whether to pull the requested Ollama model when it is
            missing and the CLI is available.

    Returns:
        Structured readiness and next-step information suitable for CLI, UI, or
        prompt-context use.
    """
    backend_name = normalize_setup_backend_name(llm_backend)
    resolved_model = str(
        model_name or os.getenv("BIO_HARNESS_MODEL", _DEFAULT_SETUP_MODEL) or _DEFAULT_SETUP_MODEL
    ).strip()
    resolved_host = str(
        host or os.getenv(backend_host_env_var_for_setup(backend_name), "") or ""
    ).strip()
    if not resolved_host:
        resolved_host = backend_default_host_for_setup(backend_name)

    report: dict[str, Any] = {
        "backend_name": backend_name,
        "backend_label": backend_label_for_setup(backend_name),
        "host": resolved_host,
        "model_name": resolved_model,
        "ready": False,
        "status_message": "",
        "python_runtime_ready": True,
        "python_runtime_error": "",
        "backend_reachable": False,
        "model_present": False,
        "ollama_command_available": None,
        "available_models": [],
        "recommended_defaults": {"planner": "", "executor": ""},
        "diagnostics": {},
        "pull_result": {"attempted": False, "succeeded": False, "summary": "", "returncode": 0},
        "next_steps": [],
        "recommended_commands": [],
    }

    if backend_name in {"ollama", "ollama_openai"}:
        report["ollama_command_available"] = _command_available("ollama")

    helpers, import_error = _safe_import_backend_helpers()
    if helpers is None:
        report["python_runtime_ready"] = False
        report["python_runtime_error"] = import_error
        report["status_message"] = (
            "Bio-Harness Python runtime dependencies are not ready yet; "
            "bootstrap the repo before probing model backends."
        )
        report["next_steps"], report["recommended_commands"] = _build_next_steps(report)
        return report

    probe = helpers["probe_llm_backend"](
        llm_backend=backend_name,
        model_name=resolved_model,
        host=resolved_host,
        probe_text=False,
        probe_plan=False,
    )
    report["ready"] = bool(probe.get("available", False))
    report["status_message"] = str(probe.get("message", "")).strip()
    report["diagnostics"] = dict(probe.get("diagnostics", {}) or {})

    reachable, rows, defaults, discover_error = _discover_available_models(
        backend_name=backend_name,
        host=resolved_host,
        helpers=helpers,
    )
    report["backend_reachable"] = reachable
    report["available_models"] = rows
    report["recommended_defaults"] = defaults
    report["model_present"] = any(
        _model_matches(resolved_model, row.get("name", "")) for row in rows
    )
    if not report["status_message"] and discover_error:
        report["status_message"] = discover_error

    if (
        pull_if_missing
        and backend_name == "ollama"
        and bool(report.get("ollama_command_available"))
        and resolved_model
        and not report["model_present"]
    ):
        report["pull_result"] = _ollama_pull_model(resolved_model, host=resolved_host)
        if bool(report["pull_result"].get("succeeded", False)):
            probe = helpers["probe_llm_backend"](
                llm_backend=backend_name,
                model_name=resolved_model,
                host=resolved_host,
                probe_text=False,
                probe_plan=False,
            )
            report["ready"] = bool(probe.get("available", False))
            report["status_message"] = str(probe.get("message", "")).strip()
            report["diagnostics"] = dict(probe.get("diagnostics", {}) or {})
            reachable, rows, defaults, discover_error = _discover_available_models(
                backend_name=backend_name,
                host=resolved_host,
                helpers=helpers,
            )
            report["backend_reachable"] = reachable
            report["available_models"] = rows
            report["recommended_defaults"] = defaults
            report["model_present"] = any(
                _model_matches(resolved_model, row.get("name", "")) for row in rows
            )
            if not report["status_message"] and discover_error:
                report["status_message"] = discover_error

    report["next_steps"], report["recommended_commands"] = _build_next_steps(report)
    return report


def render_llm_setup_text(report: dict[str, Any]) -> str:
    """Render an LLM setup report as a compact text guide.

    Args:
        report: Output from ``build_llm_setup_report``.

    Returns:
        Deterministic multi-section text describing readiness and next steps.
    """
    lines = [
        "## LLM Backend Setup",
        (
            f"- Backend: {str(report.get('backend_label', '')).strip()} "
            f"[{str(report.get('backend_name', '')).strip()}]"
        ),
        f"- Host: {str(report.get('host', '')).strip()}",
        f"- Requested model: {str(report.get('model_name', '')).strip()}",
        f"- Ready: {'yes' if bool(report.get('ready', False)) else 'no'}",
        f"- Status: {str(report.get('status_message', '')).strip() or 'No status message.'}",
    ]

    if report.get("python_runtime_error"):
        lines.append(
            f"- Python runtime note: {str(report.get('python_runtime_error', '')).strip()}"
        )
    if report.get("ollama_command_available") is not None:
        lines.append(
            "- Ollama CLI available: "
            f"{'yes' if bool(report.get('ollama_command_available')) else 'no'}"
        )

    available_models = list(report.get("available_models", []) or [])
    if available_models:
        lines.append("")
        lines.append("## Available Models")
        for row in available_models[:8]:
            name = str(row.get("name", "")).strip()
            tier = str(row.get("tier", "")).strip()
            family = str(row.get("family", "")).strip()
            size_gb = float(row.get("size_gb", 0.0) or 0.0)
            param_b = float(row.get("parameter_count_b", 0.0) or 0.0)
            suffix_parts = []
            if tier:
                suffix_parts.append(tier)
            if family:
                suffix_parts.append(family)
            if param_b > 0:
                suffix_parts.append(f"{param_b:.0f}B")
            if size_gb > 0:
                suffix_parts.append(f"{size_gb:.1f} GB")
            suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
            lines.append(f"- {name}{suffix}")

    defaults = dict(report.get("recommended_defaults", {}) or {})
    default_executor = str(defaults.get("executor", "")).strip()
    default_planner = str(defaults.get("planner", "")).strip()
    if default_executor or default_planner:
        lines.append("")
        lines.append("## Bio-Harness Defaults From Currently Available Models")
        if default_executor:
            lines.append(f"- Default executor: {default_executor}")
        if default_planner:
            lines.append(f"- Default planner: {default_planner}")

    pull_result = dict(report.get("pull_result", {}) or {})
    if pull_result.get("attempted"):
        lines.append("")
        lines.append("## Pull Attempt")
        lines.append(
            f"- Succeeded: {'yes' if bool(pull_result.get('succeeded', False)) else 'no'} "
            f"(exit {int(pull_result.get('returncode', 0) or 0)})"
        )
        if str(pull_result.get("summary", "")).strip():
            lines.append(f"- Summary: {str(pull_result.get('summary', '')).strip()}")

    next_steps = list(report.get("next_steps", []) or [])
    if next_steps:
        lines.append("")
        lines.append("## Next Steps")
        for step in next_steps:
            lines.append(f"1. {str(step).strip()}")

    commands = list(report.get("recommended_commands", []) or [])
    if commands:
        lines.append("")
        lines.append("## Recommended Commands")
        for command in commands:
            lines.append(f"- `{str(command).strip()}`")

    return "\n".join(lines).strip() + "\n"
