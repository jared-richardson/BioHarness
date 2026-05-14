"""Deterministic bedtools intersect wrapper."""

from __future__ import annotations

import shlex
from pathlib import Path

from bio_harness.core.tool_env import shell_path_prefix, which_with_pixi

_REPORT_MODE_FLAGS: dict[str, tuple[str, ...]] = {
    "default": (),
    "wa": ("-wa",),
    "wb": ("-wb",),
    "wawb": ("-wa", "-wb"),
    "wao": ("-wao",),
    "loj": ("-loj",),
    "u": ("-u",),
    "v": ("-v",),
    "c": ("-c",),
}


def _is_truthy(value: object) -> bool:
    """Return whether a wrapper argument should be treated as enabled."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def bedtools_intersect(**kwargs: object) -> str:
    """Render a deterministic bedtools intersect command.

    Args:
        **kwargs: Wrapper arguments. Supported keys are ``a_intervals``,
            ``b_intervals``, ``output_file``, ``report_mode``, ``sorted_input``,
            ``min_overlap_fraction``, ``require_reciprocal_overlap``, and the
            optional passthrough ``command`` override.

    Returns:
        A shell command string that runs ``bedtools intersect`` and writes the
        overlap output to the requested file.

    Raises:
        ValueError: If required parameters are missing or overlap settings are
            invalid.
    """
    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    a_intervals = str(kwargs.get("a_intervals", "")).strip()
    b_intervals = str(kwargs.get("b_intervals", "")).strip()
    output_file = str(kwargs.get("output_file", "")).strip()
    missing = [
        name
        for name, value in (
            ("a_intervals", a_intervals),
            ("b_intervals", b_intervals),
            ("output_file", output_file),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required parameter(s) for template: {', '.join(missing)}")

    report_mode = str(kwargs.get("report_mode", "default") or "default").strip().lower()
    report_flags = _REPORT_MODE_FLAGS.get(report_mode)
    if report_flags is None:
        supported = ", ".join(sorted(_REPORT_MODE_FLAGS))
        raise ValueError(f"Unsupported bedtools intersect report_mode '{report_mode}'. Supported values: {supported}")

    extra_flags: list[str] = list(report_flags)
    if _is_truthy(kwargs.get("sorted_input")):
        extra_flags.append("-sorted")

    min_overlap_fraction = kwargs.get("min_overlap_fraction")
    if min_overlap_fraction is not None and str(min_overlap_fraction).strip():
        overlap_fraction = float(min_overlap_fraction)
        if overlap_fraction <= 0 or overlap_fraction > 1:
            raise ValueError("min_overlap_fraction must be > 0 and <= 1")
        extra_flags.extend(["-f", f"{overlap_fraction:g}"])
        if _is_truthy(kwargs.get("require_reciprocal_overlap")):
            extra_flags.append("-r")

    bedtools_bin = shlex.quote(which_with_pixi("bedtools") or "bedtools")
    path_prefix = shell_path_prefix("bedtools")
    path_export = f"export PATH={shlex.quote(path_prefix)}:$PATH; " if path_prefix else ""
    output_parent = str(Path(output_file).expanduser().parent)
    flag_text = (" " + " ".join(extra_flags)) if extra_flags else ""

    return (
        "set -euo pipefail; "
        f"{path_export}"
        f"mkdir -p {shlex.quote(output_parent)}; "
        f"{bedtools_bin} intersect -a {shlex.quote(a_intervals)} -b {shlex.quote(b_intervals)}"
        f"{flag_text} > {shlex.quote(output_file)}"
    )
