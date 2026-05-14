"""Planner payload application helpers for the Streamlit UI."""

from __future__ import annotations

from datetime import datetime
from typing import Any, MutableMapping

from bio_harness.core.schemas import safe_parse_planner_result


def apply_planner_payload(
    run: dict[str, Any],
    planner_payload: dict[str, Any],
    *,
    session_state: MutableMapping[str, Any],
    fallback_benchmark_policy: str,
) -> None:
    """Apply a completed planner payload to one UI run."""

    parsed = (
        safe_parse_planner_result(planner_payload)
        if isinstance(planner_payload, dict)
        else None
    )
    payload = parsed.model_dump(mode="json") if parsed is not None else planner_payload
    plan_payload = payload.get("plan", {}) if isinstance(payload.get("plan", {}), dict) else {}
    step_count = len(plan_payload.get("plan", []) if isinstance(plan_payload, dict) else [])
    run["plan_kind"] = "executable"
    run["plan"] = plan_payload
    run["plan_contract"] = payload.get("plan_contract", {})
    run["contract_validation"] = payload.get("contract_validation", {})
    run["user_request"] = str(payload.get("user_request", run.get("user_request", ""))).strip() or run.get(
        "user_request",
        "",
    )
    run["analysis_spec"] = payload.get("analysis_spec", {})
    run["protocol_validation"] = payload.get("protocol_validation", {})
    run["semantic_validation"] = payload.get("semantic_validation", {})
    run["protocol_normalization_meta"] = payload.get("protocol_normalization_meta", {})
    run["benchmark_policy"] = str(
        payload.get("benchmark_policy", run.get("benchmark_policy", fallback_benchmark_policy))
    ).strip() or fallback_benchmark_policy
    run["status"] = "planned"
    run["planner_status"] = "planned"
    run["planner_error"] = ""
    run["planning_finished_at"] = datetime.now().isoformat()
    run["error"] = ""
    run["step_statuses"] = ["pending"] * step_count
    run["next_step_idx"] = 0
    session_state["last_plan"] = plan_payload


def mark_planning_failure(run: dict[str, Any], *, status: str, error: str) -> None:
    """Persist one planning-phase failure on a UI run."""

    run["status"] = status
    run["planner_status"] = status
    run["planner_error"] = error
    run["planning_finished_at"] = datetime.now().isoformat()
    run["error"] = error
