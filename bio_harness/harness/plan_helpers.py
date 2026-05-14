from __future__ import annotations
# ruff: noqa: F401

from typing import Any

from bio_harness.core.request_output_intent import is_file_like_output_path
from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.harness.config import BAM_LIST_TOKEN_RE, PROVENANCE_CRITICAL_TOOLS
from bio_harness.harness.plan_helpers_support import (
    _apply_repaired_plan_with_resume,
    _is_actionable_executable_plan,
    _is_probe_only_bash,
    _missing_local_scripts_for_plan,
    _normalize_steps,
    _plan_completed_prefix_len,
    _plan_summary_for_repair_prompt,
    _renumber_plan_steps,
    _step_fingerprint,
)
from bio_harness.harness.plan_semantic_guards import (
    _assess_plan_semantic_guards,
    _extract_csv_output_from_command,
)

def _repair_scope_for_run(run: dict[str, Any]) -> dict[str, Any]:
    plan = run.get("plan", {}) if isinstance(run.get("plan", {}), dict) else {}
    steps = _normalize_steps(plan)
    failed_step_number = _first_failed_step_number(
        run.get("step_statuses", []),
        fallback_next_idx=int(run.get("next_step_idx", 0) or 0),
    )
    failed_tool = _failed_tool_name(plan, failed_step_number)
    failed_idx = failed_step_number - 1
    failed_step = steps[failed_idx] if 0 <= failed_idx < len(steps) else {}
    args = failed_step.get("arguments", {}) if isinstance(failed_step.get("arguments", {}), dict) else {}
    failed_command = str(args.get("command", "")).strip()
    failed_outputs_csv = bool(_extract_csv_output_from_command(failed_command))
    completed_prefix = _plan_completed_prefix_len(run)
    completed_tools = {
        str(step.get("tool_name", "")).strip().lower()
        for step in steps[:completed_prefix]
        if isinstance(step, dict) and str(step.get("tool_name", "")).strip()
    }
    provenance_locked = bool(completed_tools.intersection(PROVENANCE_CRITICAL_TOOLS))
    total_steps = len(steps)
    tail_steps = max(0, total_steps - max(0, failed_step_number - 1))

    scope = "unknown"
    if failed_step_number > 0:
        if failed_step_number == total_steps:
            scope = "step_local"
        elif tail_steps <= 2 and failed_tool == "bash_run":
            scope = "tail_local"
        elif completed_prefix > 0 and failed_step_number > completed_prefix:
            scope = "subgraph_local"
        else:
            scope = "full_replan"

    return {
        "scope": scope,
        "failed_step_number": failed_step_number,
        "failed_tool": failed_tool,
        "failed_outputs_csv": failed_outputs_csv,
        "failed_command": failed_command,
        "completed_prefix": completed_prefix,
        "completed_tools": sorted(completed_tools),
        "provenance_locked": provenance_locked,
        "tail_steps": tail_steps,
        "total_steps": total_steps,
    }


def _normalize_capability_list(values: list[Any]) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        token = str(value or "").strip()
        if token:
            normalized.add(token)
    return sorted(normalized)


_UNDECLARED_OUTPUT_ARGUMENT_HINTS = (
    "output_file",
    "output_csv",
    "output_tsv",
    "final_csv",
    "final_tsv",
    "final_json",
    "final_output",
)


