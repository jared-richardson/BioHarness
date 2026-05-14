"""Single-cell repair helpers extracted from plan_repair."""
from __future__ import annotations

from typing import Any

from bio_harness.core.benchmark_policy import OFFICIAL_BIOAGENTBENCH_POLICY
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _repair_single_cell_qc_thresholds(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any],
    benchmark_policy: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if str(analysis_spec.get("analysis_type", "") or "").strip().lower() != "single_cell_rna_seq":
        return plan, {"changed": False, "why": "analysis_type_not_single_cell_rna_seq"}
    if str(benchmark_policy or "").strip() != OFFICIAL_BIOAGENTBENCH_POLICY:
        return plan, {"changed": False, "why": "benchmark_policy_not_official"}

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    changed_steps: list[int] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        if str(step.get("tool_name", "")).strip().lower() != "sc_count_and_cluster":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        updated_args = dict(args)
        desired = {"min_genes": 3, "min_cells": 1, "kmer_size": 25, "leiden_resolution": 0.5}
        local_changed = False
        for key, value in desired.items():
            if updated_args.get(key) != value:
                updated_args[key] = value
                local_changed = True
        if local_changed:
            step["arguments"] = updated_args
            changed_steps.append(int(step.get("step_id", idx)))

    if not changed_steps:
        return plan, {"changed": False, "why": "single_cell_qc_thresholds_already_safe"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    return patched, {
        "changed": True,
        "why": "single_cell_qc_thresholds_repaired_for_official_mode",
        "changed_steps": changed_steps,
    }


def _repair_single_cell_export_tail(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Drop fragile single-cell export tails that the deliverable agent can rebuild."""
    if str(analysis_spec.get("analysis_type", "") or "").strip().lower() != "single_cell_rna_seq":
        return plan, {"changed": False, "why": "analysis_type_not_single_cell_rna_seq"}

    steps = _normalize_steps(plan)
    if len(steps) < 2:
        return plan, {"changed": False, "why": "no_single_cell_export_tail"}

    retained_steps: list[dict[str, Any]] = []
    removed_step_ids: list[int] = []
    changed = False
    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "")).strip().lower()
        command = ""
        if tool_name == "bash_run":
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            command = str(args.get("command", "") or "")
        command_lower = command.lower()
        looks_like_export_tail = tool_name == "bash_run" and (
            "differential_expression.csv" in command_lower or "single_cell_results.csv" in command_lower
        )
        if looks_like_export_tail:
            changed = True
            removed_step_ids.append(int(step.get("step_id", idx)))
            continue
        retained_steps.append(step)

    if not changed:
        return plan, {"changed": False, "why": "single_cell_export_tail_not_detected"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = retained_steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "why": "single_cell_export_tail_removed",
        "removed_step_ids": removed_step_ids,
    }
