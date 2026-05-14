"""Execution normalization helpers shared by the Streamlit UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.artifact_role_validator import (
    repair_artifact_role_violations,
    summarize_artifact_role_violations,
    validate_artifact_role_invariants,
)
from scripts.run_agent_e2e_plan_normalization_support import (
    PlanNormalizationContext,
    normalize_plan_for_execution,
)


def normalize_ui_run_plan_for_execution(
    *,
    plan: Mapping[str, Any],
    analysis_spec: Mapping[str, Any] | None,
    plan_contract: Mapping[str, Any] | None,
    user_request: str,
    selected_dir: str,
    data_root: str,
    benchmark_policy: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply backend execution normalization to one UI-managed run plan.

    Args:
        plan: Candidate plan selected by the UI planner.
        analysis_spec: Normalized analysis spec attached to the UI run.
        plan_contract: Request contract attached to the UI run.
        user_request: User request text persisted on the UI run.
        selected_dir: Concrete run-owned selected directory.
        data_root: Active read-only data root for the run.
        benchmark_policy: Active benchmark policy token.

    Returns:
        Tuple of ``(normalized_plan, normalization_meta, featurecounts_meta)``.
    """

    selected_dir_path = Path(str(selected_dir or "")).expanduser().resolve(strict=False)
    data_root_path = Path(str(data_root or "")).expanduser().resolve(strict=False)
    analysis_spec_dict = dict(analysis_spec or {}) if isinstance(analysis_spec, Mapping) else {}
    context = PlanNormalizationContext(
        selected_dir=selected_dir_path,
        data_root=data_root_path,
        benchmark_policy=str(benchmark_policy or ""),
        user_request=str(user_request or ""),
        analysis_spec=analysis_spec_dict,
        runtime_binding_analysis_spec=analysis_spec_dict,
        plan_contract=dict(plan_contract or {}) if isinstance(plan_contract, Mapping) else {},
        preserved_tool_names=_preserved_tool_names_for_execution_normalization(analysis_spec_dict),
    )
    return normalize_plan_for_execution(
        dict(plan or {}),
        context=context,
        stabilize_artifact_roles=lambda candidate, source_plan: _stabilize_artifact_roles(
            candidate,
            source_plan=source_plan,
            selected_dir=selected_dir_path,
            data_root=data_root_path,
        ),
        artifact_role_issue_strings=lambda candidate: _artifact_role_issue_strings(
            candidate,
            selected_dir=selected_dir_path,
            data_root=data_root_path,
        ),
    )


def _preserved_tool_names_for_execution_normalization(
    analysis_spec: Mapping[str, Any],
) -> set[str]:
    """Return tool names whose explicit arguments should survive normalization."""

    intent = (
        analysis_spec.get("explicit_execution_intent", {})
        if isinstance(analysis_spec.get("explicit_execution_intent", {}), Mapping)
        else {}
    )
    raw_tools = intent.get("preserve_existing_values_for_tools", [])
    if not isinstance(raw_tools, list):
        raw_tools = []
    preserved = {
        str(tool).strip().lower()
        for tool in raw_tools
        if str(tool).strip()
    }
    if preserved:
        return preserved
    locked = intent.get("locked_tools", [])
    if not isinstance(locked, list):
        return set()
    return {
        str(tool).strip().lower()
        for tool in locked
        if str(tool).strip()
    }


def _artifact_role_issue_strings(
    plan: Mapping[str, Any],
    *,
    selected_dir: Path,
    data_root: Path,
) -> list[str]:
    """Return stable artifact-role issue strings for one plan."""

    violations = validate_artifact_role_invariants(
        dict(plan or {}),
        selected_dir=selected_dir,
        allowed_input_roots=[data_root],
    )
    return summarize_artifact_role_violations(violations)


def _stabilize_artifact_roles(
    plan: Mapping[str, Any],
    *,
    source_plan: Mapping[str, Any],
    selected_dir: Path,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Restore corrupted input or reference bindings from the source plan."""

    repaired, meta = repair_artifact_role_violations(
        dict(plan or {}),
        source_plan=dict(source_plan or {}),
        selected_dir=selected_dir,
        allowed_input_roots=[data_root],
    )
    issues = _artifact_role_issue_strings(
        repaired,
        selected_dir=selected_dir,
        data_root=data_root,
    )
    if not issues:
        return repaired, meta
    updated_meta = dict(meta or {})
    updated_meta["issues"] = issues
    return repaired, updated_meta
