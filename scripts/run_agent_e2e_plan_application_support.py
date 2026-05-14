"""Shared helpers for adopting normalized plans into harness run state.

These helpers keep the plan-validation and preexecution-repair mixins aligned
on how they compare plan payloads, count executable steps, and reset run state
when a new normalized candidate is accepted before execution.
"""

from __future__ import annotations

import json
from typing import Any


def plan_step_count(plan: dict[str, Any] | None) -> int:
    """Return the number of executable steps in one plan payload."""

    plan_dict = plan if isinstance(plan, dict) else {}
    steps = plan_dict.get("plan", [])
    return len(steps) if isinstance(steps, list) else 0


def plan_step_diff_summary(
    *,
    before_step_count: int,
    after_plan: dict[str, Any] | None,
) -> dict[str, int]:
    """Return a stable before/after step-count summary for one candidate."""

    return {
        "before_step_count": int(before_step_count),
        "after_step_count": int(plan_step_count(after_plan)),
    }


def plans_are_distinct(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> bool:
    """Return whether two plan payloads differ after stable JSON rendering."""

    left_text = json.dumps(left if isinstance(left, dict) else {}, sort_keys=True)
    right_text = json.dumps(right if isinstance(right, dict) else {}, sort_keys=True)
    return left_text != right_text


def install_candidate_plan(
    run: dict[str, Any],
    plan: dict[str, Any],
    *,
    reset_step_state: bool,
    mark_planned: bool,
    clear_error: bool,
) -> None:
    """Install one normalized candidate plan into the mutable run state.

    Args:
        run: Mutable harness run-state dict.
        plan: Normalized executable plan payload.
        reset_step_state: Whether to replace ``step_statuses`` with pending
            statuses and reset ``next_step_idx``.
        mark_planned: Whether to force ``run["status"] = "planned"``.
        clear_error: Whether to clear the current run error text.
    """

    run["plan"] = plan
    if reset_step_state:
        run["step_statuses"] = ["pending"] * plan_step_count(plan)
        run["next_step_idx"] = 0
    if mark_planned:
        run["status"] = "planned"
    if clear_error:
        run["error"] = ""


__all__ = [
    "install_candidate_plan",
    "plan_step_count",
    "plan_step_diff_summary",
    "plans_are_distinct",
]
