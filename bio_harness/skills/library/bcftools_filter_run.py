"""Atomic wrapper for one ``bcftools filter`` operation."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "run_bcftools_filter.py"


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def bcftools_filter_run(**kwargs: object) -> str:
    """Render one atomic ``bcftools filter`` helper invocation.

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
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    filter_expression = str(kwargs.get("filter_expression", "")).strip()
    if not input_vcf or not output_vcf or not filter_expression:
        raise ValueError(
            "Missing required parameter(s) for template: input_vcf, output_vcf, filter_expression"
        )

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    command_parts.extend(
        [
            "--input-vcf",
            input_vcf,
            "--output-vcf",
            output_vcf,
            "--filter-expression",
            filter_expression,
            "--output-type",
            str(kwargs.get("output_type", "z") or "z").strip() or "z",
        ]
    )
    soft_filter_name = str(kwargs.get("soft_filter_name", "") or "").strip()
    if soft_filter_name:
        command_parts.extend(["--soft-filter-name", soft_filter_name])
    return _render_shell_parts(command_parts)
