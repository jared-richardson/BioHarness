"""Deterministic guidance for switching model backends in the Streamlit UI."""

from __future__ import annotations

from typing import Any

from bio_harness.core.llm_backends import normalize_backend_name


def build_model_switch_help(backend_name: str, model_name: str) -> dict[str, Any]:
    """Return sidebar help text for the selected backend/model combination.

    Args:
        backend_name: Raw backend identifier selected in the UI.
        model_name: Raw model name currently entered in the UI.

    Returns:
        A deterministic payload with steps, examples, and any backend-specific
        notes needed to switch models safely in the UI.
    """
    normalized_backend = normalize_backend_name(backend_name)
    entered_model = str(model_name or "").strip()
    entered_model_l = entered_model.lower()
    generic_steps = [
        "Choose the backend first. The model field is interpreted by that backend.",
        "Enter the exact model id exposed by the selected backend.",
        "If the backend changes, review the host/base URL before sending the next request.",
        "The new model takes effect on the next chat, planning, or execution request.",
    ]
    host_help = "Use the API host/base URL for the selected backend."
    examples: list[str] = []
    backend_note = ""
    gemini_note = ""

    if normalized_backend == "ollama":
        host_help = "Native Ollama usually stays on http://127.0.0.1:11434."
        examples = [
            "`qwen3-coder-next:latest` on `ollama`",
            "`gemma4:26b` on `ollama`",
        ]
        backend_note = (
            "This path expects a model name that appears in `ollama list`."
        )
    elif normalized_backend == "ollama_openai":
        host_help = "Use Ollama's OpenAI-compatible `/v1` base URL."
        examples = [
            "`qwen3-coder-next:latest` on `ollama_openai`",
            "`gemma4:26b` on `ollama_openai`",
        ]
        backend_note = "Use this when you want Ollama through an OpenAI-compatible transport."
    elif normalized_backend in {"mlx", "vllm"}:
        host_help = "Use the OpenAI-compatible base URL served by this backend."
        examples = [
            "`<served-model-name>` on the selected OpenAI-compatible backend",
        ]
        backend_note = (
            "The model name must match an entry returned by that server's `/models` endpoint."
        )
    else:
        host_help = "Use the full OpenAI-compatible base URL and set `BIO_HARNESS_OPENAI_API_KEY` if required."
        examples = [
            "`gemini-2.5-flash` on `openai_compatible`",
            "`gemini-2.5-pro` on `openai_compatible`",
            "`gpt-4.1` on `openai_compatible`",
        ]
        backend_note = (
            "This path works for generic OpenAI-compatible providers, including Gemini-compatible endpoints."
        )

    if "gemini" in entered_model_l or normalized_backend == "openai_compatible":
        gemini_note = (
            "Gemini models should use the `openai_compatible` backend. "
            "Set the backend host to your Gemini-compatible OpenAI base URL and "
            "provide `BIO_HARNESS_OPENAI_API_KEY` before sending requests."
        )

    return {
        "steps": generic_steps,
        "host_help": host_help,
        "examples": examples,
        "backend_note": backend_note,
        "gemini_note": gemini_note,
    }
