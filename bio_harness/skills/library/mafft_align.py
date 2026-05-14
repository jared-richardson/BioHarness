"""Deterministic MAFFT multiple-sequence alignment wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi
from bio_harness.core.tool_launchers import tool_launcher_command

_STRATEGY_FLAGS: dict[str, str] = {
    "auto": "--auto",
    "genafpair": "--genafpair",
    "globalpair": "--globalpair",
    "localpair": "--localpair",
}


def mafft_align(**kwargs: object) -> str:
    """Render a deterministic MAFFT multiple-sequence alignment command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``input_fasta``,
            ``output_fasta``, ``threads``, ``strategy_mode``, and the optional
            passthrough ``command`` override.

    Returns:
        A shell command string that runs MAFFT and writes the aligned FASTA.

    Raises:
        ValueError: If required arguments are missing or ``strategy_mode`` is
            unsupported.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_fasta = str(kwargs.get("input_fasta", "")).strip()
    output_fasta = str(kwargs.get("output_fasta", "")).strip()
    missing = [
        name
        for name, value in (
            ("input_fasta", input_fasta),
            ("output_fasta", output_fasta),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    threads = int(kwargs.get("threads", 2) or 2)
    if threads < 1:
        raise ValueError("threads must be >= 1")

    strategy_mode = str(kwargs.get("strategy_mode", "auto") or "auto").strip().lower()
    strategy_flag = _STRATEGY_FLAGS.get(strategy_mode)
    if not strategy_flag:
        supported = ", ".join(sorted(_STRATEGY_FLAGS))
        raise ValueError(f"Unsupported MAFFT strategy_mode '{strategy_mode}'. Supported values: {supported}")

    launcher_command = tool_launcher_command("mafft")
    mafft_command = launcher_command or shlex.quote(which_with_pixi("mafft") or "mafft")
    output_parent = str(Path(output_fasta).expanduser().parent)
    path_prefix = shell_path_prefix("mafft")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"{mafft_command} {strategy_flag} --thread {threads} "
        f"{shlex.quote(input_fasta)} > {shlex.quote(output_fasta)}"
    )
