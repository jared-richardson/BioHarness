"""Deterministic Sniffles structural-variant calling wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi
from bio_harness.core.tool_launchers import tool_launcher_command


def sniffles_sv_call(**kwargs: object) -> str:
    """Render a deterministic Sniffles structural-variant calling command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``input_bam``,
            ``reference_fasta``, ``output_vcf``, ``threads``, ``sample_id``,
            ``min_support``, ``min_sv_length``, and the optional passthrough
            ``command`` override.

    Returns:
        A shell command string that indexes the alignment if needed and runs
        Sniffles against the provided reference.

    Raises:
        ValueError: If required arguments are missing or numeric parameters are
            invalid.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_bam = str(kwargs.get("input_bam", "")).strip()
    reference_fasta = str(kwargs.get("reference_fasta", "")).strip()
    output_vcf = str(kwargs.get("output_vcf", "")).strip()
    missing = [
        name
        for name, value in (
            ("input_bam", input_bam),
            ("reference_fasta", reference_fasta),
            ("output_vcf", output_vcf),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    threads = int(kwargs.get("threads", 4) or 4)
    if threads < 1:
        raise ValueError("threads must be >= 1")

    min_support = kwargs.get("min_support")
    if min_support is not None and str(min_support).strip():
        min_support = int(min_support)
        if min_support < 1:
            raise ValueError("min_support must be >= 1")

    min_sv_length = kwargs.get("min_sv_length")
    if min_sv_length is not None and str(min_sv_length).strip():
        min_sv_length = int(min_sv_length)
        if min_sv_length < 1:
            raise ValueError("min_sv_length must be >= 1")

    sample_id = str(kwargs.get("sample_id", "")).strip()

    launcher_command = tool_launcher_command("sniffles")
    sniffles_command = launcher_command or shlex.quote(which_with_pixi("sniffles") or "sniffles")
    samtools_command = shlex.quote(which_with_pixi("samtools") or "samtools")
    output_parent = str(Path(output_vcf).expanduser().parent)
    path_prefix = shell_path_prefix("sniffles", "samtools")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""

    extra_args: list[str] = []
    if sample_id:
        extra_args.extend(["--sample-id", shlex.quote(sample_id)])
    if min_support is not None:
        extra_args.extend(["--minsupport", str(min_support)])
    if min_sv_length is not None:
        extra_args.extend(["--minsvlen", str(min_sv_length)])
    extra_text = (" " + " ".join(extra_args)) if extra_args else ""

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"if [ ! -f {shlex.quote(input_bam)}.bai ] && [ ! -f {shlex.quote(input_bam)}.csi ] && [ ! -f {shlex.quote(input_bam)}.crai ]; then "
        f"{samtools_command} index {shlex.quote(input_bam)}; "
        "fi; "
        f"{sniffles_command} --input {shlex.quote(input_bam)} "
        f"--vcf {shlex.quote(output_vcf)} "
        f"--reference {shlex.quote(reference_fasta)} "
        f"--threads {threads}{extra_text}"
    )
