#!/usr/bin/env python3
from __future__ import annotations
# ruff: noqa: F401,E402

import argparse
import csv
import json
import multiprocessing as mp
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Ensure pixi-managed tools are on PATH so subprocesses (runner) can find them.
_pixi_env_bin = PROJECT_ROOT / ".pixi" / "envs" / "default" / "bin"
if _pixi_env_bin.is_dir() and str(_pixi_env_bin) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(_pixi_env_bin) + os.pathsep + os.environ.get("PATH", "")
# Also add JVM bin directory for GATK/Java tools.
_pixi_jvm_bin = PROJECT_ROOT / ".pixi" / "envs" / "default" / "lib" / "jvm" / "bin"
if _pixi_jvm_bin.is_dir() and str(_pixi_jvm_bin) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(_pixi_jvm_bin) + os.pathsep + os.environ.get("PATH", "")

from bio_harness.agents.orchestrator import Orchestrator
from bio_harness.core.analysis_spec import analysis_spec_preference_profile
from bio_harness.core.benchmark_policy import (
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    SCIENTIFIC_HARNESS_POLICY,
    filter_forbidden_benchmark_sources,
    is_bioagentbench_planning_strict_policy,
    is_blind_bioagentbench_policy,
    is_official_bioagentbench_policy,
    normalize_benchmark_policy,
)
from bio_harness.core.capability_catalog import (
    capability_index,
    infer_capabilities_from_text,
    infer_tool_hints_from_text,
    load_capability_catalog,
)
from bio_harness.core.contracts import assess_plan_contract
from bio_harness.core.failure_signatures import (
    detect_plan_artifact_failure_signatures,
    detect_stream_failure_signatures,
)
from bio_harness.core.hierarchical_planning import workflow_spec_from_plan
from bio_harness.core.protocol_grounding import (
    TEMPLATE_COMPILER_TYPES,
    _apply_parameter_profile,
    _build_normalize_vcf_command,
    _build_variant_filter_command,
    _compile_rna_seq_de_plan,
    _looks_like_kraken2_db_dir,
    _resolve_metagenomics_kraken2_db,
    assess_protocol_grounding,
    deterministic_protocol_repair,
)
from bio_harness.core.pathing import discover_fastq_files_guarded
from bio_harness.core.process_monitor import collect_process_snapshot, collect_recent_outputs
from bio_harness.core.recovery_policy import (
    TOOL_EQUIVALENCE_MAP,
    build_repair_audit_entry,
    can_attempt_repair,
    classify_failure,
    classify_failure_with_context,
)
from bio_harness.core.artifact_inspectors import (
    infer_resumable_step_index,
    scan_existing_step_outputs,
)
from bio_harness.core.run_artifacts import (
    append_event,
    append_line,
    init_run_artifacts,
    write_exit,
    write_manifest,
    write_path_decisions,
    write_state,
)
from bio_harness.core.llm_backends import is_loopback_permission_error
from bio_harness.core.constants import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LIVE_PROCESS_GRACE_SECONDS,
    DEFAULT_STALL_TIMEOUT_SECONDS,
    PID_STATUS_RE,
)
from bio_harness.core.run_state import should_mark_stalled
from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.core.shell_parse import split_shell_segments
from bio_harness.core.strict_artifact_binding import rebind_direct_plan_for_strict_mode
from bio_harness.core.path_graph_store import (
    PathGraphStore,
    default_path_graph_db_path,
    deterministic_prompt_hash,
)
from bio_harness.pipeline_scripts.export_cystic_fibrosis_csv import (
    export_cystic_fibrosis_csv,
)
from bio_harness.pipeline_scripts.export_multi_model_pathway_comparison import (
    export_multi_model_pathway_comparison,
)
from bio_harness.pipeline_scripts.export_single_cell_results_csv import (
    export_single_cell_results_csv,
)
from bio_harness.workflows import (
    build_ranked_fallback_catalog,
    canonicalize_execution_plan,
    ranked_fallback_catalog_metadata,
    select_ranked_fallback_plan,
)


# ---------------------------------------------------------------------------
# Utility functions extracted to bio_harness.harness sub-package.
# Imported here so that all names remain accessible via
# ``from scripts.run_agent_e2e import X`` for backward compatibility.
# ---------------------------------------------------------------------------

