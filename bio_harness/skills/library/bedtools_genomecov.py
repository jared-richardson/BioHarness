"""Deterministic bedtools genomecov wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi

_REPORT_MODE_FLAGS: dict[str, tuple[str, ...]] = {
    "histogram": (),
    "bedgraph": ("-bg",),
    "bedgraph_all": ("-bga",),
    "per_base": ("-d",),
}


def _is_truthy(value: object) -> bool:
    """Return whether a wrapper argument should be treated as enabled."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def bedtools_genomecov(**kwargs: object) -> str:
    """Render a deterministic bedtools genomecov command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``input_bam``,
            ``input_bed``, ``genome_file``, ``output_file``, ``report_mode``,
            ``split_intervals``, ``strand``, and the optional passthrough
            ``command`` override.

    Returns:
        A shell command string that runs ``bedtools genomecov`` and writes the
        requested coverage profile output.

    Raises:
        ValueError: If required parameters are missing or inconsistent.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_bam = str(kwargs.get("input_bam", "")).strip()
    input_bed = str(kwargs.get("input_bed", "")).strip()
    genome_file = str(kwargs.get("genome_file", "")).strip()
    output_file = str(kwargs.get("output_file", "")).strip()
    if not output_file:
        raise ValueError("Missing required parameter(s) for template: output_file")
    if bool(input_bam) == bool(input_bed):
        raise ValueError("Provide exactly one of input_bam or input_bed")
    if input_bed and not genome_file:
        raise ValueError("genome_file is required when input_bed is provided")

    report_mode = str(kwargs.get("report_mode", "bedgraph") or "bedgraph").strip().lower()
    report_flags = _REPORT_MODE_FLAGS.get(report_mode)
    if report_flags is None:
        supported = ", ".join(sorted(_REPORT_MODE_FLAGS))
        raise ValueError(f"Unsupported bedtools genomecov report_mode '{report_mode}'. Supported values: {supported}")

    extra_flags: list[str] = list(report_flags)
    if _is_truthy(kwargs.get("split_intervals")):
        extra_flags.append("-split")
    strand = str(kwargs.get("strand", "")).strip()
    if strand:
        if strand not in {"+", "-"}:
            raise ValueError("strand must be '+' or '-' when provided")
        extra_flags.extend(["-strand", strand])

    bedtools_bin = shlex.quote(which_with_pixi("bedtools") or "bedtools")
    path_prefix = shell_path_prefix("bedtools")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""
    output_parent = str(Path(output_file).expanduser().parent)
    flag_text = (" " + " ".join(extra_flags)) if extra_flags else ""

    input_clause = ""
    if input_bam:
        input_clause = f"-ibam {shlex.quote(input_bam)}"
    else:
        input_clause = f"-i {shlex.quote(input_bed)} -g {shlex.quote(genome_file)}"

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"{bedtools_bin} genomecov {input_clause}{flag_text} > {shlex.quote(output_file)}"
    )
