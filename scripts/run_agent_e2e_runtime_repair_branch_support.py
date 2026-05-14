"""Branch helpers for runtime repair actions.

These helpers keep the runtime-repair action mixin focused on repair policy
ordering while centralizing the low-level mutations used by individual repair
branches such as artifact-aware resume, tool substitution, reference repair,
and checkpoint-aware canonicalization.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bio_harness.core.artifact_inspectors import can_resume_after_failed_step
from scripts.run_agent_e2e_support import (
    infer_resumable_step_index,
)

RunDict = dict[str, Any]
RepairResult = tuple[bool, str, dict[str, Any]]
EmitFn = Callable[..., None]


def maybe_resume_from_existing_artifacts(
    run: RunDict,
    *,
    selected_dir: str,
    recovery_context: dict[str, Any],
    emit: EmitFn,
    quiet: bool,
) -> RepairResult:
    """Resume from the next incomplete step when outputs already exist.

    Args:
        run: Mutable harness run state.
        selected_dir: Selected output directory for the run.
        recovery_context: Failure-classification context with existing artifacts.
        emit: UI/log emission function.
        quiet: Whether user-facing emissions are suppressed.

    Returns:
        A repair result tuple. `repaired` is `True` only when the run state was
        advanced to resume from a later step.
    """

    if recovery_context.get("recovery_strategy") != "skip_step_use_artifact":
        return False, "skip_step_not_applicable", {}
    if not recovery_context.get("existing_artifacts"):
        return False, "skip_step_not_applicable", {}

    plan = run.get("plan") or {}
    resume_idx = infer_resumable_step_index(selected_dir, plan)
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    current_idx = int(run.get("next_step_idx", 0) or 0)
    failed_idx = int(run.get("failed_step_idx", -1))
    if failed_idx >= 0 and not can_resume_after_failed_step(
        Path(selected_dir),
        plan,
        failed_idx,
    ):
        return False, "skip_step_not_applicable", {}
    if resume_idx <= current_idx:
        return False, "skip_step_not_applicable", {}

    step_statuses = list(run.get("step_statuses", []))
    for idx in range(resume_idx):
        if idx < len(step_statuses):
            step_statuses[idx] = "completed"
    run["step_statuses"] = step_statuses
    run["next_step_idx"] = resume_idx
    emit(
        f"[recovery] Artifacts exist for completed steps. Resuming from step {resume_idx + 1}/{len(steps)}.",
        quiet=quiet,
    )
    return True, "skip_step_use_artifact", {
        "why": "skip_to_resumable_step",
        "resume_idx": resume_idx,
        "skipped_artifacts": recovery_context.get("existing_artifacts", []),
        "diff_summary": {"resume_idx": resume_idx, "total_steps": len(steps)},
    }


def maybe_substitute_failed_tool_from_context(
    run: RunDict,
    *,
    recovery_context: dict[str, Any],
    emit: EmitFn,
    quiet: bool,
) -> RepairResult:
    """Substitute the failed tool in-place when the context suggests an equivalent.

    Args:
        run: Mutable harness run state.
        recovery_context: Failure-classification context with substitutions.
        emit: UI/log emission function.
        quiet: Whether user-facing emissions are suppressed.

    Returns:
        A repair result tuple. `repaired` is `True` only when a substitution was
        applied to the current plan.
    """

    if recovery_context.get("recovery_strategy") != "substitute_tool":
        return False, "substitute_tool_not_applicable", {}
    substitutes = list(recovery_context.get("viable_substitutions", []) or [])
    failed_tool = str(recovery_context.get("failed_tool", "") or "").strip()
    if not failed_tool or not substitutes:
        return False, "substitute_tool_not_applicable", {}

    plan = run.get("plan") or {}
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    new_tool = str(substitutes[0]).strip()
    if not new_tool:
        return False, "substitute_tool_not_applicable", {}

    substituted = False
    substituted_step_idx = -1
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip() != failed_tool:
            continue
        step["tool_name"] = new_tool
        substituted = True
        substituted_step_idx = idx
        emit(
            f"[recovery] Substituting {failed_tool} -> {new_tool} in step {idx + 1}.",
            quiet=quiet,
        )
        break
    if not substituted:
        return False, "substitute_tool_not_applicable", {}

    failed_idx = int(run.get("failed_step_idx", 0) or 0)
    step_statuses = list(run.get("step_statuses", []))
    if failed_idx < len(step_statuses):
        step_statuses[failed_idx] = "pending"
    run["step_statuses"] = step_statuses
    run["next_step_idx"] = failed_idx
    return True, "substitute_tool", {
        "why": "tool_substitution",
        "failed_tool": failed_tool,
        "substitute": new_tool,
        "diff_summary": {"substituted_step": failed_idx if failed_idx >= 0 else substituted_step_idx},
    }


def maybe_substitute_missing_tool(
    run: RunDict,
    *,
    missing_tools: list[str],
    tool_equivalence_map: dict[str, list[str]],
    emit: EmitFn,
    quiet: bool,
) -> RepairResult:
    """Swap a missing tool for a configured equivalent when one is available.

    Args:
        run: Mutable harness run state.
        missing_tools: Missing tools detected for the current run.
        tool_equivalence_map: Mapping from unavailable tools to substitutes.
        emit: UI/log emission function.
        quiet: Whether user-facing emissions are suppressed.

    Returns:
        A repair result tuple describing whether substitution succeeded.
    """

    plan = run.get("plan") or {}
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    for missing in missing_tools:
        missing_name = str(missing).strip()
        substitutes = tool_equivalence_map.get(missing_name, [])
        if not substitutes:
            continue
        substitute = str(substitutes[0]).strip()
        if not substitute:
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            if str(step.get("tool_name", "")).strip() != missing_name:
                continue
            step["tool_name"] = substitute
            emit(
                f"[recovery] Missing tool {missing_name} -> substituting {substitute}.",
                quiet=quiet,
            )
            return True, "tool_missing_substitution", {
                "why": "tool_missing_substitution",
                "missing_tool": missing_name,
                "substitute": substitute,
            }
    return False, "tool_missing_substitution_unavailable", {}


def merge_resume_metadata(
    *,
    canonicalization: dict[str, Any],
    resume: dict[str, Any],
) -> dict[str, Any]:
    """Merge canonicalization and resume metadata into one diff summary."""

    return {
        **dict(canonicalization.get("diff_summary", {})),
        "preserved_completed_steps": resume.get("preserved_completed_steps", 0),
        "resume_idx": resume.get("resume_idx", 0),
    }


__all__ = [
    "maybe_resume_from_existing_artifacts",
    "maybe_substitute_failed_tool_from_context",
    "maybe_substitute_missing_tool",
    "merge_resume_metadata",
]