from bio_harness.harness.config import (  # noqa: E402, F811
    CAPABILITY_CATALOG_PATH,
    CF_CAUSAL_VARIANT_EXPORTER,
    COMPARE_PATHWAYS_SCRIPT,
    DEFAULT_PLANNER_HEARTBEAT_SECONDS,
    DEFAULT_PLANNER_MAX_ATTEMPTS,
    MAX_COMPOSED_FALLBACK_SEGMENTS,
    MAX_REPLAN_STEP_DELTA,
    POST_COMPLETION_DRAIN_SECONDS,
    READONLY_LINKS_ROOT,
    SHARED_VARIANT_EXPORTER,
    SINGLE_CELL_RESULTS_EXPORTER,
    SKILLS_DEFINITIONS,
    SKILLS_LIBRARY,
    WORKSPACE_ROOT,
    _OUTPUT_PATH_KEYS,
    BAM_LIST_TOKEN_RE,
    HarnessConfig,
    PROVENANCE_CRITICAL_TOOLS,
    STEP_COMMAND_RE,
    STEP_EXEC_START_RE,
    STREAM_MARKER_RE,
)
from bio_harness.harness.stream_utils import (  # noqa: E402
    _all_steps_completed,
    _append_recent_marker,
    _emit,
    _extract_missing_tools_from_line,
    _extract_paths_from_text,
    _extract_pid_from_line,
    _extract_step_command_from_line,
    _extract_step_context_from_line,
    _is_pid_live,
    _normalize_contract_hint,
    _now,
    _now_utc_iso,
    _parse_log_channel,
    _stream_evidence,
)
from bio_harness.harness.plan_helpers import (  # noqa: E402
    _apply_featurecounts_paired_mode,
    _apply_repaired_plan_with_resume,
    _as_bool_token,
    _assess_plan_semantic_guards,
    _compose_plan_segments,
    _extract_bam_list_paths_from_plan,
    _extract_csv_output_from_command,
    _extract_selection_pipeline_id,
    _failed_tool_name,
    _first_failed_step_number,
    _is_actionable_executable_plan,
    _is_probe_only_bash,
    _missing_local_scripts_for_plan,
    _normalize_capability_list,
    _normalize_steps,
    _plan_completed_prefix_len,
    _plan_summary_for_repair_prompt,
    _relocate_undocumented_output_arguments_to_final_deliverables,
    _renumber_plan_steps,
    _repair_scope_for_run,
    _signature_contains,
    _step_fingerprint,
)
from bio_harness.harness.path_utils import (  # noqa: E402
    _collect_planned_output_paths,
    _discover_fastq_files,
    _extract_paths_from_argument_value,
    _extract_paths_from_command,
    _iter_pathlike_values,
    _looks_like_path_token,
    _normalize_plan_path_text,
    _path_has_positive_group_evidence,
    _path_text_has_group_evidence,
    _path_within_any_root,
    _path_within_root,
    _redirection_parent_dirs,
    _redirect_output_paths_to_selected_dir,
    _repair_workspace_placeholder_paths_in_plan,
    _resolve_candidate_path,
    _resolve_existing_input_path,
    _text_mentions_group_token,
)
from bio_harness.harness.repair_context import (  # noqa: E402
    build_repair_context,
)
from bio_harness.harness.sample_groups import (  # noqa: E402
    _argument_key_mentions_group,
    _ensure_group_tracking,
    _extract_group_hinted_paths_from_command,
    _group_aliases,
    _infer_observed_groups_from_plan_artifacts,
    _mark_group_missing_signal,
    _mark_group_observed,
    _normalize_group_label,
    _note_group_observation_source,
    _path_mentions_group,
    _reconcile_missing_sample_groups,
)
from bio_harness.harness.contract_utils import (  # noqa: E402
    _capability_specs_from_catalog,
    _clean_stale_tmp_cache_paths,
    _discover_fastq_pair_map,
    _extract_fastq_mate,
    _extract_fastq_sample_tag,
    _extract_group_tags_from_request_text,
    _extract_reference_paths_from_plan,
    _extract_sample_tags_from_plan,
    _find_alias_reference,
    _find_reference_candidate,
    _find_reference_candidate_in_roots,
    _find_workspace_reference,
    _infer_evolution_step_sample_tag,
    _infer_request_contract,
    _is_empty_contract,
    _is_exec_tool_available,
    _looks_like_fasta_path,
    _looks_like_task_local_generated_reference,
    _missing_exec_tools_for_plan,
    _missing_input_paths_for_plan,
    _pick_reference_paths_from_text,
    _pixi_bin_dir,
    _plan_contains_splicing_steps,
    _planned_converted_gtf_path,
    _repair_missing_references_in_plan,
    _repair_requested_references_and_index_bases_in_plan,
    _required_tool_hints_from_text,
    _resolve_reference_paths,
    _resolve_reference_paths_for_template_fallback,
    _resolve_sample_pair,
    _sample_tag_kind,
    _stable_index_base_for_tool,
    _stable_quant_index_path_for_tool,
    _tool_hint_aliases,
    _verify_run_outputs,
    _which_with_pixi,
    _workspace_reference_alias_candidates,
)
from bio_harness.harness.plan_repair import (  # noqa: E402
    _canonical_evolution_bam_path,
    _discover_cystic_fibrosis_inputs,
    _evolution_variant_repair_settings,
    _is_cystic_fibrosis_task,
    _preflight_execution_issues,
    _quote_shell_segments,
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
    _repair_quantification_export_command,
    _repair_quantification_export_segment,
    _repair_rna_seq_de_plan_with_assay_compiler,
    _repair_shared_variant_csv_exports,
    _repair_shared_variant_csv_exports_with_analysis_spec,
    _repair_single_cell_export_tail,
    _repair_single_cell_qc_thresholds,
    _repair_variant_annotation_impact_filter,
    _resolve_shell_path,
    _shared_variant_export_settings_from_analysis_spec,
    _split_shell_command_segments,
)

