"""Atomic wrapper for one ``bcftools isec`` operation."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "run_bcftools_isec.py"


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def _normalize_input_vcfs(raw_value: object) -> list[str]:
    """Return a normalized list of VCF paths from wrapper input."""

    if isinstance(raw_value, str):
        return [token for token in raw_value.split() if token.strip()]
    if isinstance(raw_value, (list, tuple, set)):
        return [str(token).strip() for token in raw_value if str(token).strip()]
    return []


def bcftools_isec_run(**kwargs: object) -> str:
    """Render one atomic ``bcftools isec`` helper invocation.

    Args:
        **kwargs: Wrapper arguments from the harness plan.

    Returns:
        Shell-safe helper command.

    Raises:
        ValueError: If one required parameter is missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_vcfs = _normalize_input_vcfs(kwargs.get("input_vcfs", []))
    output_dir = str(kwargs.get("output_dir", "")).strip()
    if len(input_vcfs) < 2 or not output_dir:
        raise ValueError("Missing required parameter(s) for template: input_vcfs, output_dir")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    for input_vcf in input_vcfs:
        command_parts.extend(["--input-vcf", input_vcf])
    command_parts.extend(
        [
            "--output-dir",
            output_dir,
            "--mode",
            str(kwargs.get("mode", "intersection") or "intersection").strip() or "intersection",
            "--min-matches",
            str(kwargs.get("min_matches", 2) or 2),
        ]
    )
    output_vcf = str(kwargs.get("output_vcf", "") or "").strip()
    if output_vcf:
        command_parts.extend(["--output-vcf", output_vcf])
    return _render_shell_parts(command_parts)
