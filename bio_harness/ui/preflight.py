"""Preflight helpers for chat-driven UI execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any


_GROUP_COMPARISON_TOOLS = frozenset(
    {
        "deseq2_run",
        "edger_run",
        "limma_voom_run",
        "dexseq_run",
        "rmats_run",
        "majiq_run",
        "spladder_run",
        "whippet_run",
    }
)


def plan_requires_sample_groups(plan: dict[str, Any]) -> bool:
    """Return whether one plan depends on grouped case/control-style inputs.

    Args:
        plan: Execution plan under preflight validation.

    Returns:
        ``True`` when the workflow semantics require grouped FASTQ labels.
    """
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list):
        return False

    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip().lower()
        if tool_name in _GROUP_COMPARISON_TOOLS:
            return True
        if tool_name != "bash_run":
            continue
        arguments = step.get("arguments", {}) if isinstance(step.get("arguments"), dict) else {}
        command = str(arguments.get("command", "")).strip().lower()
        if not command:
            continue
        if "select_sample_r1.sh" in command:
            return True
        if " control" in command or " treatment" in command:
            return True
        if any(token in command for token in ("deseq2", "edger", "limma", "dexseq", "rmats", "majiq")):
            return True

    return False


def data_root_has_sample_metadata(data_root: str) -> bool:
    """Return whether one data root provides deterministic sample metadata.

    Args:
        data_root: Candidate data root for one UI execution.

    Returns:
        ``True`` when the directory includes a recognized sample metadata table
        that can define conditions without relying on FASTQ filename tags.
    """
    root = Path(str(data_root or "")).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        return False
    return any((root / name).is_file() for name in ("sample_metadata.tsv", "sample_metadata.csv"))


def plan_requires_filename_group_tags(plan: dict[str, Any], *, data_root: str) -> bool:
    """Return whether grouped FASTQ filename tags are still required.

    Args:
        plan: Execution plan under preflight validation.
        data_root: Candidate UI data root for the run.

    Returns:
        ``True`` when the plan requires grouped inputs and the data root does
        not provide deterministic sample metadata to satisfy that grouping.
    """
    return plan_requires_sample_groups(plan) and not data_root_has_sample_metadata(data_root)
