"""Deterministic skill-availability helpers for the orchestrator."""
from __future__ import annotations

from typing import Any, Callable


def planner_skill_budget_from_env(raw_value: str) -> int:
    try:
        budget = int(raw_value)
    except Exception:
        budget = 8
    return max(6, min(64, budget))


def tool_binary_available(
    tool_name: str,
    *,
    requirement_checker: Callable[[str], bool],
) -> bool:
    token = str(tool_name or "").strip()
    if not token:
        return True
    low = token.lower()
    if low == "bwa":
        return requirement_checker("bwa") or requirement_checker("bwa-mem2")
    if low in {"star", "star_align"}:
        return requirement_checker("star")
    if low == "subread":
        return requirement_checker("subread")
    if low == "varscan":
        return requirement_checker("varscan")
    if low == "rmats":
        return requirement_checker("rmats")
    if low in {"snpeff", "snpeff_annotate"}:
        return requirement_checker("snpeff")
    return requirement_checker(token)


def skill_tools_available(
    skill: dict[str, Any],
    *,
    tool_available: Callable[[str], bool],
    find_spec: Callable[[str], Any | None],
) -> bool:
    name = str(skill.get("name", "")).strip().lower()
    required = skill.get("tools_required", []) if isinstance(skill.get("tools_required", []), list) else []
    if not required:
        return True
    if name == "deseq2_run":
        has_rscript = tool_available("rscript")
        has_deseq2 = tool_available("deseq2")
        has_pydeseq2 = find_spec("pydeseq2") is not None
        return has_rscript and (has_deseq2 or has_pydeseq2)
    return all(tool_available(str(tool)) for tool in required)


def available_skill_metadata(
    skills: dict[str, Any],
    *,
    skill_available: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    available: list[dict[str, Any]] = []
    for skill in skills.values():
        if not isinstance(skill, dict):
            continue
        if not skill_available(skill):
            continue
        available.append(dict(skill))
    return available
