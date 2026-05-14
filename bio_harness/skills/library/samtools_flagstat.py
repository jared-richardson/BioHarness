"""Deterministic samtools flagstat wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi


def samtools_flagstat(**kwargs: object) -> str:
    """Render a deterministic samtools flagstat command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``input_bam``,
            ``output_txt``, ``threads``, and the optional passthrough
            ``command`` override.

    Returns:
        A shell command string that runs ``samtools flagstat`` and writes the
        summary report.

    Raises:
        ValueError: If required parameters are missing or ``threads`` is
            invalid.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_bam = str(kwargs.get("input_bam", "")).strip()
    output_txt = str(kwargs.get("output_txt", "")).strip()
    missing = [
        name
        for name, value in (
            ("input_bam", input_bam),
            ("output_txt", output_txt),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    threads = int(kwargs.get("threads", 2) or 2)
    if threads < 1:
        raise ValueError("threads must be >= 1")

    samtools_bin = shlex.quote(which_with_pixi("samtools") or "samtools")
    path_prefix = shell_path_prefix("samtools")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""
    output_parent = str(Path(output_txt).expanduser().parent)

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"{samtools_bin} flagstat -@ {threads} {shlex.quote(input_bam)} > {shlex.quote(output_txt)}"
    )
