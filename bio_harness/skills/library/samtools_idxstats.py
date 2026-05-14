"""Deterministic samtools idxstats wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi


def samtools_idxstats(**kwargs: object) -> str:
    """Render a deterministic samtools idxstats command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``input_bam``,
            ``output_tsv``, and the optional passthrough ``command`` override.

    Returns:
        A shell command string that runs ``samtools idxstats`` and writes the
        per-reference alignment summary.

    Raises:
        ValueError: If required parameters are missing.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_bam = str(kwargs.get("input_bam", "")).strip()
    output_tsv = str(kwargs.get("output_tsv", "")).strip()
    missing = [
        name
        for name, value in (
            ("input_bam", input_bam),
            ("output_tsv", output_tsv),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    samtools_bin = shlex.quote(which_with_pixi("samtools") or "samtools")
    path_prefix = shell_path_prefix("samtools")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""
    output_parent = str(Path(output_tsv).expanduser().parent)

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"{samtools_bin} idxstats {shlex.quote(input_bam)} > {shlex.quote(output_tsv)}"
    )
