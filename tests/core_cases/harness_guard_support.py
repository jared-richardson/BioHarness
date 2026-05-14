from __future__ import annotations
# ruff: noqa: F401

import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from bio_harness.core.strict_artifact_binding import bind_step_spec_for_strict_mode
from scripts.run_agent_e2e import (
    AgentE2EHarness,
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    HarnessConfig,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    _extract_group_tags_from_request_text,
    _extract_csv_output_from_command,
    _extract_sample_tags_from_plan,
    _first_failed_step_number,
    _find_workspace_reference,
    _missing_input_paths_for_plan,
    _missing_local_scripts_for_plan,
    _assess_plan_semantic_guards,
    _preflight_execution_issues,
    _repair_scope_for_run,
    _repair_bash_redirection_output_dirs,
    _repair_bash_tool_output_parent_dirs,
    _repair_evolution_spades_reference_usage,
    _repair_fastp_cli_flags,
    _repair_metagenomics_trimmed_read_usage,
    _repair_cystic_fibrosis_csv_exports_with_analysis_spec,
    _collect_planned_output_paths,
    _repair_deseq_bash_run_to_skill,
    _repair_rna_seq_de_plan_with_assay_compiler,
    _repair_multi_model_compare_pathways_commands,
    _repair_quantification_count_exports,
    _repair_missing_fastq_inputs_in_plan,
    _repair_requested_references_and_index_bases_in_plan,
    _repair_shared_variant_csv_exports,
    _repair_shared_variant_csv_exports_with_analysis_spec,
    _repair_single_cell_export_tail,
    _materialize_cystic_fibrosis_deliverable,
    _materialize_deseq_deliverable,
    _materialize_single_cell_deliverable,
    _repair_workspace_placeholder_paths_in_plan,
    _materialize_transcript_quant_deliverable,
    _extract_deseq_rows_for_export,
    _extract_deliverable_output_path_from_protocol_grounding,
    _resolve_reference_paths_for_template_fallback,
    _stable_index_base_for_tool,
)


__all__ = [name for name in globals() if not name.startswith("__")]
