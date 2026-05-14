"""Shared helpers for initial plan acquisition before validation phases.

These helpers keep the plan-validation mixin focused on sequencing while
centralizing planner bootstrap state, planner failure recovery, and the
shape checks applied to the initial plan object before normalization.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable

from scripts.run_agent_e2e_validation_phase_support import (
    append_repair_applied_event,
)

PlanDict = dict[str, Any]
AppendEventFn = Callable[..., None]
EmitFn = Callable[..., None]
BuildFallbackFn = Callable[[str], tuple[PlanDict | None, str, PlanDict]]
GeneratePlanFn = Callable[[PlanDict], tuple[PlanDict, PlanDict]]
FailureSignatureFn = Callable[[str], None]
LoopbackCheckFn = Callable[[Exception], bool]


@dataclass(frozen=True, slots=True)
class InitialPlanAcquisition:
    """Capture the initial plan plus the planner strategy that produced it."""

    plan: PlanDict
    planner_strategy_used: str


def initialize_plan_preparation_state(
    run: PlanDict,
    *,
    catalog_summary: list[PlanDict],
) -> None:
    """Reset run state that is always refreshed before plan preparation."""

    run["fallback_catalog_summary"] = catalog_summary
    run["fallback_catalog_size"] = len(catalog_summary)
    run["planning_attempts"] = []
    run["planner_strategy_used"] = ""


def acquire_initial_plan(
    *,
    cfg: Any,
    run: PlanDict,
    contract: PlanDict,
    strict_llm_planning: bool,
    generate_plan_with_supervision: GeneratePlanFn,
    build_contract_template_repair: BuildFallbackFn,
    is_local_model_loopback_blocked: LoopbackCheckFn,
    note_failure_signature: FailureSignatureFn,
    append_event: AppendEventFn,
    emit: EmitFn,
    biollm: Any,
) -> InitialPlanAcquisition:
    """Acquire the initial plan from disk, the planner, or deterministic fallback."""

    if cfg.plan_path:
        plan = json.loads(Path(cfg.plan_path).read_text(encoding="utf-8"))
        return InitialPlanAcquisition(plan=plan, planner_strategy_used="")

    try:
        plan, planner_meta = generate_plan_with_supervision(contract)
        strategy = str(planner_meta.get("strategy", "") or "")
        return InitialPlanAcquisition(
            plan=plan,
            planner_strategy_used=strategy,
        )
    except Exception as exc:
        err_text = str(exc)
        if "timed out" in err_text.lower():
            run["planner_timeout_detected"] = True
            note_failure_signature("planner_timeout")
        if is_local_model_loopback_blocked(exc):
            run["local_model_loopback_blocked_detected"] = True
            note_failure_signature("local_model_loopback_blocked")
            append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="LOCAL_MODEL_LOOPBACK_BLOCKED",
                severity="error",
                payload={
                    "backend_name": str(getattr(biollm, "backend_name", "") or ""),
                    "backend_label": str(getattr(biollm, "backend_label", "") or ""),
                    "host": str(getattr(biollm, "host", "") or ""),
                    "error": err_text,
                },
            )
            raise RuntimeError(
                "Local model loopback access is blocked for the selected backend. "
                "Grant localhost network permission or run the harness outside the sandbox."
            ) from exc
        if strict_llm_planning:
            raise RuntimeError(
                "Strict LLM planning is enabled and planner did not produce a usable plan: "
                + err_text
            ) from exc

        fallback_plan, fallback_action, fallback_details = build_contract_template_repair(
            "runtime_step_failure"
        )
        if fallback_plan is None:
            raise
        run["fallback_selection"] = fallback_details
        append_repair_applied_event(
            append_event=append_event,
            run=run,
            failure_class="runtime_step_failure",
            action=f"preplanning_{fallback_action}",
            details={
                "planner_error": err_text,
                "planning_attempts": run.get("planning_attempts", []),
                **fallback_details,
            },
        )
        emit(
            f"Planner unavailable; selected deterministic fallback template ({fallback_action}).",
            quiet=cfg.quiet,
        )
        return InitialPlanAcquisition(plan=fallback_plan, planner_strategy_used="")


def validate_initial_plan_shape(plan: Any) -> None:
    """Validate the basic structure of the initial planner output."""

    if not isinstance(plan, dict):
        raise ValueError("Planner did not return a plan dictionary.")
    if "plan" not in plan:
        raise ValueError("Planner output missing `plan` key.")


__all__ = [
    "InitialPlanAcquisition",
    "acquire_initial_plan",
    "initialize_plan_preparation_state",
    "validate_initial_plan_shape",
]
