"""Atomic wrapper for the shared-variants CSV exporter."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "export_shared_variants_csv.py"


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


def shared_variants_export_run(**kwargs: object) -> str:
    """Render one atomic shared-variant export helper invocation.

    Args:
        **kwargs: Wrapper arguments from the harness plan.

    Returns:
        Shell-safe helper command.

    Raises:
        ValueError: If one required parameter is missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_vcf_a = str(kwargs.get("input_vcf_a", "")).strip()
    input_vcf_b = str(kwargs.get("input_vcf_b", "")).strip()
    output_csv = str(kwargs.get("output_csv", "")).strip()
    if not input_vcf_a or not input_vcf_b or not output_csv:
        raise ValueError("Missing required parameter(s) for template: input_vcf_a, input_vcf_b, output_csv")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    command_parts.extend(
        [
            "--input-vcf-a",
            input_vcf_a,
            "--input-vcf-b",
            input_vcf_b,
            "--output-csv",
            output_csv,
            "--min-impact",
            str(kwargs.get("min_impact", "MODERATE") or "MODERATE").strip() or "MODERATE",
            "--status",
            str(kwargs.get("status", "shared") or "shared").strip() or "shared",
            "--header-case",
            str(kwargs.get("header_case", "upper") or "upper").strip() or "upper",
        ]
    )
    if _coerce_bool(kwargs.get("dedupe_by_gene", True), default=True):
        command_parts.append("--dedupe-by-gene")
    return _render_shell_parts(command_parts)
