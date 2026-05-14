"""FASTQ input repair helpers for plan repair."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bio_harness.harness.contract_utils import (
    _collect_planned_output_paths,
    _discover_fastq_pair_map,
    _extract_fastq_sample_tag,
    _resolve_sample_pair,
)
from bio_harness.harness.path_utils import _normalize_plan_path_text, _resolve_existing_input_path
from bio_harness.harness.plan_helpers import _normalize_steps, _renumber_plan_steps


def _repair_missing_fastq_inputs_in_plan(
    plan: dict[str, Any],
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    pair_map = _discover_fastq_pair_map(data_root)
    if not pair_map:
        return plan, {"changed": False, "why": "no_fastq_pairs_discovered"}
    planned_outputs = _collect_planned_output_paths(plan, selected_dir)

    def _is_planned_intermediate_input(raw_path: str, step_index: int) -> bool:
        normalized = _normalize_plan_path_text(raw_path, selected_dir)
        if normalized and normalized in planned_outputs:
            return True
        target = str(raw_path or "").strip()
        if not target:
            return False
        for prior in steps[: max(0, step_index - 1)]:
            if not isinstance(prior, dict):
                continue
            prior_args = prior.get("arguments", {}) if isinstance(prior.get("arguments", {}), dict) else {}
            command = str(prior_args.get("command", "")).strip()
            if command and target in command:
                return True
        return False

    # When there is exactly one FASTQ pair, use it as a universal fallback
    # for steps whose reads_1/reads_2 paths cannot be resolved.
    sole_pair: dict[str, str] | None = None
    if len(pair_map) == 1:
        sole_pair = next(iter(pair_map.values()))

    replacements: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        if not args:
            continue
        if ("reads_1" not in args) and ("reads_2" not in args):
            continue
        current_r1 = str(args.get("reads_1", "")).strip()
        current_r2 = str(args.get("reads_2", "")).strip()
        current_r1_is_planned = bool(current_r1 and _is_planned_intermediate_input(current_r1, idx))
        current_r2_is_planned = bool(current_r2 and _is_planned_intermediate_input(current_r2, idx))
        if current_r1_is_planned and current_r2_is_planned:
            continue
        desired_sample_tag = ""
        for probe_key in ("output_bam", "output_sam", "reads_1", "reads_2", "output_dir"):
            candidate_tag = _extract_fastq_sample_tag(str(args.get(probe_key, "")).strip())
            if not candidate_tag:
                continue
            if not desired_sample_tag:
                desired_sample_tag = candidate_tag
            resolved_candidate_tag, _candidate_pair = _resolve_sample_pair(pair_map, candidate_tag)
            if resolved_candidate_tag:
                desired_sample_tag = candidate_tag
                break
        resolved_sample_tag = ""
        pair: dict[str, str] = {}
        if desired_sample_tag:
            resolved_sample_tag, pair = _resolve_sample_pair(pair_map, desired_sample_tag)
        if not pair:
            r1_raw = current_r1
            r2_raw = current_r2
            r1_exists = bool(r1_raw and _resolve_existing_input_path(r1_raw, selected_dir, data_root))
            r2_exists = bool(r2_raw and _resolve_existing_input_path(r2_raw, selected_dir, data_root))
            if not r1_exists and r1_raw and _is_planned_intermediate_input(r1_raw, idx):
                r1_exists = True
            if not r2_exists and r2_raw and _is_planned_intermediate_input(r2_raw, idx):
                r2_exists = True
            if not (r1_exists and r2_exists) and sole_pair:
                pair = sole_pair
                resolved_sample_tag = "sole_pair_fallback"
        if not pair:
            continue
        updated_args = dict(args)
        step_changed = False
        for arg_key, mate_key in (("reads_1", "r1"), ("reads_2", "r2")):
            raw = str(args.get(arg_key, "")).strip()
            if not raw:
                continue
            if _is_planned_intermediate_input(raw, idx):
                continue
            resolved = str(pair.get(mate_key, "")).strip()
            if not resolved:
                continue
            current_tag = _extract_fastq_sample_tag(raw)
            current_resolved_tag, _ = _resolve_sample_pair(pair_map, current_tag)
            same_sample = bool(current_resolved_tag and current_resolved_tag == resolved_sample_tag)
            if _resolve_existing_input_path(raw, selected_dir, data_root) and same_sample:
                continue
            updated_args[arg_key] = resolved
            replacements.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": str(step.get("tool_name", "")).strip(),
                    "argument": arg_key,
                    "sample_tag": resolved_sample_tag or desired_sample_tag,
                    "from": raw,
                    "to": resolved,
                }
            )
            step_changed = True
        if step_changed:
            step["arguments"] = updated_args

    if not replacements:
        return plan, {"changed": False, "why": "no_missing_fastq_inputs_repaired"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched = _renumber_plan_steps(patched)
    return patched, {
        "changed": True,
        "replacements": replacements,
        "diff_summary": {"replacement_count": len(replacements)},
    }
