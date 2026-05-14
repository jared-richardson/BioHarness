"""Tool availability helpers for contract utilities."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.core.tool_env import (
    pixi_bin_dir as shared_pixi_bin_dir,
    requirement_available,
    which_with_pixi as shared_which_with_pixi,
)
from bio_harness.core.tool_registry import default_tool_registry


def _pixi_bin_dir() -> Path | None:
    """Compatibility wrapper around the shared pixi bin resolver."""

    return shared_pixi_bin_dir()


def _which_with_pixi(name: str) -> str | None:
    """Compatibility wrapper around the shared executable resolver."""

    return shared_which_with_pixi(name)


def _exec_hint_name(tool_name: str) -> str:
    raw = str(tool_name or "").strip()
    if not raw:
        return ""
    try:
        tokens = shlex.split(raw, posix=True)
    except Exception:
        tokens = raw.split()
    token = tokens[0] if tokens else raw
    return Path(token).name or token


def _is_exec_tool_available(tool_name: str) -> bool:
    name = _exec_hint_name(tool_name)
    if not name:
        return True
    return requirement_available(name)


def _missing_exec_tools_for_plan(plan: dict[str, Any]) -> list[str]:
    registry = default_tool_registry()
    missing: set[str] = set()
    for step in plan.get("plan", []) if isinstance(plan, dict) else []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip()
        if not tool_name:
            continue
        exec_hints = registry.exec_hints_for(tool_name)
        if not exec_hints:
            continue
        if not any(_is_exec_tool_available(hint) for hint in exec_hints):
            missing.add(_exec_hint_name(exec_hints[0]))
    return sorted(missing)
