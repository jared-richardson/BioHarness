"""Helpers for recording harness run state in the path graph.

This module keeps path-graph bookkeeping out of the runner scripts so they can
focus on orchestration. The helpers here are deterministic and side-effect
oriented, which makes them easier to test directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _now_utc_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat()


def build_active_preference_profile(
    *,
    stored_preferences: dict[str, Any] | None,
    analysis_preferences: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge persisted graph preferences with analysis-spec preferences."""

    merged = dict(stored_preferences) if isinstance(stored_preferences, dict) else {}
    blacklist = merged.get("tool_blacklist", [])
    if isinstance(blacklist, list) and blacklist:
        existing_discouraged = merged.get("discouraged_tools", [])
        if not isinstance(existing_discouraged, list):
            existing_discouraged = []
        merged["discouraged_tools"] = sorted(
            {str(item).strip() for item in list(existing_discouraged) + list(blacklist) if str(item).strip()}
        )
    whitelist = merged.get("tool_whitelist", [])
    if isinstance(whitelist, list) and whitelist:
        existing_preferred = merged.get("preferred_tools", [])
        if not isinstance(existing_preferred, list):
            existing_preferred = []
        merged["preferred_tools"] = sorted(
            {str(item).strip() for item in list(existing_preferred) + list(whitelist) if str(item).strip()}
        )
    analysis_pref = dict(analysis_preferences) if isinstance(analysis_preferences, dict) else {}
    for key, value in analysis_pref.items():
        if isinstance(value, list):
            base = merged.get(key, [])
            if not isinstance(base, list):
                base = []
            merged[key] = sorted({str(item).strip() for item in list(base) + list(value) if str(item).strip()})
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def infer_selected_path_id(
    *,
    plan: dict[str, Any] | None,
    fallback_selection: dict[str, Any] | None,
    prompt_hash_fallback: str,
) -> str:
    """Infer the selected path identifier for the current plan."""

    selection = fallback_selection if isinstance(fallback_selection, dict) else {}
    selected = str(selection.get("selected_pipeline_id", "")).strip()
    if selected:
        return selected
    nested_sel = selection.get("selection", {})
    if isinstance(nested_sel, dict):
        nested_pipeline = str(nested_sel.get("pipeline_id", "")).strip()
        if nested_pipeline:
            return nested_pipeline

    plan_dict = plan if isinstance(plan, dict) else {}
    canonical = str(plan_dict.get("canonical_template", "")).strip()
    if canonical and not canonical.startswith("custom_"):
        return canonical
    return f"llm_plan::{prompt_hash_fallback}"


def record_graph_selection(
    *,
    path_graph: Any,
    run: dict[str, Any],
    path_id: str,
) -> None:
    """Record one planned path selection in the path graph store."""

    run["selected_path_id"] = path_id
    path_graph.upsert_node(
        node_id=f"path:{path_id}",
        node_type="path",
        label=path_id,
        properties={
            "pipeline_id": path_id,
            "contract_capabilities": list(run.get("plan_contract", {}).get("must_include_capabilities", []))
            if isinstance(run.get("plan_contract", {}), dict)
            else [],
            "required_tools": list(run.get("missing_tools_detected", [])),
        },
    )
    path_graph.record_path_run(
        run_id=f"{run.get('run_uid', '')}:planned",
        path_id=path_id,
        prompt_hash=str(run.get("prompt_hash", "")),
        status="planned",
        started_at=run.get("started_at", _now_utc_iso()),
        finished_at=None,
        artifacts={
            "selection_reason": str(run.get("fallback_selection", {}).get("selection_reason", "")),
            "selection": run.get("fallback_selection", {}),
        },
    )

    selection = run.get("fallback_selection", {}) if isinstance(run.get("fallback_selection", {}), dict) else {}
    candidates = selection.get("candidates", []) if isinstance(selection.get("candidates", []), list) else []
    selected_pipeline = str(selection.get("selected_pipeline_id", "")).strip() or path_id
    if selected_pipeline:
        path_graph.add_annotation(
            target_type="path",
            target_id=selected_pipeline,
            note=(
                "selected_path "
                f"reason={selection.get('selection_reason', '')} "
                f"score={selection.get('selection_score', 0)} "
                f"graph_score={selection.get('selection_graph_score', 0.0)}"
            ).strip(),
            tags=["selection", "selected", str(run.get("prompt_hash", ""))],
        )
    for row in candidates[:10]:
        if not isinstance(row, dict):
            continue
        pipeline_id = str(row.get("pipeline_id", "")).strip()
        if not pipeline_id or pipeline_id == selected_pipeline:
            continue
        path_graph.add_annotation(
            target_type="path",
            target_id=pipeline_id,
            note=(
                "rejected_path "
                f"missing_caps={row.get('missing_caps', [])} "
                f"missing_inputs={row.get('missing_inputs', [])} "
                f"missing_tools={row.get('missing_tools', [])}"
            ),
            tags=["selection", "rejected", str(run.get("prompt_hash", ""))],
        )


def record_graph_outcome(
    *,
    path_graph: Any,
    run: dict[str, Any],
    persist_preference_updates: bool,
    path_graph_user_key: str,
    path_graph_scope: str,
) -> None:
    """Record the final execution outcome for the selected path."""

    path_id = str(run.get("selected_path_id", "")).strip()
    if not path_id:
        return
    finished_at = str(run.get("finished_at", "")).strip() or _now_utc_iso()
    status_norm = str(run.get("status", "")).strip().lower() or "unknown"
    success = status_norm == "completed"
    missing_tools = list(run.get("missing_tools_detected", []))
    missing_refs = list(run.get("missing_reference_detected", []))
    missing_groups = list(run.get("missing_sample_groups", []))
    error_present = bool(str(run.get("error", "")).strip())
    penalties = 0.0
    penalties += 0.20 if missing_tools else 0.0
    penalties += 0.15 if missing_refs else 0.0
    penalties += 0.10 if missing_groups else 0.0
    penalties += 0.20 if error_present else 0.0
    quality_score = max(0.0, min(1.0, (1.0 if success else 0.35) - penalties))
    reliability_score = max(
        0.0,
        min(
            1.0,
            (0.7 if success else 0.2) + (0.2 if not missing_tools else 0.0) + (0.1 if not error_present else 0.0),
        ),
    )
    path_graph.record_path_run(
        run_id=str(run.get("run_uid", "")),
        path_id=path_id,
        prompt_hash=str(run.get("prompt_hash", "")),
        status=status_norm,
        started_at=str(run.get("started_at", "")).strip() or _now_utc_iso(),
        finished_at=finished_at,
        artifacts={
            "error": str(run.get("error", "")),
            "missing_tools_detected": missing_tools,
            "missing_reference_detected": missing_refs,
            "missing_sample_groups": missing_groups,
            "quality_score": round(float(quality_score), 6),
            "reliability_score": round(float(reliability_score), 6),
            "fallback_selection": run.get("fallback_selection", {}),
        },
    )
    if persist_preference_updates and status_norm == "completed":
        contract = run.get("plan_contract", {}) if isinstance(run.get("plan_contract", {}), dict) else {}
        requested_caps = contract.get("must_include_capabilities", []) if isinstance(contract.get("must_include_capabilities", []), list) else []
        path_graph.persist_success_preferences(
            user_key=str(path_graph_user_key),
            scope=str(path_graph_scope),
            path_id=path_id,
            requested_capabilities=[str(value) for value in requested_caps if str(value).strip()],
        )
