"""Strict-mode artifact binding for benchmark-blind BioAgentBench runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from bio_harness.core.benchmark_policy import (
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    SCIENTIFIC_HARNESS_POLICY,
    normalize_benchmark_policy,
)
from bio_harness.core.strict_artifact_binding_command_builders import (
    _build_germline_verify_command as _build_germline_verify_command,
    _build_rna_seq_de_alignment_command as _build_rna_seq_de_alignment_command,
    _build_rna_seq_de_export_command as _build_rna_seq_de_export_command,
    _copy_step_with_arguments as _copy_step_with_arguments,
)
from bio_harness.core.strict_artifact_binding_benchmark_helpers import (
    _benchmark_task_data_dir as _benchmark_task_data_dir,
    _benchmark_task_reference_dir as _benchmark_task_reference_dir,
    _build_metagenomics_command as _build_metagenomics_command,
    _build_multi_model_compare_command as _build_multi_model_compare_command,
    _build_multi_model_verify_command as _build_multi_model_verify_command,
    _build_phylogenetics_command as _build_phylogenetics_command,
    _build_viral_metagenomics_command as _build_viral_metagenomics_command,
    _discover_multi_model_pathway_inputs as _discover_multi_model_pathway_inputs,
    _discover_phylogenetics_input_fasta as _discover_phylogenetics_input_fasta,
    _discover_primary_fastq_pair as _discover_primary_fastq_pair,
    _infer_selected_dir_from_payload as _infer_selected_dir_from_payload,
    _selected_dir_from_analysis_spec as _selected_dir_from_analysis_spec,
)
from bio_harness.core.strict_artifact_binding_benchmark_binders import (
    _bind_metagenomics_classification as _bind_metagenomics_classification,
    _bind_multi_model_dge_pathway as _bind_multi_model_dge_pathway,
    _bind_phylogenetics as _bind_phylogenetics,
    _bind_viral_metagenomics as _bind_viral_metagenomics,
)
from bio_harness.core.strict_artifact_binding_structured_assay_binders import (
    _bind_germline_variant_calling as _bind_germline_variant_calling,
    _bind_rna_seq_differential_expression as _bind_rna_seq_differential_expression,
    _bind_single_cell_rna_seq as _bind_single_cell_rna_seq,
)
from bio_harness.core.strict_artifact_binding_variant_binders import (
    _bind_bacterial_evolution_variant_calling as _bind_bacterial_evolution_variant_calling,
    _bind_variant_annotation as _bind_variant_annotation,
)
from bio_harness.core.strict_artifact_binding_direct_steps import (
    _EVOLUTION_ANNOTATE_OBJECTIVE as _EVOLUTION_ANNOTATE_OBJECTIVE,
    _build_cystic_fibrosis_clinvar_command as _build_cystic_fibrosis_clinvar_command,
    _build_cystic_fibrosis_export_command as _build_cystic_fibrosis_export_command,
    _build_cystic_fibrosis_filter_command as _build_cystic_fibrosis_filter_command,
    _classify_cystic_fibrosis_bash_role as _classify_cystic_fibrosis_bash_role,
    _fallback_cystic_fibrosis_role as _fallback_cystic_fibrosis_role,
    _infer_direct_branch_id as _infer_direct_branch_id,
    _infer_evolution_direct_bash_objective as _infer_evolution_direct_bash_objective,
    _infer_evolution_direct_branch_id as _infer_evolution_direct_branch_id,
    _normalize_cystic_fibrosis_bash_command as _normalize_cystic_fibrosis_bash_command,
)
from bio_harness.core.strict_artifact_binding_paths import (
    CysticFibrosisArtifactPaths as CysticFibrosisArtifactPaths,
    GermlineVariantArtifactPaths as GermlineVariantArtifactPaths,
    RnaSeqDeArtifactPaths as RnaSeqDeArtifactPaths,
    SingleCellArtifactPaths as SingleCellArtifactPaths,
    _build_cystic_fibrosis_paths as _build_cystic_fibrosis_paths,
    _build_germline_variant_paths as _build_germline_variant_paths,
    _build_rna_seq_de_paths as _build_rna_seq_de_paths,
    _build_single_cell_paths as _build_single_cell_paths,
    _discover_first_existing_path as _discover_first_existing_path,
    _read_rna_seq_sample_rows as _read_rna_seq_sample_rows,
)

@dataclass(frozen=True)
class StrictArtifactBindingContext:
    """Trusted runtime context for strict benchmark artifact binding.

    In strict benchmark mode, the model should decide the workflow structure
    while the harness owns the filesystem scaffold. This context carries the
    deterministic run directory and the minimal semantic labels needed to bind a
    step onto that scaffold without trusting model-generated paths.
    """

    analysis_type: str
    tool_name: str
    branch_id: str
    objective: str
    selected_dir: Path | None
    analysis_spec: Dict[str, Any]


def _infer_helper_backed_direct_objective(
    *,
    analysis_type: str,
    tool_name: str,
    command: str,
) -> str:
    """Recover helper-backed objectives for strict direct plans with empty commands."""

    if tool_name != "bash_run" or command:
        return ""

    if analysis_type == "metagenomics_classification":
        return "classify the paired-end metagenomics reads and write the requested kraken-style report"
    if analysis_type == "phylogenetics":
        return "infer a phylogenetic tree from the provided sequences and write the requested newick output"
    return ""


def _fallback_evolution_direct_branch_id(*, tool_name: str, tool_ordinal: int) -> str:
    """Infer evolution branch identity from deterministic workflow order."""

    if tool_name in {"bwa_mem_align", "freebayes_call"}:
        return {
            1: "ancestor",
            2: "evol1",
            3: "evol2",
        }.get(tool_ordinal, "")
    if tool_name == "snpeff_annotate":
        return {
            1: "evol1",
            2: "evol2",
        }.get(tool_ordinal, "")
    return ""


def _scientific_helper_bash_binding_allowed(
    *,
    step_spec: Dict[str, Any],
    workflow_step: Dict[str, Any],
    analysis_spec: Dict[str, Any] | None,
) -> bool:
    """Return whether an empty scientific bash step may bind a declared helper."""

    if str((step_spec or {}).get("tool_name", "") or "").strip().lower() != "bash_run":
        return False
    args = step_spec.get("arguments", {}) if isinstance(step_spec.get("arguments", {}), dict) else {}
    if str(args.get("command", "") or "").strip():
        return False
    hints = workflow_step.get("parameter_hints", {})
    if isinstance(hints, dict) and str(hints.get("helper_script", "") or "").strip():
        return True
    for entry in (analysis_spec or {}).get("parameter_profile", []) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("tool_name", "") or "").strip().lower() != "bash_run":
            continue
        settings = entry.get("settings", {})
        if isinstance(settings, dict) and str(settings.get("helper_script", "") or "").strip():
            return True
    return False


def _direct_workflow_step_for_strict_binding(
    step_spec: Dict[str, Any],
    *,
    analysis_spec: Dict[str, Any] | None = None,
    tool_ordinal: int = 0,
) -> Dict[str, Any]:
    """Build pseudo workflow context for rebinding direct strict plans."""

    tool_name = str(step_spec.get("tool_name", "") or "").strip()
    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    objective = ""
    branch_id = _infer_direct_branch_id(step_spec)
    args = step_spec.get("arguments", {}) if isinstance(step_spec.get("arguments", {}), dict) else {}
    command = str(args.get("command", "") or "").strip()

    if analysis_type == "bacterial_evolution_variant_calling":
        if tool_name == "bash_run":
            objective = _infer_evolution_direct_bash_objective(command)
            branch_id = _infer_evolution_direct_branch_id(command, branch_id)
        elif tool_name == "snpeff_annotate" and branch_id.startswith("evol"):
            objective = _EVOLUTION_ANNOTATE_OBJECTIVE
        if not branch_id:
            branch_id = _fallback_evolution_direct_branch_id(
                tool_name=tool_name,
                tool_ordinal=tool_ordinal,
            )
        if tool_name == "snpeff_annotate" and branch_id.startswith("evol") and not objective:
            objective = _EVOLUTION_ANNOTATE_OBJECTIVE
    if not objective:
        objective = _infer_helper_backed_direct_objective(
            analysis_type=analysis_type,
            tool_name=tool_name,
            command=command,
        )

    return {
        "step_id": int(step_spec.get("step_id", 0) or 0),
        "tool_name": tool_name,
        "objective": objective,
        "branch_id": branch_id,
        "depends_on": [],
        "parameter_hints": {},
        "downstream_constraints": [],
    }


def make_strict_artifact_binding_context(
    *,
    step_spec: Dict[str, Any],
    workflow_step: Dict[str, Any],
    analysis_spec: Dict[str, Any] | None = None,
) -> StrictArtifactBindingContext:
    """Build the trusted strict-binding context for a planner-produced step."""

    analysis_type = str((analysis_spec or {}).get("analysis_type", "") or "").strip().lower()
    tool_name = str(step_spec.get("tool_name", "") or "").strip().lower()
    branch_id = str(workflow_step.get("branch_id", "") or "").strip().lower()
    objective = str(workflow_step.get("objective", "") or "").strip().lower()
    selected_dir = _selected_dir_from_analysis_spec(analysis_spec) or _infer_selected_dir_from_payload(
        step_spec,
        workflow_step,
    )
    return StrictArtifactBindingContext(
        analysis_type=analysis_type,
        tool_name=tool_name,
        branch_id=branch_id,
        objective=objective,
        selected_dir=selected_dir,
        analysis_spec=dict(analysis_spec or {}),
    )


_STRICT_BINDERS: Dict[str, Callable[[Dict[str, Any], StrictArtifactBindingContext], Dict[str, Any]]] = {
    "bacterial_evolution_variant_calling": _bind_bacterial_evolution_variant_calling,
    "germline_variant_calling": _bind_germline_variant_calling,
    "metagenomics_classification": _bind_metagenomics_classification,
    "phylogenetics": _bind_phylogenetics,
    "rna_seq_differential_expression": _bind_rna_seq_differential_expression,
    "single_cell_rna_seq": _bind_single_cell_rna_seq,
    "multi_model_dge_pathway": _bind_multi_model_dge_pathway,
    "variant_annotation": _bind_variant_annotation,
    "viral_metagenomics": _bind_viral_metagenomics,
}


def bind_step_spec_for_strict_mode(
    *,
    step_spec: Dict[str, Any],
    workflow_step: Dict[str, Any],
    analysis_spec: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Bind a model-produced step onto the deterministic strict-mode scaffold.

    The binder is keyed by analysis type, tool, branch label, and selected run
    directory. New assay types can register additional binders without forcing
    planner code to learn or preserve filesystem structure.
    """

    ctx = make_strict_artifact_binding_context(
        step_spec=step_spec,
        workflow_step=workflow_step,
        analysis_spec=analysis_spec,
    )
    binder = _STRICT_BINDERS.get(ctx.analysis_type)
    if binder is None:
        return dict(step_spec if isinstance(step_spec, dict) else {})
    return binder(step_spec, ctx)


