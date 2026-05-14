from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from bio_harness.core.analysis_spec_support import (
    managed_python_command_parts,
    preferred_helper_python_executable,
)
from bio_harness.core.tool_env import which_with_pixi


_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "pipeline_scripts" / "run_freebayes_call.py"


def _require_optional_helper(tool_name: str, *, context: str) -> str:
    """Resolve an optional helper tool or raise a deterministic message."""

    resolved = which_with_pixi(tool_name) or shutil.which(tool_name)
    if resolved:
        return shlex.quote(str(resolved))
    raise ValueError(f"{context} requires helper tool '{tool_name}' to be available.")


def _render_shell_parts(parts: list[str]) -> str:
    """Render one shell-safe command from raw parts."""

    return " ".join(shlex.quote(str(part)) for part in parts if str(part or "").strip())


def freebayes_call(**kwargs) -> str:
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    output_vcf_gz = str(kwargs.get("output_vcf_gz", "")).strip()
    ploidy = str(kwargs.get("ploidy", "")).strip()
    if not output_vcf and not output_vcf_gz:
        raise ValueError("Missing required parameter(s) for template: output_vcf or output_vcf_gz")
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    input_bam = str(kwargs.get("input_bam", "")).strip()
    if not reference_fasta or not input_bam:
        raise ValueError("Missing required parameter(s) for template: reference_fasta, input_bam")

    if output_vcf_gz:
        _require_optional_helper("bgzip", context="freebayes_call compressed VCF output")
        _require_optional_helper("tabix", context="freebayes_call compressed VCF output")

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
        ]
    )
    if output_vcf:
        command_parts.extend(["--output-vcf", output_vcf])
    if output_vcf_gz:
        command_parts.extend(["--output-vcf-gz", output_vcf_gz])
    if ploidy:
        command_parts.extend(["--ploidy", ploidy])
    return _render_shell_parts(command_parts)