_TOOL_REGISTRY = default_tool_registry()
HEAVY_TOOL_NAMES = _TOOL_REGISTRY.heavy_tools()
PLAN_INPUT_PATH_KEYS = _TOOL_REGISTRY.all_input_path_keys()
PLAN_TOOL_EXEC_HINTS = _TOOL_REGISTRY.tools_with_exec_hints()
TOOL_STALL_GRACE_HINTS = {
    tool_name: _TOOL_REGISTRY.stall_grace_for(tool_name)
    for tool_name in _TOOL_REGISTRY.known_tool_names()
    if _TOOL_REGISTRY.stall_grace_for(tool_name) > 0
}


def _json_safe_default(value: Any) -> Any:
    """Return a JSON-serializable representation for prompt context values.

    Args:
        value: Arbitrary object included in repair or planning prompt context.

    Returns:
        A value accepted by ``json.dumps``.

    Raises:
        TypeError: If *value* cannot be normalized safely.
    """
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        return as_dict()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps_safe(payload: Any, *, indent: int | None = None, sort_keys: bool = False) -> str:
    """Serialize prompt payloads with support for repo-specific helper objects.

    Args:
        payload: Object to serialize.
        indent: Optional indentation width.
        sort_keys: Whether to sort dictionary keys.

    Returns:
        JSON text.
    """
    return json.dumps(
        payload,
        indent=indent,
        sort_keys=sort_keys,
        default=_json_safe_default,
    )
from bio_harness.harness.deliverables import (  # noqa: E402
    _extract_deseq_rows_for_export,
    _extract_deliverable_output_path_from_protocol_grounding,
    _extract_quantification_counts_for_export,
    _materialize_cystic_fibrosis_deliverable,
    _materialize_deseq_deliverable,
    _materialize_multi_model_dge_pathway_deliverable,
    _materialize_single_cell_deliverable,
    _materialize_transcript_quant_deliverable,
)


def _planner_worker_think(
    prompt: str,
    model_name: str | None,
    host: str | None,
    llm_backend: str | None,
    out_conn: Any,
    analysis_spec: dict[str, Any] | None = None,
    planner_mode: str = "auto",
    seed_plan: dict[str, Any] | None = None,
    planner_trace_dir: str | None = None,
    planner_trace_context: dict[str, Any] | None = None,
    model_override: str | None = None,
    available_skills_metadata_override: list[dict[str, Any]] | None = None,
) -> None:
    try:
        worker = Orchestrator(
            skills_dir=SKILLS_DEFINITIONS,
            skill_library_dir=SKILLS_LIBRARY,
            model_name=model_name,
            host=host,
            llm_backend=llm_backend,
            planner_trace_dir=planner_trace_dir,
            planner_trace_context=planner_trace_context,
        )
        plan = worker.think(
            prompt,
            analysis_spec=analysis_spec,
            planner_mode=planner_mode,
            seed_plan=seed_plan if isinstance(seed_plan, dict) else None,
            model_override=model_override,
            available_skills_metadata_override=available_skills_metadata_override,
        )
        payload = {"ok": True, "plan": plan}
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "exception_type": type(exc).__name__,
        }
    try:
        out_conn.send(payload)
    except Exception:
        pass
    finally:
        try:
            out_conn.close()
        except Exception:
            pass


@dataclass
class _ExecutionMonitorState:
    """Tracks transient execution-loop state for a running plan."""

    last_progress_ts: float
    active_step_started_ts: float
    last_heartbeat_print: float = 0.0
    active_pid: int | None = None
    active_step_id: int | None = None
    active_tool_name: str = ""
    active_command: str = ""
    active_phase: str = ""
    active_phase_started_ts: float = 0.0
    first_pid_observed: bool = False
    saw_runner_start: bool = False
    last_tree_cpu_seconds: float = 0.0
    last_cpu_progress_ts: float = 0.0
    latest_cpu_progressing: bool = False


__all__ = [name for name in globals() if not name.startswith("__")]
