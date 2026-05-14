from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from bio_harness.core.schemas import RepairAuditEntrySchema
from bio_harness.core.tool_registry import default_tool_registry

DEFAULT_MAX_REPAIR_ATTEMPTS: Dict[str, int] = {
    "tool_missing": 2,
    "missing_reference": 2,
    "stale_tmp_cache": 1,
    "format_input_error": 1,
    "validation_block": 1,
    "policy_block": 1,
    "runtime_step_failure": 3,
    "contract_mismatch": 2,
    "unknown_failure": 1,
    "tool_substitution": 2,
    "resource_exhaustion": 0,
    "permission_error": 0,
}


def _build_bidirectional_equivalence_map(
    forward: Dict[str, List[str]],
) -> Dict[str, List[str]]:
    """Build bidirectional equivalence map from forward-only map.

    If bwa_mem_align -> [bowtie2_align, minimap2_align], then also:
    bowtie2_align -> [bwa_mem_align, minimap2_align]
    minimap2_align -> [bwa_mem_align, bowtie2_align]
    """
    groups: Dict[str, set] = {}
    for key, alternatives in forward.items():
        group = {key} | set(alternatives)
        merged_key = None
        for member in list(group):
            if member in groups:
                if merged_key is None:
                    merged_key = member
                elif member != merged_key and member in groups:
                    groups[merged_key] |= groups.pop(member)
        if merged_key is None:
            merged_key = key
        groups.setdefault(merged_key, set()).update(group)
    result: Dict[str, List[str]] = {}
    for group_members in groups.values():
        for tool in group_members:
            result[tool] = sorted(group_members - {tool})
    return result


# Tool equivalence map for recovery substitutions (bidirectional).
TOOL_EQUIVALENCE_MAP: Dict[str, List[str]] = {
    tool_name: default_tool_registry().alternative_tools_for(tool_name)
    for tool_name in default_tool_registry().known_tool_names()
    if default_tool_registry().alternative_tools_for(tool_name)
}


def classify_failure(run: Dict[str, Any]) -> str:
    """Categorize a run failure into a recovery class.

    Inspects run state flags and error messages to determine the failure
    category, which drives repair strategy and retry limits.

    Args:
        run: Run state dict with error info, detection flags, and step statuses.

    Returns:
        Failure class string (e.g. 'tool_missing', 'runtime_step_failure').
    """
    err = str(run.get("error", "")).lower()
    if run.get("policy_block_detected", False) or "__policy_block__" in err or "denied command" in err:
        return "policy_block"
    if run.get("validation_block_detected", False) or "blocked by validation agent" in err or "__validation_block__" in err:
        return "validation_block"
    if (
        run.get("execution_stalled_detected", False)
        or run.get("planner_timeout_detected", False)
        or "execution stalled" in err
        or "stalled for" in err
        or "planner request timed out" in err
    ):
        return "runtime_step_failure"
    if run.get("missing_tools_detected", []):
        return "tool_missing"
    if run.get("missing_reference_detected") or "missing reference" in err:
        return "missing_reference"
    if run.get("stale_tmp_cache_detected", False) or "__stale_tmp_cache__" in err:
        return "stale_tmp_cache"
    if (
        run.get("format_input_error_detected", False)
        or "__missing_pair__" in err
        or "__read_decompress_failed__" in err
        or "input validation issue" in err
    ):
        return "format_input_error"
    step_statuses = run.get("step_statuses", [])
    if isinstance(step_statuses, list) and any(str(s).strip().lower() == "failed" for s in step_statuses):
        return "runtime_step_failure"
    if run.get("no_fastq_found", False) or run.get("missing_sample_groups"):
        return "format_input_error"
    contract_val = run.get("contract_validation", {}) if isinstance(run.get("contract_validation", {}), dict) else {}
    if contract_val.get("missing_capabilities"):
        return "contract_mismatch"
    # Resource exhaustion — no retry, needs human intervention
    if any(p in err for p in ("no space left", "disk full", "enospc")):
        return "resource_exhaustion"
    if any(p in err for p in ("out of memory", "cannot allocate memory", "killed", "oom-kill", "oom_kill")):
        return "resource_exhaustion"
    if "permission denied" in err:
        return "permission_error"
    if "exit code" in err or "failed" in err:
        return "runtime_step_failure"
    return "unknown_failure"