def bind_step_spec_for_benchmark_policy(
    *,
    step_spec: Dict[str, Any],
    workflow_step: Dict[str, Any],
    analysis_spec: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Bind deterministic scaffold paths allowed by the active policy.

    Planning-strict runs use the full benchmark-blind scaffold. Scientific
    harness runs keep model-authored ``bash_run`` content intact for the model
    comparison, but still canonicalize non-bash wrapper paths so producer and
    consumer closure has stable filesystem anchors.
    """

    policy = normalize_benchmark_policy((analysis_spec or {}).get("benchmark_policy"))
    if policy == BIOAGENTBENCH_PLANNING_STRICT_POLICY:
        return bind_step_spec_for_strict_mode(
            step_spec=step_spec,
            workflow_step=workflow_step,
            analysis_spec=analysis_spec,
        )
    if policy != SCIENTIFIC_HARNESS_POLICY:
        return dict(step_spec if isinstance(step_spec, dict) else {})
    if str((step_spec or {}).get("tool_name", "") or "").strip().lower() == "bash_run":
        if _scientific_helper_bash_binding_allowed(
            step_spec=step_spec,
            workflow_step=workflow_step,
            analysis_spec=analysis_spec,
        ):
            return bind_step_spec_for_strict_mode(
                step_spec=step_spec,
                workflow_step=workflow_step,
                analysis_spec=analysis_spec,
            )
        return dict(step_spec if isinstance(step_spec, dict) else {})
    return bind_step_spec_for_strict_mode(
        step_spec=step_spec,
        workflow_step=workflow_step,
        analysis_spec=analysis_spec,
    )


def rebind_direct_plan_for_strict_mode(
    plan: Dict[str, Any],
    *,
    analysis_spec: Dict[str, Any] | None = None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Rebind direct planner outputs onto the strict deterministic scaffold."""

    if not isinstance(plan, dict):
        return plan, {"changed": False, "why": "plan_missing"}
    steps = plan.get("plan", [])
    if not isinstance(steps, list) or not steps:
        return plan, {"changed": False, "why": "plan_missing"}

    rebound_steps: list[Dict[str, Any]] = []
    changed_step_ids: list[int] = []
    tool_ordinals: Dict[str, int] = {}
    for idx, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, dict):
            rebound_steps.append(raw_step)
            continue
        step = dict(raw_step)
        step["step_id"] = int(step.get("step_id", idx))
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        tool_ordinal = tool_ordinals.get(tool_name, 0) + 1
        tool_ordinals[tool_name] = tool_ordinal
        workflow_step = _direct_workflow_step_for_strict_binding(
            step,
            analysis_spec=analysis_spec,
            tool_ordinal=tool_ordinal,
        )
        rebound = bind_step_spec_for_strict_mode(
            step_spec=step,
            workflow_step=workflow_step,
            analysis_spec=analysis_spec,
        )
        if rebound != step:
            changed_step_ids.append(int(step.get("step_id", idx)))
        rebound_steps.append(rebound)

    if not changed_step_ids:
        return plan, {"changed": False, "why": "already_bound"}

    patched = dict(plan)
    patched["plan"] = rebound_steps
    return patched, {
        "changed": True,
        "why": "strict_direct_plan_rebinding",
        "changed_step_ids": changed_step_ids,
    }