def _relocate_undocumented_output_arguments_to_final_deliverables(
    plan: dict[str, Any],
    *,
    required_deliverables: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move invented final-output args out of tool arguments.

    The planner occasionally invents undeclared wrapper parameters such as
    ``output_file`` or ``final_csv`` when the user asks for a final published
    artifact. Those paths belong to run-level deliverables, not to the
    executable tool contract.
    """

    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "reason": "plan_missing"}

    registry = default_tool_registry()
    expected_deliverables = [
        str(path).strip()
        for path in (required_deliverables or [])
        if str(path).strip()
    ]
    expected_basenames = {
        path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        for path in expected_deliverables
        if path
    }
    final_deliverables = [
        str(path).strip()
        for path in (plan.get("final_deliverables", []) if isinstance(plan, dict) else [])
        if str(path).strip()
    ]
    changed_rows: list[dict[str, Any]] = []

    for step in steps:
        tool_name = str(step.get("tool_name", "")).strip()
        if not tool_name or tool_name == "bash_run":
            continue
        declared = set(registry.parameter_schema_for(tool_name))
        args = dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        relocated_keys: list[str] = []
        for key in list(args):
            arg_key = str(key).strip()
            if not arg_key or arg_key in declared:
                continue
            lowered = arg_key.lower()
            if lowered not in _UNDECLARED_OUTPUT_ARGUMENT_HINTS and not lowered.startswith("final_"):
                continue
            value = str(args.get(key, "") or "").strip()
            if not value or not is_file_like_output_path(value):
                continue
            basename = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if expected_deliverables and value not in expected_deliverables and basename not in expected_basenames:
                continue
            final_deliverables.append(value)
            args.pop(key, None)
            relocated_keys.append(arg_key)
        if relocated_keys:
            step["arguments"] = args
            changed_rows.append(
                {
                    "step_id": int(step.get("step_id", 0) or 0),
                    "tool_name": tool_name,
                    "relocated_keys": relocated_keys,
                }
            )

    deduped_deliverables: list[str] = []
    seen: set[str] = set()
    for path in final_deliverables:
        if not path or path in seen:
            continue
        seen.add(path)
        deduped_deliverables.append(path)
    if not changed_rows:
        return plan, {"changed": False, "reason": "no_undocumented_output_arguments"}

    patched = dict(plan) if isinstance(plan, dict) else {}
    patched["plan"] = steps
    patched["final_deliverables"] = deduped_deliverables
    return _renumber_plan_steps(patched), {
        "changed": True,
        "reason": "relocated_undocumented_output_arguments",
        "changed_steps": changed_rows,
        "diff_summary": {"changed_steps": len(changed_rows)},
    }


def _extract_selection_pipeline_id(selection: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
    sel = selection.get("selection", {}) if isinstance(selection.get("selection", {}), dict) else {}
    pipeline_id = str(sel.get("pipeline_id", "")).strip()
    if pipeline_id:
        return pipeline_id
    if isinstance(plan, dict):
        canonical = str(plan.get("canonical_template", "")).strip()
        if canonical:
            return canonical
    selected_template = selection.get("selected_template", {}) if isinstance(selection.get("selected_template", {}), dict) else {}
    return str(selected_template.get("pipeline_id", "")).strip()


def _compose_plan_segments(
    *,
    base_plan: dict[str, Any],
    segment_plans: list[dict[str, Any]],
    segment_ids: list[str],
) -> dict[str, Any]:
    composed = dict(base_plan)
    merged_steps: list[dict[str, Any]] = []
    for idx, segment in enumerate(segment_plans, start=1):
        segment_id = str(segment_ids[idx - 1]).strip() if idx - 1 < len(segment_ids) else f"segment_{idx:02d}"
        if idx > 1:
            merged_steps.append(
                {
                    "tool_name": "bash_run",
                    "arguments": {"command": f"echo __COMPOSITION_SEGMENT__:{segment_id}"},
                }
            )
        for step in _normalize_steps(segment):
            row = dict(step)
            row.pop("step_id", None)
            merged_steps.append(row)

    composed["plan"] = merged_steps
    canonical_ids = [str(x).strip() for x in segment_ids if str(x).strip()]
    if canonical_ids:
        composed["canonical_template"] = "composed::" + "+".join(canonical_ids)
    thought = str(base_plan.get("thought_process", "")).strip()
    suffix = "Composed deterministic fallback plan from multiple templates."
    composed["thought_process"] = f"{thought} {suffix}".strip()
    execution_options = composed.get("execution_options", {})
    options = dict(execution_options) if isinstance(execution_options, dict) else {}
    options["composition_enabled"] = True
    options["composed_pipeline_ids"] = canonical_ids
    composed["execution_options"] = options
    return _renumber_plan_steps(composed)


def _extract_bam_list_paths_from_plan(plan: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for step in _normalize_steps(plan):
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        for value in args.values():
            text = str(value or "").strip()
            if not text:
                continue
            if text.endswith(".txt") and "bam" in text.lower():
                if text not in seen:
                    seen.add(text)
                    out.append(text)
            for token in BAM_LIST_TOKEN_RE.findall(text):
                tok = str(token or "").strip()
                if tok and tok not in seen:
                    seen.add(tok)
                    out.append(tok)
    return out


def _as_bool_token(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _apply_featurecounts_paired_mode(plan: dict[str, Any], *, force: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    steps = _normalize_steps(plan)
    if not steps:
        return plan, {"changed": False, "reason": "plan_missing"}

    paired_signal = bool(force)
    if not paired_signal:
        for step in steps:
            tool = str(step.get("tool_name", "")).strip().lower()
            if tool not in {"star_align", "star_2pass_align", "hisat2_align"}:
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            if str(args.get("reads_1", "")).strip() and str(args.get("reads_2", "")).strip():
                paired_signal = True
                break
    if not paired_signal:
        return plan, {"changed": False, "reason": "no_paired_alignment_signal"}

    changed_steps: list[int] = []
    for idx, step in enumerate(steps, start=1):
        tool = str(step.get("tool_name", "")).strip().lower()
        if tool != "featurecounts_run":
            continue
        args = dict(step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {})
        if _as_bool_token(args.get("is_paired_end")):
            continue
        args["is_paired_end"] = True
        if "count_read_pairs" not in args:
            args["count_read_pairs"] = True
        step["arguments"] = args
        changed_steps.append(idx)

    if not changed_steps:
        return plan, {"changed": False, "reason": "featurecounts_already_paired"}

    patched_plan = dict(plan) if isinstance(plan, dict) else {}
    patched_plan["plan"] = steps
    patched_plan = _renumber_plan_steps(patched_plan)
    return patched_plan, {
        "changed": True,
        "reason": "featurecounts_switched_to_paired_end",
        "changed_steps": changed_steps,
        "diff_summary": {"changed_steps": len(changed_steps)},
    }


def _first_failed_step_number(step_statuses: list[Any], fallback_next_idx: int = 0) -> int:
    normalized = [str(status).strip().lower() for status in (step_statuses or [])]
    for idx, status in enumerate(normalized, start=1):
        if status == "failed":
            return idx
    if normalized and all(status == "completed" for status in normalized):
        return 0
    if fallback_next_idx > 0:
        return int(fallback_next_idx + 1)
    return 0


def _failed_tool_name(plan: dict[str, Any], failed_step_number: int) -> str:
    if failed_step_number <= 0:
        return ""
    steps = _normalize_steps(plan)
    idx = failed_step_number - 1
    if 0 <= idx < len(steps):
        return str(steps[idx].get("tool_name", "")).strip().lower()
    return ""


def _signature_contains(text: str, marker: str) -> bool:
    return marker.lower() in (text or "").lower()
