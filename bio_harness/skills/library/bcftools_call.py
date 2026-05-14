from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "run_bcftools_call.py"


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def bcftools_call(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    output_vcf_gz = str(kwargs.get("output_vcf_gz", "")).strip()
    if not output_vcf_gz:
        raise ValueError("Missing required parameter(s) for template: output_vcf_gz")
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    input_bam = str(kwargs.get("input_bam", "")).strip()
    if not reference_fasta or not input_bam:
        raise ValueError("Missing required parameter(s) for template: reference_fasta, input_bam, output_vcf_gz")

    command_parts = managed_python_command_parts(
        python_executable=str(preferred_helper_python_executable()),
        script_path=str(_SCRIPT_PATH),
    )
    command_parts.extend(
        [
            "--reference-fasta",
            reference_fasta,
            "--input-bam",
            input_bam,
            "--output-vcf-gz",
            output_vcf_gz,
        ]
    )
    return _render_shell_parts(command_parts)
