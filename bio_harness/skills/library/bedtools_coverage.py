"""Deterministic bedtools coverage wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi


def _is_truthy(value: object) -> bool:
    """Return whether a wrapper argument should be treated as enabled."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def bedtools_coverage(**kwargs: object) -> str:
    """Render a deterministic bedtools coverage command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``a_intervals``,
            ``b_features``, ``output_tsv``, ``counts_only``,
            ``split_alignments``, ``sorted_input``, and the optional
            passthrough ``command`` override.

    Returns:
        A shell command string that runs ``bedtools coverage`` and writes the
        requested summary output.

    Raises:
        ValueError: If required parameters are missing.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    a_intervals = str(kwargs.get("a_intervals", "")).strip()
    b_features = str(kwargs.get("b_features", "")).strip()
    output_tsv = str(kwargs.get("output_tsv", "")).strip()
    missing = [
        name
        for name, value in (
            ("a_intervals", a_intervals),
            ("b_features", b_features),
            ("output_tsv", output_tsv),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    extra_flags: list[str] = []
    if _is_truthy(kwargs.get("counts_only")):
        extra_flags.append("-counts")
    if _is_truthy(kwargs.get("split_alignments")):
        extra_flags.append("-split")
    if _is_truthy(kwargs.get("sorted_input")):
        extra_flags.append("-sorted")

    bedtools_bin = shlex.quote(which_with_pixi("bedtools") or "bedtools")
    path_prefix = shell_path_prefix("bedtools")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""
    output_parent = str(Path(output_tsv).expanduser().parent)
    flag_text = (" " + " ".join(extra_flags)) if extra_flags else ""

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"{bedtools_bin} coverage -a {shlex.quote(a_intervals)} -b {shlex.quote(b_features)}"
        f"{flag_text} > {shlex.quote(output_tsv)}"
    )
