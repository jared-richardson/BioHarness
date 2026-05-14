"""Central semantic validation rules for execution plans."""

from __future__ import annotations

import re
from typing import Any

from bio_harness.core.bash_single_operation_policy import check_single_operation
from bio_harness.core.stage_dag import validate_stage_dag
from bio_harness.core.tool_registry import default_tool_registry

_EVOLUTION_SAMPLE_RE = re.compile(r"(?<![A-Za-z0-9])(evol\d+)(?![A-Za-z0-9])", re.IGNORECASE)
_EVOLUTION_MINUS_ANCESTOR_ALIAS_RE = re.compile(
    r"(?:subtracted[_-]?anc|ancestor[_-]?subtracted|minus[_-]?anc|no[_-]?anc)",
    re.IGNORECASE,
)
_BCFTOOLS_ISEC_SHARED_WITH_ANCESTOR_RE = re.compile(
    r"bcftools\s+isec\b.*(?:-n(?:=|\s+)\+?2\b)",
    re.IGNORECASE | re.DOTALL,
)


def _evolution_minus_ancestor_consumers(steps: list[dict[str, Any]]) -> set[str]:
    """Return evolved-branch IDs consumed as minus-ancestor VCFs downstream."""

    consumers: set[str] = set()
    for step in steps:
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        if tool_name != "snpeff_annotate":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        input_vcf = str(args.get("input_vcf", "") or "").strip().lower()
        if not input_vcf or not _EVOLUTION_MINUS_ANCESTOR_ALIAS_RE.search(input_vcf):
            continue
        match = _EVOLUTION_SAMPLE_RE.search(input_vcf)
        if match:
            consumers.add(str(match.group(1) or "").lower())
    return consumers


def _materializes_minus_ancestor_output(command_l: str, sample: str) -> bool:
    """Return whether one command emits a concrete minus-ancestor artifact."""

    aliases = (
        f"{sample}_subtracted",
        f"{sample}.subtracted",
        f"{sample}_ancestor_subtracted",
        f"{sample}.ancestor_subtracted",
        f"{sample}_minus_anc",
        f"{sample}.minus_anc",
        f"{sample}_no_anc",
        f"{sample}.no_anc",
    )
    return any(alias in command_l for alias in aliases)


def _filters_raw_call_into_novel_output(command_l: str, sample: str) -> bool:
    """Return whether one command substitutes raw-call filtering for subtraction."""

    return (
        "bcftools view" in command_l
        and f"{sample}_raw.vcf" in command_l
        and f"{sample}_novel.vcf" in command_l
    )


def _evolution_subtraction_semantic_issues(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return minus-ancestor semantic issues for evolution comparison plans."""

    expected_samples = _evolution_minus_ancestor_consumers(steps)
    if not expected_samples:
        return []

    issues: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        if tool_name != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        command_l = command.lower()
        if "bcftools isec" not in command_l:
            continue
        referenced_samples = sorted(sample for sample in expected_samples if sample in command_l)
        if not referenced_samples:
            continue
        if "ancestor" not in command_l and "anc_" not in command_l:
            continue

        missing_outputs = [
            sample for sample in referenced_samples if not _materializes_minus_ancestor_output(command_l, sample)
        ]
        if missing_outputs:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "invalid_evolution_minus_ancestor_handoff",
                    "reason": "missing_concrete_minus_ancestor_outputs",
                    "samples": missing_outputs,
                    "command": command,
                }
            )

        if _BCFTOOLS_ISEC_SHARED_WITH_ANCESTOR_RE.search(command_l):
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "invalid_evolution_minus_ancestor_handoff",
                    "reason": "shared_with_ancestor_intersection_for_minus_ancestor_step",
                    "samples": referenced_samples,
                    "command": command,
                }
            )

        raw_filter_samples = [
            sample for sample in referenced_samples if _filters_raw_call_into_novel_output(command_l, sample)
        ]
        if raw_filter_samples:
            issues.append(
                {
                    "step_id": int(step.get("step_id", idx)),
                    "tool_name": "bash_run",
                    "issue": "invalid_evolution_minus_ancestor_handoff",
                    "reason": "raw_call_filter_does_not_subtract_ancestor",
                    "samples": raw_filter_samples,
                    "command": command,
                }
            )

    return issues


def _normalize_steps(plan: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = plan.get("plan", []) if isinstance(plan, dict) else []
    if not isinstance(raw_steps, list):
        return []
    return [step for step in raw_steps if isinstance(step, dict)]


def _distinct_group_tags(plan: dict[str, Any]) -> tuple[str, str]:
    execution_options = (
        plan.get("execution_options", {})
        if isinstance(plan.get("execution_options", {}), dict)
        else {}
    )
    control_tag = str(execution_options.get("control_tag", "") or "").strip()
    treatment_tag = str(execution_options.get("treatment_tag", "") or "").strip()
    return control_tag, treatment_tag


def semantic_plan_issues(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return centralized semantic issues for one execution plan."""

    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    registry = default_tool_registry()
    steps = _normalize_steps(plan)
    issues: list[dict[str, Any]] = [
        issue.as_dict() for issue in validate_stage_dag(plan, registry=registry)
    ]
    for idx, step in enumerate(steps, start=1):
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        if tool_name != "bash_run":
            continue
        args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
        command = str(args.get("command", "") or "").strip()
        operation_check = check_single_operation(command)
        if operation_check.passed:
            continue
        issues.append(
            {
                "step_id": int(step.get("step_id", idx)),
                "tool_name": "bash_run",
                "issue": "oops_violation",
                "type": "oops_violation",
                "violations": operation_check.violations,
                "operation_count": operation_check.operation_count,
                "normalized_command": operation_check.normalized_command,
                "suggestion": (
                    "Emit exactly one shell operation per bash_run step. "
                    "Split compound shell into multiple steps and prefer typed wrappers when available."
                ),
            }
        )

    if not analysis_type:
        return issues

    if analysis_type == "transcript_quantification":
        for idx, step in enumerate(steps, start=1):
            tool_name = str(step.get("tool_name", "") or "").strip().lower()
            capabilities = set(registry.capabilities_for(tool_name))
            if capabilities.intersection({"differential_analysis", "group_comparison"}):
                issues.append(
                    {
                        "step_id": int(step.get("step_id", idx)),
                        "tool_name": tool_name,
                        "issue": "analysis_type_drift",
                        "analysis_type": analysis_type,
                        "reason": "transcript_quantification_plan_contains_group_or_differential_tool",
                    }
                )

    canonical_template = str(plan.get("canonical_template", "") or "").strip().lower()
    if canonical_template.startswith(("differential_expression_", "somatic_variant_")):
        control_tag, treatment_tag = _distinct_group_tags(plan)
        if not control_tag or not treatment_tag or control_tag == treatment_tag:
            issues.append(
                {
                    "step_id": None,
                    "tool_name": canonical_template or "plan",
                    "issue": "missing_distinct_group_evidence",
                    "analysis_type": analysis_type,
                    "reason": "group_comparison_template_missing_distinct_group_tags",
                }
            )

    if analysis_type == "bacterial_evolution_variant_calling":
        issues.extend(_evolution_subtraction_semantic_issues(steps))

    return issues


def assess_semantic_plan(
    plan: dict[str, Any],
    *,
    analysis_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the normalized semantic validation result for one plan."""

    issues = semantic_plan_issues(plan, analysis_spec=analysis_spec)
    return {"passed": not issues, "issues": issues}
