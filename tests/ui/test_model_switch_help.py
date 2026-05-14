from __future__ import annotations

from bio_harness.ui.model_switch_help import build_model_switch_help


def test_build_model_switch_help_for_ollama_mentions_local_model_listing() -> None:
    payload = build_model_switch_help("ollama", "qwen3-coder-next:latest")

    assert "ollama list" in payload["backend_note"]
    assert any("qwen3-coder-next:latest" in example for example in payload["examples"])
    assert payload["gemini_note"] == ""


def test_build_model_switch_help_for_openai_compatible_mentions_api_key() -> None:
    payload = build_model_switch_help("openai_compatible", "gpt-4.1")

    assert "BIO_HARNESS_OPENAI_API_KEY" in payload["host_help"]
    assert any("gemini-2.5-flash" in example for example in payload["examples"])


def test_build_model_switch_help_surfaces_gemini_specific_note() -> None:
    payload = build_model_switch_help("openai_compatible", "gemini-2.5-flash")

    assert "Gemini models should use the `openai_compatible` backend." in payload["gemini_note"]
