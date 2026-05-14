"""Support helpers for execution-plan normalization.

This module keeps the large repair pipeline used during pre-execution plan
normalization out of the main plan-context mixin. The helpers here preserve the
existing repair order while making the runner script thinner and easier to
reason about.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bio_harness.core.direct_wrapper_completeness import repair_direct_wrapper_plan_bindings
from bio_harness.core.protocol_grounding._shared import _apply_parameter_knowledge_base
from bio_harness.core.template_assistance_policy import (
    protocol_template_assistance_enabled,
)
from bio_harness.harness.plan_repair_analysis_workflows import (
    _repair_direct_wrapper_inspection_bash_run,
    _repair_direct_wrapper_helper_bash_run,
)
from scripts.run_agent_e2e_support import (
    _apply_featurecounts_paired_mode,
    _apply_parameter_profile,
    _redirect_output_paths_to_selected_dir,
    _relocate_undocumented_output_arguments_to_final_deliverables,
    _repair_bash_redirection_output_dirs,
    _repair_bash_tool_output_parent_dirs,
    _repair_cystic_fibrosis_csv_exports_with_analysis_spec,
    _repair_deseq_bash_run_to_skill,
    _repair_evolution_alignment_path_bindings,
    _repair_evolution_missing_variant_branches,
    _repair_evolution_spades_reference_usage,
    _repair_fastp_cli_flags,
    _repair_metagenomics_prebuilt_db_bindings,
    _repair_metagenomics_trimmed_read_usage,
    _repair_missing_fastq_inputs_in_plan,
    _repair_multi_model_compare_pathways_commands,
    _repair_quantification_count_exports,
    _repair_requested_references_and_index_bases_in_plan,
    _repair_rna_seq_de_plan_with_assay_compiler,
    _repair_shared_variant_csv_exports_with_analysis_spec,
    _repair_single_cell_export_tail,
    _repair_single_cell_qc_thresholds,
    _repair_variant_annotation_impact_filter,
    _repair_workspace_placeholder_paths_in_plan,
    canonicalize_execution_plan,
    is_bioagentbench_planning_strict_policy,
    rebind_direct_plan_for_strict_mode,
)

PlanDict = dict[str, Any]
ArtifactStabilizer = Callable[[PlanDict, PlanDict], tuple[PlanDict, dict[str, Any]]]
ArtifactIssueCollector = Callable[[PlanDict], list[str]]


@dataclass(frozen=True)
class PlanNormalizationContext:
    """Stable inputs required for pre-execution plan normalization.

    Attributes:
        selected_dir: Selected workspace output root.
        data_root: Task-local readonly input root.
        benchmark_policy: Active benchmark policy token.
        user_request: Original user request text.
        analysis_spec: Current analysis-spec payload.
        runtime_binding_analysis_spec: Analysis-spec payload normalized for
            runtime binding.
        plan_contract: Current inferred request contract.
        preserved_tool_names: Tool names whose explicit values must survive
            parameter-profile application.
        freeze_completed_prefix: Whether the caller is validating a stepwise
            cumulative plan whose completed prefix must not receive scientific
            plan repairs.
    """

    selected_dir: Path | str
    data_root: Path | str
    benchmark_policy: str
    user_request: str
    analysis_spec: PlanDict
    runtime_binding_analysis_spec: PlanDict
    plan_contract: PlanDict
    preserved_tool_names: set[str]
    freeze_completed_prefix: bool = False


def _record_changed_meta(
    canonical_meta: PlanDict,
    *,
    key: str,
    repair_meta: PlanDict,
) -> PlanDict:
    """Record one successful repair payload in the normalization metadata."""

    if not repair_meta.get("changed", False):
        return canonical_meta
    updated = dict(canonical_meta or {})
    updated["changed"] = True
    updated[key] = repair_meta
    return updated


def _guard_artifact_role_regression(
    source_plan: PlanDict,
    repaired_plan: PlanDict,
    *,
    repair_meta: PlanDict,
    artifact_role_issue_strings: ArtifactIssueCollector | None,
) -> tuple[PlanDict, PlanDict]:
    """Reject deterministic repairs that introduce new artifact-role issues."""

    if (
        artifact_role_issue_strings is None
        or not repair_meta.get("changed", False)
    ):
        return repaired_plan, repair_meta
    before_issues = set(artifact_role_issue_strings(source_plan))
    after_issues = set(artifact_role_issue_strings(repaired_plan))
    if after_issues.issubset(before_issues):
        return repaired_plan, repair_meta
    return source_plan, {
        "changed": False,
        "why": "artifact_role_regression",
        "introduced_issues": sorted(after_issues - before_issues),
        "prior_issues": sorted(before_issues),
        "retained_issues": sorted(after_issues & before_issues),
    }


def _apply_repair_step(
    normalized: PlanDict,
    canonical_meta: PlanDict,
    *,
    key: str,
    repair_fn: Callable[..., tuple[PlanDict, PlanDict]],
    artifact_role_issue_strings: ArtifactIssueCollector | None = None,
    **kwargs: Any,
) -> tuple[PlanDict, PlanDict]:
    """Apply one repair function and merge its metadata when it changes."""

    repaired, repair_meta = repair_fn(normalized, **kwargs)
    repaired, repair_meta = _guard_artifact_role_regression(
        normalized,
        repaired,
        repair_meta=repair_meta,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    return repaired, _record_changed_meta(
        canonical_meta,
        key=key,
        repair_meta=repair_meta,
    )


def _apply_artifact_stabilization(
    normalized: PlanDict,
    canonical_meta: PlanDict,
    *,
    key: str,
    source_plan: PlanDict,
    stabilize_artifact_roles: ArtifactStabilizer,
    artifact_role_issue_strings: ArtifactIssueCollector | None = None,
) -> tuple[PlanDict, PlanDict]:
    """Reapply artifact-role stabilization and merge its metadata."""

    repaired, repair_meta = stabilize_artifact_roles(normalized, source_plan)
    repaired, repair_meta = _guard_artifact_role_regression(
        normalized,
        repaired,
        repair_meta=repair_meta,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    return repaired, _record_changed_meta(
        canonical_meta,
        key=key,
        repair_meta=repair_meta,
    )


def _apply_initial_binding_repairs(
    normalized: PlanDict,
    canonical_meta: PlanDict,
    *,
    context: PlanNormalizationContext,
    planning_strict: bool,
    artifact_role_issue_strings: ArtifactIssueCollector | None = None,
) -> tuple[PlanDict, PlanDict]:
    """Apply early binding repairs before output relocation."""

    spec = dict(context.runtime_binding_analysis_spec or {})
    if str(context.data_root or "").strip():
        spec.setdefault("requested_data_root", str(Path(context.data_root).expanduser().resolve(strict=False)))
    if _strict_direct_rebinding_enabled(context=context, planning_strict=planning_strict):
        rebound_plan, rebound_meta = rebind_direct_plan_for_strict_mode(
            normalized,
            analysis_spec=spec,
        )
        if rebound_meta.get("changed", False):
            normalized = rebound_plan
            canonical_meta = _record_changed_meta(
                canonical_meta,
                key="strict_direct_plan_rebinding",
                repair_meta=rebound_meta,
            )

    manifest = spec.get("file_manifest")
    if manifest is not None:
        try:
            steps = normalized.get("steps", [])
            if isinstance(steps, list):
                injected_steps = manifest.inject_into_plan(steps)
                if injected_steps != steps:
                    normalized = {**normalized, "steps": injected_steps}
                    canonical_meta = dict(canonical_meta or {})
                    canonical_meta["changed"] = True
                    canonical_meta["manifest_injection"] = True
        except Exception:
            pass

    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="analysis_spec_parameter_profile_repairs",
        repair_fn=_apply_parameter_profile,
        artifact_role_issue_strings=artifact_role_issue_strings,
        parameter_profile=list(spec.get("parameter_profile", []) or []),
        preserve_existing_values_for_tools=context.preserved_tool_names,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="parameter_knowledge_base_repairs",
        repair_fn=_apply_parameter_knowledge_base,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="single_cell_qc_threshold_repairs",
        repair_fn=_repair_single_cell_qc_thresholds,
        artifact_role_issue_strings=artifact_role_issue_strings,
        analysis_spec=spec,
        benchmark_policy=context.benchmark_policy,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="single_cell_export_tail_repairs",
        repair_fn=_repair_single_cell_export_tail,
        artifact_role_issue_strings=artifact_role_issue_strings,
        analysis_spec=spec,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="requested_reference_or_index_repairs",
        repair_fn=_repair_requested_references_and_index_bases_in_plan,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
        data_root=context.data_root,
        request_text=context.user_request,
    )
    if not planning_strict:
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="metagenomics_prebuilt_db_repairs",
            repair_fn=_repair_metagenomics_prebuilt_db_bindings,
            artifact_role_issue_strings=artifact_role_issue_strings,
            selected_dir=context.selected_dir,
            data_root=context.data_root,
            analysis_spec=context.analysis_spec,
            request_text=context.user_request,
        )
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="metagenomics_trimmed_read_repairs",
            repair_fn=_repair_metagenomics_trimmed_read_usage,
            artifact_role_issue_strings=artifact_role_issue_strings,
            selected_dir=context.selected_dir,
            analysis_spec=context.analysis_spec,
            request_text=context.user_request,
        )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="early_fastq_repairs",
        repair_fn=_repair_missing_fastq_inputs_in_plan,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
        data_root=context.data_root,
    )
    return normalized, canonical_meta


def _apply_path_binding_repairs(
    normalized: PlanDict,
    canonical_meta: PlanDict,
    *,
    context: PlanNormalizationContext,
    source_plan: PlanDict,
    stabilize_artifact_roles: ArtifactStabilizer,
    artifact_role_issue_strings: ArtifactIssueCollector,
) -> tuple[PlanDict, PlanDict]:
    """Apply artifact, wrapper-binding, and output-path repairs."""

    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_prebinding",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="direct_wrapper_binding_repairs",
        repair_fn=repair_direct_wrapper_plan_bindings,
        artifact_role_issue_strings=artifact_role_issue_strings,
        analysis_spec=context.runtime_binding_analysis_spec,
        contract=context.plan_contract,
        request_text=context.user_request,
        selected_dir=context.selected_dir,
        data_root=context.data_root,
    )
    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_direct_wrapper_binding",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="workspace_placeholder_repairs",
        repair_fn=_repair_workspace_placeholder_paths_in_plan,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
        data_root=context.data_root,
    )
    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_workspace_path_repair",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="output_redirect_repairs",
        repair_fn=_redirect_output_paths_to_selected_dir,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
        data_root=context.data_root,
    )
    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_output_redirect",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="post_redirect_direct_wrapper_binding_repairs",
        repair_fn=repair_direct_wrapper_plan_bindings,
        artifact_role_issue_strings=artifact_role_issue_strings,
        analysis_spec=context.runtime_binding_analysis_spec,
        contract=context.plan_contract,
        request_text=context.user_request,
        selected_dir=context.selected_dir,
        data_root=context.data_root,
    )
    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_post_redirect_binding",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="direct_wrapper_inspection_bash_run_repairs",
        repair_fn=_repair_direct_wrapper_inspection_bash_run,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
        analysis_spec=context.runtime_binding_analysis_spec,
        request_text=context.user_request,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="direct_wrapper_helper_bash_run_repairs",
        repair_fn=_repair_direct_wrapper_helper_bash_run,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
        analysis_spec=context.runtime_binding_analysis_spec,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="undocumented_output_argument_repairs",
        repair_fn=_relocate_undocumented_output_arguments_to_final_deliverables,
        artifact_role_issue_strings=artifact_role_issue_strings,
        required_deliverables=list(context.runtime_binding_analysis_spec.get("required_deliverables", []) or []),
    )
    final_artifact_issues = artifact_role_issue_strings(normalized)
    if final_artifact_issues:
        canonical_meta = dict(canonical_meta or {})
        canonical_meta["artifact_role_issues"] = final_artifact_issues
    return normalized, canonical_meta


def _apply_analysis_specific_repairs(
    normalized: PlanDict,
    canonical_meta: PlanDict,
    *,
    context: PlanNormalizationContext,
    planning_strict: bool,
    scientific_plan_mutations_enabled: bool,
    artifact_role_issue_strings: ArtifactIssueCollector | None = None,
) -> tuple[PlanDict, PlanDict]:
    """Apply analysis-family-specific deterministic repairs."""

    if not planning_strict:
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="evolution_reference_path_repairs",
            repair_fn=_repair_evolution_spades_reference_usage,
            artifact_role_issue_strings=artifact_role_issue_strings,
            request_text=context.user_request,
            selected_dir=Path(context.selected_dir),
            allow_destructive_mutations=False,
        )
        if scientific_plan_mutations_enabled:
            normalized, canonical_meta = _apply_repair_step(
                normalized,
                canonical_meta,
                key="evolution_spades_repairs",
                repair_fn=_repair_evolution_spades_reference_usage,
                artifact_role_issue_strings=artifact_role_issue_strings,
                request_text=context.user_request,
                selected_dir=Path(context.selected_dir),
                allow_destructive_mutations=True,
            )
            normalized, canonical_meta = _apply_repair_step(
                normalized,
                canonical_meta,
                key="evolution_branch_repairs",
                repair_fn=_repair_evolution_missing_variant_branches,
                artifact_role_issue_strings=artifact_role_issue_strings,
                request_text=context.user_request,
                selected_dir=context.selected_dir,
                data_root=context.data_root,
                analysis_spec=context.analysis_spec,
            )
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="evolution_alignment_path_repairs",
            repair_fn=_repair_evolution_alignment_path_bindings,
            artifact_role_issue_strings=artifact_role_issue_strings,
            request_text=context.user_request,
            selected_dir=context.selected_dir,
        )

    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="quantification_count_export_repairs",
        repair_fn=_repair_quantification_count_exports,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    if not planning_strict and scientific_plan_mutations_enabled:
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="deseq_bash_run_repairs",
            repair_fn=_repair_deseq_bash_run_to_skill,
            artifact_role_issue_strings=artifact_role_issue_strings,
            selected_dir=context.selected_dir,
            analysis_spec=context.analysis_spec,
        )
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="rna_seq_de_plan_repairs",
            repair_fn=_repair_rna_seq_de_plan_with_assay_compiler,
            artifact_role_issue_strings=artifact_role_issue_strings,
            selected_dir=context.selected_dir,
            data_root=context.data_root,
            analysis_spec=context.analysis_spec,
        )
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="shared_variant_csv_repairs",
            repair_fn=_repair_shared_variant_csv_exports_with_analysis_spec,
            artifact_role_issue_strings=artifact_role_issue_strings,
            analysis_spec=context.analysis_spec,
        )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="variant_annotation_impact_filter_repairs",
        repair_fn=_repair_variant_annotation_impact_filter,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    if not planning_strict and scientific_plan_mutations_enabled:
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="cystic_fibrosis_csv_export_repairs",
            repair_fn=_repair_cystic_fibrosis_csv_exports_with_analysis_spec,
            artifact_role_issue_strings=artifact_role_issue_strings,
            analysis_spec=context.analysis_spec,
            selected_dir=context.selected_dir,
            data_root=context.data_root,
            request_text=context.user_request,
        )
        normalized, canonical_meta = _apply_repair_step(
            normalized,
            canonical_meta,
            key="multi_model_compare_pathways_repairs",
            repair_fn=_repair_multi_model_compare_pathways_commands,
            artifact_role_issue_strings=artifact_role_issue_strings,
            analysis_spec=context.analysis_spec,
            selected_dir=context.selected_dir,
            data_root=context.data_root,
        )
    return normalized, canonical_meta


def _scientific_plan_mutations_enabled(
    *,
    context: PlanNormalizationContext,
    planning_strict: bool,
) -> bool:
    """Return whether deterministic scientific plan mutations are allowed.

    Artifact-path normalization is always allowed, but scientific template-like
    plan mutations must stay disabled for strict blind runs and scientific
    ablation variants that explicitly turn off template assistance.
    """

    if planning_strict:
        return False
    return protocol_template_assistance_enabled(context.benchmark_policy)


def _strict_direct_rebinding_enabled(
    *,
    context: PlanNormalizationContext,
    planning_strict: bool,
) -> bool:
    """Return whether deterministic direct-step artifact rebinding is enabled.

    Blind planning-strict runs always need deterministic rebinding. Scientific
    harness ablations that explicitly disable template assistance also need the
    same artifact rebinding so no-template plans can land on the deterministic
    filesystem scaffold before contract validation.
    """

    if planning_strict:
        return True
    return not protocol_template_assistance_enabled(context.benchmark_policy)


def _apply_shell_output_repairs(
    normalized: PlanDict,
    canonical_meta: PlanDict,
    *,
    context: PlanNormalizationContext,
    artifact_role_issue_strings: ArtifactIssueCollector | None = None,
) -> tuple[PlanDict, PlanDict]:
    """Apply bash command and output-directory repairs."""

    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="bash_redirection_output_repairs",
        repair_fn=_repair_bash_redirection_output_dirs,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="fastp_cli_flag_repairs",
        repair_fn=_repair_fastp_cli_flags,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_repair_step(
        normalized,
        canonical_meta,
        key="bash_tool_output_parent_dir_repairs",
        repair_fn=_repair_bash_tool_output_parent_dirs,
        artifact_role_issue_strings=artifact_role_issue_strings,
        selected_dir=context.selected_dir,
    )
    return normalized, canonical_meta


def normalize_plan_for_execution(
    plan: PlanDict,
    *,
    context: PlanNormalizationContext,
    stabilize_artifact_roles: ArtifactStabilizer,
    artifact_role_issue_strings: ArtifactIssueCollector,
) -> tuple[PlanDict, PlanDict, PlanDict]:
    """Normalize one candidate execution plan before validation or execution.

    Args:
        plan: Raw planner or fallback plan.
        context: Stable harness context for deterministic repairs.
        stabilize_artifact_roles: Callback that restores corrupted input/output
            bindings from the original source plan.
        artifact_role_issue_strings: Callback that renders final artifact-role
            violations into stable strings.

    Returns:
        A tuple of ``(normalized_plan, canonical_meta, featurecounts_meta)``.
    """

    source_plan = copy.deepcopy(plan or {})
    canonical_plan, canonical_meta = canonicalize_execution_plan(
        plan,
        data_root=str(context.data_root),
    )
    normalized = canonical_plan if canonical_meta.get("changed", False) else plan
    planning_strict = is_bioagentbench_planning_strict_policy(context.benchmark_policy)
    scientific_plan_mutations_enabled = _scientific_plan_mutations_enabled(
        context=context,
        planning_strict=planning_strict,
    )

    normalized, canonical_meta = _apply_initial_binding_repairs(
        normalized,
        canonical_meta,
        context=context,
        planning_strict=planning_strict,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_path_binding_repairs(
        normalized,
        canonical_meta,
        context=context,
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    if not context.freeze_completed_prefix:
        normalized, canonical_meta = _apply_analysis_specific_repairs(
            normalized,
            canonical_meta,
            context=context,
            planning_strict=planning_strict,
            scientific_plan_mutations_enabled=scientific_plan_mutations_enabled,
            artifact_role_issue_strings=artifact_role_issue_strings,
        )
    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_analysis_specific_repairs",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_shell_output_repairs(
        normalized,
        canonical_meta,
        context=context,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, canonical_meta = _apply_artifact_stabilization(
        normalized,
        canonical_meta,
        key="artifact_role_repairs_after_shell_output_repairs",
        source_plan=source_plan,
        stabilize_artifact_roles=stabilize_artifact_roles,
        artifact_role_issue_strings=artifact_role_issue_strings,
    )
    normalized, fc_meta = _apply_featurecounts_paired_mode(normalized, force=False)
    return normalized, canonical_meta, fc_meta


__all__ = [
    "PlanNormalizationContext",
    "normalize_plan_for_execution",
]
