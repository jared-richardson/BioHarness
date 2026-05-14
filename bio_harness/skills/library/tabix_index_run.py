"""Atomic wrapper for one ``tabix`` indexing operation."""

from __future__ import annotations

import shlex

from bio_harness.core.tool_env import which_with_pixi


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


def tabix_index_run(**kwargs: object) -> str:
    """Render one atomic ``tabix`` command.

    Args:
        **kwargs: Wrapper arguments from the harness plan.

    Returns:
        Shell-safe ``tabix`` command.

    Raises:
        ValueError: If one required parameter is missing.
    """

    if "command" in kwargs and str(kwargs.get("command", "")).strip():
        return str(kwargs["command"]).strip()

    input_file = str(kwargs.get("input_file", "")).strip()
    if not input_file:
        raise ValueError("Missing required parameter(s) for template: input_file")

    tabix_bin = which_with_pixi("tabix") or "tabix"
    preset = str(kwargs.get("preset", "vcf") or "vcf").strip() or "vcf"
    command_parts = [str(tabix_bin)]
    if _coerce_bool(kwargs.get("force", True), default=True):
        command_parts.append("-f")
    command_parts.extend(["-p", preset, input_file])
    return " ".join(shlex.quote(str(part)) for part in command_parts if str(part or "").strip())
