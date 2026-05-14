"""Render safe helper commands for the ``prodigal_annotate`` skill."""

from __future__ import annotations

from pathlib import Path
import shlex

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "pipeline_scripts"
    / "run_prodigal_annotate.py"
)


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw argv parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def prodigal_annotate(**kwargs) -> str:
    """Render one atomic ``prodigal`` gene-prediction helper invocation.

    Args:
        **kwargs: Wrapper arguments from the harness plan.

    Returns:
        Shell-safe helper command.

    Raises:
        ValueError: If one required parameter is missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_fasta = str(kwargs.get("input_fasta", "")).strip()
    output_gff = str(kwargs.get("output_gff", "")).strip()
    output_faa = str(kwargs.get("output_faa", "")).strip()
    missing = [
        name
        for name, value in (
            ("input_fasta", input_fasta),
            ("output_gff", output_gff),
            ("output_faa", output_faa),
        )
        if not value
    ]
    if missing:
        missing_args = ", ".join(missing)
        raise ValueError(f"Missing required parameter(s) for template: {missing_args}")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    command_parts.extend(
        [
            "--input-fasta",
            input_fasta,
            "--output-gff",
            output_gff,
            "--output-faa",
            output_faa,
            "--mode",
            str(kwargs.get("mode", "auto") or "auto").strip() or "auto",
        ]
    )
    allow_empty = str(kwargs.get("require_cds", "true")).strip().lower() in {
        "0",
        "false",
        "no",
    }
    if allow_empty:
        command_parts.append("--allow-empty-cds")
    return _render_shell_parts(command_parts)
