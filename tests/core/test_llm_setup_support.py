from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bio_harness.core import llm_setup_support


def test_setup_llm_backend_cli_help_succeeds_on_clean_interpreter() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "scripts" / "setup_llm_backend.py"

    completed = subprocess.run(
        [sys.executable, "-S", str(script_path), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    assert "Check and explain local LLM-backend setup for Bio-Harness." in completed.stdout


def test_build_llm_setup_report_handles_missing_python_runtime_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(
        llm_setup_support,
        "_safe_import_backend_helpers",
        lambda: (None, "missing Python dependency `httpx`"),
    )
    monkeypatch.setattr(llm_setup_support, "_command_available", lambda _name: False)

    report = llm_setup_support.build_llm_setup_report(
        llm_backend="ollama",
        model_name="qwen3-coder-next:latest",
    )

    assert report["ready"] is False
    assert report["python_runtime_ready"] is False
    assert "bootstrap the repo" in report["status_message"].lower()
    assert any("bootstrap_bioharness.py" in command for command in report["recommended_commands"])


def test_build_llm_setup_report_surfaces_missing_model_and_available_defaults(monkeypatch) -> None:
    helpers = {
        "probe_llm_backend": lambda **_kwargs: {
            "available": False,
            "message": "Ollama reachable, but model 'missing-model:latest' is not pulled.",
            "diagnostics": {"backend": "ollama"},
        }
    }
    monkeypatch.setattr(llm_setup_support, "_safe_import_backend_helpers", lambda: (helpers, ""))
    monkeypatch.setattr(llm_setup_support, "_command_available", lambda _name: True)
    monkeypatch.setattr(
        llm_setup_support,
        "_discover_available_models",
        lambda **_kwargs: (
            True,
            [
                {
                    "name": "qwen3-coder-next:latest",
                    "family": "qwen3next",
                    "tier": "fast",
                    "parameter_count_b": 14.0,
                    "size_gb": 8.0,
                }
            ],
            {"planner": "qwen3-coder-next:latest", "executor": "qwen3-coder-next:latest"},
            "",
        ),
    )

    report = llm_setup_support.build_llm_setup_report(
        llm_backend="ollama",
        model_name="missing-model:latest",
    )

    assert report["backend_reachable"] is True
    assert report["model_present"] is False
    assert report["recommended_defaults"]["executor"] == "qwen3-coder-next:latest"
    assert any(
        "ollama pull missing-model:latest" in command for command in report["recommended_commands"]
    )
    assert any("default to `qwen3-coder-next:latest`" in step for step in report["next_steps"])


def test_build_llm_setup_report_can_pull_missing_ollama_model(monkeypatch) -> None:
    probe_responses = iter(
        [
            {
                "available": False,
                "message": "Ollama reachable, but model 'missing-model:latest' is not pulled.",
                "diagnostics": {"backend": "ollama"},
            },
            {
                "available": True,
                "message": "Connected to Ollama. Model ready: missing-model:latest",
                "diagnostics": {"backend": "ollama"},
            },
        ]
    )
    helpers = {
        "probe_llm_backend": lambda **_kwargs: next(probe_responses),
    }
    discover_responses = iter(
        [
            (
                True,
                [
                    {
                        "name": "qwen3-coder-next:latest",
                        "family": "qwen3next",
                        "tier": "fast",
                        "parameter_count_b": 14.0,
                        "size_gb": 8.0,
                    }
                ],
                {"planner": "qwen3-coder-next:latest", "executor": "qwen3-coder-next:latest"},
                "",
            ),
            (
                True,
                [
                    {
                        "name": "missing-model:latest",
                        "family": "qwen3next",
                        "tier": "fast",
                        "parameter_count_b": 14.0,
                        "size_gb": 8.0,
                    }
                ],
                {"planner": "missing-model:latest", "executor": "missing-model:latest"},
                "",
            ),
        ]
    )
    monkeypatch.setattr(llm_setup_support, "_safe_import_backend_helpers", lambda: (helpers, ""))
    monkeypatch.setattr(llm_setup_support, "_command_available", lambda _name: True)
    monkeypatch.setattr(
        llm_setup_support,
        "_discover_available_models",
        lambda **_kwargs: next(discover_responses),
    )
    monkeypatch.setattr(
        llm_setup_support,
        "_ollama_pull_model",
        lambda _model_name, **_kwargs: {
            "attempted": True,
            "succeeded": True,
            "returncode": 0,
            "summary": "pulled successfully",
        },
    )

    report = llm_setup_support.build_llm_setup_report(
        llm_backend="ollama",
        model_name="missing-model:latest",
        pull_if_missing=True,
    )

    assert report["ready"] is True
    assert report["model_present"] is True
    assert report["pull_result"]["attempted"] is True
    assert report["pull_result"]["succeeded"] is True
    assert any(
        ".venv/bin/streamlit run app.py" in command for command in report["recommended_commands"]
    )


def test_render_llm_setup_text_includes_available_models_and_commands() -> None:
    text = llm_setup_support.render_llm_setup_text(
        {
            "backend_name": "ollama",
            "backend_label": "Ollama",
            "host": "http://127.0.0.1:11434",
            "model_name": "qwen3-coder-next:latest",
            "ready": True,
            "status_message": "Connected to Ollama. Model ready: qwen3-coder-next:latest",
            "python_runtime_error": "",
            "ollama_command_available": True,
            "available_models": [
                {
                    "name": "qwen3-coder-next:latest",
                    "family": "qwen3next",
                    "tier": "fast",
                    "parameter_count_b": 14.0,
                    "size_gb": 8.0,
                }
            ],
            "recommended_defaults": {
                "planner": "qwen3-coder-next:latest",
                "executor": "qwen3-coder-next:latest",
            },
            "pull_result": {"attempted": False, "succeeded": False, "summary": "", "returncode": 0},
            "next_steps": ["The backend is ready. You can launch the UI or CLI workflows now."],
            "recommended_commands": [".venv/bin/streamlit run app.py"],
        }
    )

    assert "## LLM Backend Setup" in text
    assert "## Available Models" in text
    assert "qwen3-coder-next:latest" in text
    assert "## Recommended Commands" in text
    assert ".venv/bin/streamlit run app.py" in text


def test_looks_like_llm_setup_query_matches_setup_intents() -> None:
    assert llm_setup_support.looks_like_llm_setup_query(
        "How do I set up Ollama and pull the right model?"
    )
    assert llm_setup_support.looks_like_llm_setup_query(
        "The model backend is not ready; what do I do?"
    )
    assert not llm_setup_support.looks_like_llm_setup_query("Run STAR on these reads.")
