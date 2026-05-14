"""Helpers for evaluating runtime replanning candidates.

These helpers keep the runtime-repair mixin focused on recovery strategy
selection while centralizing the repeated candidate validation, pruning, and
resume-installation logic for runtime replanning attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from scripts.run_agent_e2e_support import (
    MAX_REPLAN_STEP_DELTA,
    _is_actionable_executable_plan,
)

PlanDict = dict[str, Any]
RunDict = dict[str, Any]
CanonicalizePlanFn = Callable[[PlanDict, str], tuple[PlanDict, PlanDict]]
PrunePlanFn = Callable[..., tuple[PlanDict, PlanDict]]
MissingScriptsFn = Callable[[PlanDict, str], list[str]]
AssessContractFn = Callable[[PlanDict], PlanDict]
ApplyResumeFn = Callable[[RunDict, PlanDict], PlanDict]


@dataclass(frozen=True, slots=True)
class RuntimeReplanEvaluation:
    """The result of evaluating one runtime replan candidate."""

    applied: bool
    attempt_row: PlanDict
    details: PlanDict
    guard: PlanDict
    validation: PlanDict


def evaluate_runtime_replan_candidate(
    *,
    run: RunDict,
    candidate: Any,
    failure_class: str,
    focus_mode: str,
    attempt_num: int,
    strategy: str,
    before_steps: int,
    data_root: str,
    selected_dir: str,
    canonicalize_plan: CanonicalizePlanFn,
    prune_candidate: PrunePlanFn,
    missing_scripts_for_plan: MissingScriptsFn,
    assess_contract: AssessContractFn,
    apply_repaired_plan_with_resume: ApplyResumeFn,
) -> RuntimeReplanEvaluation:
    """Evaluate, and when valid apply, one runtime replanning candidate."""

    if not _is_actionable_executable_plan(candidate):
        return RuntimeReplanEvaluation(
            applied=False,
            attempt_row={
                "attempt": attempt_num,
                "focus_mode": focus_mode,
                "strategy": strategy,
                "status": "invalid",
                "reason": "model returned empty/non-actionable plan",
            },
            details={},
            guard={},
            validation={},
        )

    canonical_plan, canonical_meta = canonicalize_plan(candidate, data_root=data_root)
    if canonical_meta.get("changed", False):
        candidate = canonical_plan
    candidate, guard = prune_candidate(
        candidate,
        failure_class=failure_class,
        before_steps=before_steps,
    )

    if guard.get("step_growth", 0) > MAX_REPLAN_STEP_DELTA:
        return RuntimeReplanEvaluation(
            applied=False,
            attempt_row={
                "attempt": attempt_num,
                "focus_mode": focus_mode,
                "strategy": strategy,
                "status": "rejected",
                "reason": "step_growth_exceeded",
                "guard": guard,
            },
            details={},
            guard=guard,
            validation={},
        )
    if guard.get("heavy_reintroduced", False):
        return RuntimeReplanEvaluation(
            applied=False,
            attempt_row={
                "attempt": attempt_num,
                "focus_mode": focus_mode,
                "strategy": strategy,
                "status": "rejected",
                "reason": "heavy_steps_reintroduced",
                "guard": guard,
            },
            details={},
            guard=guard,
            validation={},
        )
    if not _is_actionable_executable_plan(candidate):
        return RuntimeReplanEvaluation(
            applied=False,
            attempt_row={
                "attempt": attempt_num,
                "focus_mode": focus_mode,
                "strategy": strategy,
                "status": "invalid",
                "reason": "plan became non-actionable after prune",
                "guard": guard,
            },
            details={},
            guard=guard,
            validation={},
        )

    missing_scripts = missing_scripts_for_plan(candidate, selected_dir)
    if missing_scripts:
        return RuntimeReplanEvaluation(
            applied=False,
            attempt_row={
                "attempt": attempt_num,
                "focus_mode": focus_mode,
                "strategy": strategy,
                "status": "rejected",
                "reason": "missing_local_scripts",
                "missing_scripts": missing_scripts,
                "guard": guard,
            },
            details={},
            guard=guard,
            validation={},
        )

    validation = assess_contract(candidate)
    if not validation.get("passed", False):
        return RuntimeReplanEvaluation(
            applied=False,
            attempt_row={
                "attempt": attempt_num,
                "focus_mode": focus_mode,
                "strategy": strategy,
                "status": "rejected",
                "reason": "contract_validation_failed",
                "validation": validation,
            },
            details={},
            guard=guard,
            validation=validation,
        )

    auto_steps = candidate.get("plan", []) if isinstance(candidate, dict) else []
    resume_meta = apply_repaired_plan_with_resume(run, candidate)
    return RuntimeReplanEvaluation(
        applied=True,
        attempt_row={
            "attempt": attempt_num,
            "focus_mode": focus_mode,
            "strategy": strategy,
            "status": "applied",
        },
        details={
            "why": "Replanned with failure context and contract constraints.",
            "failure_class": failure_class,
            "repair_focus_mode": focus_mode,
            "contract_validation": validation,
            "replan_guard": guard,
            "diff_summary": {
                "before_step_count": before_steps,
                "after_step_count": len(auto_steps),
                "preserved_completed_steps": resume_meta.get("preserved_completed_steps", 0),
                "resume_idx": resume_meta.get("resume_idx", 0),
            },
            "resume": resume_meta,
        },
        guard=guard,
        validation=validation,
    )


__all__ = [
    "RuntimeReplanEvaluation",
    "evaluate_runtime_replan_candidate",
]
