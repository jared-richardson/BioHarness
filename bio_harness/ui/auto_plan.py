"""UI helpers for normalizing model-generated execution plans.

The Streamlit UI should use the same deterministic protocol repair path as the
rest of the harness instead of maintaining a narrow shell-only interpretation
of executable plans. These helpers keep the auto-start workflow aligned with
the benchmark-blind backend behavior while staying easy to test outside the
browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from bio_harness.core.benchmark_policy import SCIENTIFIC_HARNESS_POLICY, normalize_benchmark_policy
from bio_harness.core.contracts import assess_plan_contract
from bio_harness.core.protocol_grounding import (
    assess_protocol_grounding,
    deterministic_protocol_repair,
)
from bio_harness.harness.plan_helpers_support import _is_output_free_read_only_bash
from bio_harness.harness.plan_semantic_guards import _assess_plan_semantic_guards

if TYPE_CHECKING:
    from bio_harness.agents.orchestrator import Orchestrator


def is_probe_only_bash(command: str) -> bool:
    """Return whether one shell command is a non-actionable probe.

    Args:
        command: Shell command string from one ``bash_run`` step.

    Returns:
        ``True`` when the command only probes environment state and does not
        advance scientific execution.
    """
    return _is_output_free_read_only_bash(command)


def is_actionable_execution_plan(plan: dict[str, Any]) -> bool:
    """Return whether one executable plan contains real work.

    Args:
        plan: Candidate execution plan.

    Returns:
        ``True`` when at least one plan step would perform meaningful work.
    """
    steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(steps, list) or not steps:
        return False

    for step in steps:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool_name", "")).strip()
        if not tool_name:
            continue
        if tool_name != "bash_run":
            return True
        args = step.get("arguments", {}) if isinstance(step.get("arguments"), dict) else {}
        command = str(args.get("command", "")).strip()
        if command and not is_probe_only_bash(command):
            return True
    return False


def normalize_ui_auto_plan(
    plan: dict[str, Any],
    *,
    orchestrator: Orchestrator,
    user_request: str,
    contract: dict[str, Any] | None,
    selected_dir: str,
    data_root: str,
    project_root: str,
    benchmark_policy: str = SCIENTIFIC_HARNESS_POLICY,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize one UI-generated execution plan for real execution.

    Args:
        plan: The direct model output from the UI planning turn.
        orchestrator: Shared orchestrator instance.
        user_request: Full user request/context string used to build the plan.
        contract: Inferred execution contract for the request.
        selected_dir: Run output directory.
        data_root: Input data root resolved for the UI session.
        project_root: Repository root used for protocol grounding.
        benchmark_policy: Benchmark policy for blind or strict benchmark runs.

    Returns:
        Tuple of ``(selected_plan, metadata)`` where metadata records the
        deterministic analysis spec, repair outcome, and validation results.
    """
    normalized_plan = dict(plan) if isinstance(plan, dict) else {}
    normalized_policy = normalize_benchmark_policy(benchmark_policy)
    analysis_spec = orchestrator.build_analysis_spec(
        user_request,
        contract=contract,
        selected_dir=selected_dir,
        data_root=data_root,
        project_root=project_root,
        benchmark_policy=normalized_policy,
    )
    repair_meta: dict[str, Any] = {"changed": False, "why": "no_input_plan"}
    repaired_plan = normalized_plan

    if normalized_plan.get("plan"):
        try:
            repaired_plan, repair_meta = deterministic_protocol_repair(
                normalized_plan,
                analysis_spec=analysis_spec,
                selected_dir=Path(selected_dir),
                data_root=Path(data_root),
            )
        except Exception as exc:  # pragma: no cover - defensive metadata path
            repaired_plan = normalized_plan
            repair_meta = {
                "changed": False,
                "why": "protocol_repair_failed",
                "exception_class": exc.__class__.__name__,
                "message": str(exc).strip() or exc.__class__.__name__,
            }

    if is_actionable_execution_plan(repaired_plan):
        selected_plan = repaired_plan
        selected_source = "repaired_plan"
    else:
        selected_plan = normalized_plan
        selected_source = "original_plan"

    protocol_validation = assess_protocol_grounding(selected_plan, analysis_spec)
    semantic_validation = _assess_plan_semantic_guards(selected_plan, analysis_spec=analysis_spec)

    full_template = repair_meta.get("_full_template")
    if isinstance(full_template, dict) and is_actionable_execution_plan(full_template):
        full_protocol_validation = assess_protocol_grounding(full_template, analysis_spec)
        full_semantic_validation = _assess_plan_semantic_guards(
            full_template,
            analysis_spec=analysis_spec,
        )
        if (
            (not protocol_validation.get("passed", False) or not semantic_validation.get("passed", False))
            and full_protocol_validation.get("passed", False)
            and full_semantic_validation.get("passed", False)
        ):
            selected_plan = full_template
            selected_source = "compiled_template"
            protocol_validation = full_protocol_validation
            semantic_validation = full_semantic_validation

    selected_tools = [
        str(step.get("tool_name", "")).strip()
        for step in (selected_plan.get("plan", []) if isinstance(selected_plan, dict) else [])
        if isinstance(step, dict) and str(step.get("tool_name", "")).strip()
    ]
    selected_contract_validation = (
        assess_plan_contract(selected_plan, contract)
        if isinstance(contract, dict) and contract
        else {}
    )
    original_contract_validation = (
        assess_plan_contract(normalized_plan, contract)
        if isinstance(contract, dict) and contract and is_actionable_execution_plan(normalized_plan)
        else {}
    )
    if (
        isinstance(contract, dict)
        and contract
        and selected_contract_validation
        and not selected_contract_validation.get("passed", False)
        and original_contract_validation.get("passed", False)
    ):
        selected_plan = normalized_plan
        selected_source = "original_plan_contract_preserved"
        protocol_validation = assess_protocol_grounding(selected_plan, analysis_spec)
        semantic_validation = _assess_plan_semantic_guards(selected_plan, analysis_spec=analysis_spec)
        selected_tools = [
            str(step.get("tool_name", "")).strip()
            for step in (selected_plan.get("plan", []) if isinstance(selected_plan, dict) else [])
            if isinstance(step, dict) and str(step.get("tool_name", "")).strip()
        ]
        selected_contract_validation = original_contract_validation

    return selected_plan, {
        "analysis_spec": analysis_spec,
        "repair_meta": repair_meta,
        "protocol_validation": protocol_validation,
        "semantic_validation": semantic_validation,
        "contract_validation": selected_contract_validation,
        "original_contract_validation": original_contract_validation,
        "actionable": is_actionable_execution_plan(selected_plan),
        "used_repaired_plan": selected_plan == repaired_plan,
        "selected_source": selected_source,
        "selected_tools": selected_tools,
        "benchmark_policy": normalized_policy,
    }