def max_attempts_for_class(failure_class: str, limits: Dict[str, int] | None = None) -> int:
    """Return the maximum allowed repair attempts for a failure class.

    Args:
        failure_class: The failure category string.
        limits: Optional override for the default repair attempt limits.

    Returns:
        Maximum number of repair attempts allowed.
    """
    table = limits or DEFAULT_MAX_REPAIR_ATTEMPTS
    return int(table.get(failure_class, 1))


def can_attempt_repair(
    attempts: Dict[str, Any],
    failure_class: str,
    limits: Dict[str, int] | None = None,
) -> bool:
    """Check if another repair attempt is allowed for this failure class.

    Args:
        attempts: Dict mapping failure class to current attempt count.
        failure_class: The failure category string.
        limits: Optional override for the default repair attempt limits.

    Returns:
        True if the current attempt count is below the maximum.
    """
    current = int((attempts or {}).get(failure_class, 0))
    return current < max_attempts_for_class(failure_class, limits=limits)


def classify_failure_with_context(
    run: Dict[str, Any],
    selected_dir: str | Path | None = None,
    plan: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return a richer failure classification with recovery strategy hints.

    Returns a dict with:
      - failure_class: str (same as classify_failure)
      - recovery_strategy: one of retry_step, skip_step_use_artifact,
        replan_tail, substitute_tool, full_replan
      - existing_artifacts: list of output files from prior steps that exist
      - viable_substitutions: alternative tools for the failed step
    """
    failure_class = classify_failure(run)

    # Check for existing artifacts at the current step
    existing_artifacts: List[str] = []
    viable_substitutions: List[str] = []
    failed_tool = str(run.get("failed_tool_name", "")).strip()

    if selected_dir and plan:
        try:
            from bio_harness.core.artifact_inspectors import scan_existing_step_outputs
            from bio_harness.core.step_completion import check_completion_manifest

            step_idx = int(run.get("failed_step_idx", 0) or 0)
            outputs = scan_existing_step_outputs(Path(selected_dir), plan, step_idx)
            existing_artifacts = []
            for path_text, info in outputs.items():
                if not info.get("valid", False):
                    continue
                if not info.get("is_dir", False):
                    existing_artifacts.append(path_text)
                    continue
                if failed_tool and check_completion_manifest(Path(path_text), failed_tool).completed:
                    existing_artifacts.append(path_text)
        except Exception:
            pass

    if failed_tool:
        viable_substitutions = list(default_tool_registry().alternative_tools_for(failed_tool))
        # Augment with graph-based alternatives when available
        if not viable_substitutions:
            try:
                from bio_harness.core.capability_graph import CapabilityGraph
                graph = CapabilityGraph.default()
                graph_alts = graph.alternatives_for_tool(failed_tool)
                viable_substitutions = graph_alts
            except Exception:
                pass

    # Determine recovery strategy
    if existing_artifacts:
        strategy = "skip_step_use_artifact"
    elif failure_class == "tool_missing" and viable_substitutions:
        strategy = "substitute_tool"
    elif failure_class in {"runtime_step_failure", "unknown_failure"} and viable_substitutions:
        strategy = "substitute_tool"
    elif failure_class in {"contract_mismatch", "format_input_error"}:
        strategy = "replan_tail"
    elif failure_class == "policy_block":
        strategy = "full_replan"
    else:
        strategy = "retry_step"

    return {
        "failure_class": failure_class,
        "recovery_strategy": strategy,
        "existing_artifacts": existing_artifacts,
        "viable_substitutions": viable_substitutions,
        "failed_tool": failed_tool,
    }


def build_repair_audit_entry(
    *,
    run_id: str,
    failure_class: str,
    attempt: int,
    action: str,
    details: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a timestamped audit record for a repair attempt.

    Args:
        run_id: Unique identifier for the current run.
        failure_class: The failure category being repaired.
        attempt: Current attempt number.
        action: Description of the repair action taken.
        details: Additional context about the repair.

    Returns:
        Audit entry dict with timestamp, run_id, failure info, and patch audit.
    """
    payload = {
        "ts": datetime.now().isoformat(),
        "run_id": run_id,
        "failure_class": failure_class,
        "attempt": int(attempt),
        "action": action,
        "details": details,
        "patch_audit": {
            "run_id": run_id,
            "why": details.get("why", action),
            "diff_summary": details.get("diff_summary", {}),
        },
    }
    return RepairAuditEntrySchema.model_validate(payload).model_dump(mode="json")
