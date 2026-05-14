"""Plan repair functions for the E2E harness.

Each repair function takes a plan dict and returns (patched_plan, metadata)
or modifies the plan in place.  These are called by the harness during the
repair loop to fix common LLM plan generation issues.
"""
from __future__ import annotations

from typing import Any

from bio_harness.harness import plan_repair_cystic_fibrosis as _plan_repair_cystic_fibrosis
from bio_harness.harness import plan_repair_analysis_workflows as _plan_repair_analysis_workflows
from bio_harness.harness import plan_repair_bash_outputs as _plan_repair_bash_outputs
from bio_harness.harness import plan_repair_evolution as _plan_repair_evolution
from bio_harness.harness import plan_repair_fastq_inputs as _plan_repair_fastq_inputs
from bio_harness.harness import plan_repair_preflight as _plan_repair_preflight
from bio_harness.harness import plan_repair_quant_exports as _plan_repair_quant_exports
from bio_harness.harness import plan_repair_shell_metagenomics as _plan_repair_shell_metagenomics
from bio_harness.harness import plan_repair_shared_variants as _plan_repair_shared_variants
from bio_harness.harness import plan_repair_single_cell as _plan_repair_single_cell
_discover_cystic_fibrosis_inputs = _plan_repair_cystic_fibrosis._discover_cystic_fibrosis_inputs
_canonical_evolution_bam_path = _plan_repair_evolution._canonical_evolution_bam_path
_is_cystic_fibrosis_task = _plan_repair_cystic_fibrosis._is_cystic_fibrosis_task
_looks_like_inline_multi_model_compare_pathways_command = (
    _plan_repair_analysis_workflows._looks_like_inline_multi_model_compare_pathways_command
)
_quote_shell_segments = _plan_repair_shell_metagenomics._quote_shell_segments
_repair_bash_redirection_output_dirs = _plan_repair_bash_outputs._repair_bash_redirection_output_dirs
_repair_bash_tool_output_parent_dirs = _plan_repair_bash_outputs._repair_bash_tool_output_parent_dirs
_repair_cystic_fibrosis_csv_exports_with_analysis_spec = _plan_repair_cystic_fibrosis._repair_cystic_fibrosis_csv_exports_with_analysis_spec
_repair_deseq_bash_run_to_skill = _plan_repair_analysis_workflows._repair_deseq_bash_run_to_skill
_repair_evolution_alignment_path_bindings = _plan_repair_evolution._repair_evolution_alignment_path_bindings
_repair_evolution_missing_variant_branches = _plan_repair_evolution._repair_evolution_missing_variant_branches
_repair_evolution_spades_reference_usage = _plan_repair_evolution._repair_evolution_spades_reference_usage
_repair_fastp_cli_flags = _plan_repair_shell_metagenomics._repair_fastp_cli_flags
_repair_missing_fastq_inputs_in_plan = _plan_repair_fastq_inputs._repair_missing_fastq_inputs_in_plan
_repair_metagenomics_prebuilt_db_bindings = _plan_repair_shell_metagenomics._repair_metagenomics_prebuilt_db_bindings
_repair_metagenomics_trimmed_read_usage = _plan_repair_shell_metagenomics._repair_metagenomics_trimmed_read_usage
_repair_multi_model_compare_pathways_commands = (
    _plan_repair_analysis_workflows._repair_multi_model_compare_pathways_commands
)
_preflight_execution_issues = _plan_repair_preflight._preflight_execution_issues
_repair_quantification_count_exports = _plan_repair_quant_exports._repair_quantification_count_exports
_repair_quantification_export_command = _plan_repair_quant_exports._repair_quantification_export_command
_repair_quantification_export_segment = _plan_repair_quant_exports._repair_quantification_export_segment
_repair_rna_seq_de_plan_with_assay_compiler = (
    _plan_repair_analysis_workflows._repair_rna_seq_de_plan_with_assay_compiler
)
_repair_shared_variant_csv_exports_with_analysis_spec = _plan_repair_shared_variants._repair_shared_variant_csv_exports_with_analysis_spec
_repair_single_cell_export_tail = _plan_repair_single_cell._repair_single_cell_export_tail
_repair_single_cell_qc_thresholds = _plan_repair_single_cell._repair_single_cell_qc_thresholds
_repair_variant_annotation_impact_filter = (
    _plan_repair_analysis_workflows._repair_variant_annotation_impact_filter
)
_resolve_shell_path = _plan_repair_shell_metagenomics._resolve_shell_path
_split_shell_command_segments = _plan_repair_shell_metagenomics._split_shell_command_segments
_shared_variant_export_settings_from_analysis_spec = _plan_repair_shared_variants._shared_variant_export_settings_from_analysis_spec
_evolution_variant_repair_settings = _plan_repair_shared_variants._evolution_variant_repair_settings


# ---------------------------------------------------------------------------
# Shared variant CSV export repairs
# ---------------------------------------------------------------------------


def _repair_shared_variant_csv_exports(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _repair_shared_variant_csv_exports_with_analysis_spec(plan, analysis_spec=None)
