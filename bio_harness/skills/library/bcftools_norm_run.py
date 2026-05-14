"""Atomic wrapper for one ``bcftools norm`` operation."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "run_bcftools_norm.py"


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    """Return one stable boolean from wrapper input."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "y", "on"}:
        return True
    if token in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value)


def bcftools_norm_run(**kwargs: object) -> str:
    """Render one atomic ``bcftools norm`` helper invocation.

    Args:
        **kwargs: Wrapper arguments from the harness plan.

    Returns:
        Shell-safe helper command.

    Raises:
        ValueError: If one required parameter is missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_vcf = str(kwargs.get("input_vcf", "")).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    if not input_vcf or not reference_fasta or not output_vcf:
        raise ValueError("Missing required parameter(s) for template: input_vcf, reference_fasta, output_vcf")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    command_parts.extend(
        [
            "--input-vcf",
            input_vcf,
            "--reference-fasta",
            reference_fasta,
            "--output-vcf",
            output_vcf,
            "--multiallelic-mode",
            str(kwargs.get("multiallelic_mode", "-any") or "-any").strip() or "-any",
        ]
    )
    if _coerce_bool(kwargs.get("atomize", False), default=False):
        command_parts.append("--atomize")
    return _render_shell_parts(command_parts)
