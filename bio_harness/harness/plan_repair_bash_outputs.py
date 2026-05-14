"""Bash output-directory repair helpers for plan repair."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from bio_harness.harness.path_utils import _redirection_parent_dirs
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _repair_bash_redirection_output_dirs(
    plan: dict[str, Any],
    selected_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue
        parents = _redirection_parent_dirs(command, selected_dir)
        if not parents:
            continue
        mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(p) for p in parents)
        updated_command = f"{mkdir_cmd} && {command}"
        step["arguments"] = {**args, "command": updated_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "parents": parents,
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_bash_redirection_repairs"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }


def _repair_bash_tool_output_parent_dirs(
    plan: dict[str, Any],
    selected_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip() != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "")).strip()
        if not command:
            continue
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        if not tokens:
            continue
        executable = Path(tokens[0]).name.lower()
        output_flags: set[str] = set()
        if executable == "fastp":
            output_flags = {"-o", "-O", "-j", "-h", "--out1", "--out2", "--json", "--html"}
        elif executable == "kraken2":
            output_flags = {"--report", "--output", "--classified-out", "--unclassified-out"}
        if not output_flags:
            continue

        parents: list[str] = []
        seen: set[str] = set()
        for pos, token in enumerate(tokens[:-1]):
            if token not in output_flags:
                continue
            candidate = str(tokens[pos + 1]).strip().strip("'\"")
            if not candidate:
                continue
            parent = str(Path(candidate).expanduser().parent.resolve(strict=False))
            if not parent or parent in seen:
                continue
            seen.add(parent)
            parents.append(parent)
        if not parents:
            continue
        mkdir_cmd = "mkdir -p " + " ".join(shlex.quote(parent) for parent in parents)
        updated_command = f"{mkdir_cmd} && {command}"
        step["arguments"] = {**args, "command": updated_command}
        replacements.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "parents": parents,
                "executable": executable,
            }
        )

    if not replacements:
        return plan, {"changed": False, "why": "no_bash_tool_output_parent_dir_repairs"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }
