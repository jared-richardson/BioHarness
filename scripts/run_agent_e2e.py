#!/usr/bin/env python3
from __future__ import annotations
# ruff: noqa: F401

import argparse
import os
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_agent_e2e_support import (  # noqa: E402
    BIOAGENTBENCH_PLANNING_STRICT_POLICY,
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_LIVE_PROCESS_GRACE_SECONDS,
    DEFAULT_STALL_TIMEOUT_SECONDS,
    OFFICIAL_BIOAGENTBENCH_POLICY,
    READONLY_LINKS_ROOT,
    SCIENTIFIC_HARNESS_POLICY,
    WORKSPACE_ROOT,
    _now_utc_iso,
    append_line,
    assess_protocol_grounding,
    canonicalize_execution_plan,
    default_path_graph_db_path,
    deterministic_protocol_repair,
    json,
    mp,
    normalize_benchmark_policy,
    _json_dumps_safe,
    _infer_observed_groups_from_plan_artifacts,
    _mark_group_missing_signal,
    _mark_group_observed,
    _normalize_group_label,
    _reconcile_missing_sample_groups,
    shutil,
)
from scripts.run_agent_e2e_harness import AgentE2EHarness  # noqa: E402
from scripts.run_agent_e2e_research_support import (  # noqa: E402
    handle_explicit_research_prompt,
    is_explicit_research_prompt,
)
from bio_harness.core.request_scope import infer_request_data_root  # noqa: E402
from bio_harness.core.failure_reporting import build_failure_diagnosis  # noqa: E402
from bio_harness.harness.config import HarnessConfig  # noqa: E402
from bio_harness.harness.deliverables import (  # noqa: E402
    _extract_deseq_rows_for_export,
    _extract_deliverable_output_path_from_protocol_grounding,
    _materialize_cystic_fibrosis_deliverable,
    _materialize_deseq_deliverable,
    _materialize_single_cell_deliverable,
    _materialize_transcript_quant_deliverable,
)
from bio_harness.harness.path_utils import (  # noqa: E402
    _collect_planned_output_paths,
    _repair_workspace_placeholder_paths_in_plan,
)
from bio_harness.harness.contract_utils import (  # noqa: E402
    _extract_group_tags_from_request_text,
    _extract_sample_tags_from_plan,
    _find_workspace_reference,
    _missing_input_paths_for_plan,
    _repair_requested_references_and_index_bases_in_plan,
    _resolve_reference_paths_for_template_fallback,
    _stable_index_base_for_tool,
)
from bio_harness.harness.plan_helpers import (  # noqa: E402
    _apply_repaired_plan_with_resume,
    _assess_plan_semantic_guards,
    _extract_csv_output_from_command,
    _first_failed_step_number,
    _missing_local_scripts_for_plan,
    _repair_scope_for_run,
)
from bio_harness.harness.plan_repair import (  # noqa: E402
    _preflight_execution_issues,
    _repair_bash_redirection_output_dirs,
    _repair_bash_tool_output_parent_dirs,
    _repair_cystic_fibrosis_csv_exports_with_analysis_spec,
    _repair_deseq_bash_run_to_skill,
    _repair_evolution_spades_reference_usage,
    _repair_fastp_cli_flags,
    _repair_metagenomics_trimmed_read_usage,
    _repair_missing_fastq_inputs_in_plan,
    _repair_multi_model_compare_pathways_commands,
    _repair_quantification_count_exports,
    _repair_rna_seq_de_plan_with_assay_compiler,
    _repair_shared_variant_csv_exports,
    _repair_shared_variant_csv_exports_with_analysis_spec,
    _repair_single_cell_export_tail,
)


def _path_is_within_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _parse_args() -> HarnessConfig:
    parser = argparse.ArgumentParser(
        description=(
            "CLI end-to-end harness for BioHarness backend (plan + execute + heartbeat + auto-recovery) "
            "without Streamlit UI."
        )
    )
    parser.add_argument("--prompt", type=str, default="", help="User request prompt for planning.")
    parser.add_argument("--prompt-file", type=str, default="", help="Path to a text file containing the prompt.")
    parser.add_argument("--plan-file", type=str, default="", help="Optional JSON plan file to execute directly.")
    parser.add_argument("--selected-dir", type=str, default=str(WORKSPACE_ROOT), help="Execution working directory.")
    parser.add_argument(
        "--data-root",
        type=str,
        default=str(WORKSPACE_ROOT / "inputs_readonly"),
        help="Data root used for preflight and canonicalization.",
    )
    parser.add_argument(
        "--analysis-type",
        type=str,
        default="",
        help="Optional explicit analysis type from a trusted manifest.",
    )
    parser.add_argument("--max-repairs", type=int, default=3, help="Maximum automatic repair cycles.")
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS, help="Heartbeat print interval.")
    parser.add_argument("--stall-timeout-seconds", type=int, default=DEFAULT_STALL_TIMEOUT_SECONDS, help="Stall timeout.")
    parser.add_argument(
        "--live-process-grace-seconds",
        type=int,
        default=DEFAULT_LIVE_PROCESS_GRACE_SECONDS,
        help="Stall grace while process is alive.",
    )
    parser.add_argument("--model-name", type=str, default=os.getenv("BIO_HARNESS_MODEL", ""), help="Override model name.")
    parser.add_argument(
        "--llm-backend",
        type=str,
        default=os.getenv("BIO_HARNESS_LLM_BACKEND", os.getenv("BIO_HARNESS_LLM_PROVIDER", "ollama")),
        help="LLM backend: ollama, ollama_openai, openai_compatible, vllm, or mlx.",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv(
            "BIO_HARNESS_OLLAMA_HOST",
            os.getenv(
                "BIO_HARNESS_OLLAMA_OPENAI_BASE_URL",
                os.getenv(
                    "BIO_HARNESS_MLX_BASE_URL",
                    os.getenv("BIO_HARNESS_VLLM_BASE_URL", os.getenv("BIO_HARNESS_OPENAI_BASE_URL", "")),
                ),
            ),
        ),
        help="Override backend base URL. For OpenAI-compatible servers, root and /v1 layouts are both probed.",
    )
    parser.add_argument(
        "--auto-install-missing-tools",
        dest="auto_install_missing_tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Attempt pixi auto-install for missing tools. Defaults on in scientific_harness mode.",
    )
    parser.add_argument(
        "--auto-setup-isolated-tools",
        dest="auto_setup_isolated_tools",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Attempt isolated-tool recipe setup for recoverable missing tools. Defaults on in scientific_harness mode.",
    )
    parser.add_argument("--no-replan", action="store_true", help="Disable failure-context replanning.")
    parser.add_argument("--no-canonicalize", action="store_true", help="Disable canonicalization repair action.")
    parser.add_argument(
        "--benchmark-policy",
        type=str,
        choices=sorted(
            {
                BIOAGENTBENCH_PLANNING_STRICT_POLICY,
                OFFICIAL_BIOAGENTBENCH_POLICY,
                SCIENTIFIC_HARNESS_POLICY,
            }
        ),
        default=normalize_benchmark_policy(os.getenv("BIO_HARNESS_BENCHMARK_POLICY", SCIENTIFIC_HARNESS_POLICY)),
        help=(
            "Benchmark assistance policy: scientific_harness keeps protocol compilers; "
            "official_bioagentbench hides benchmark recipes/results while allowing limited generic normalization; "
            "bioagentbench_planning_strict keeps the blind setup but disables compiler-driven plan rescue."
        ),
    )
    parser.add_argument("--result-json", type=str, default="", help="Optional path to write machine-readable result JSON.")
    parser.add_argument("--quiet", action="store_true", help="Reduce harness log output.")
    parser.add_argument("--print-plan", action="store_true", help="Print final plan JSON before execution.")
    parser.add_argument(
        "--execution-mode",
        type=str,
        choices=("batch", "stepwise"),
        default=str(os.getenv("BIO_HARNESS_EXECUTION_MODE", "batch") or "batch").strip().lower(),
        help="Execution mode: batch plans the whole workflow up front; stepwise plans one next step at a time.",
    )
    parser.add_argument(
        "--path-graph-db",
        type=str,
        default="",
        help="Optional path for SQLite path graph DB (default: <selected-dir>/knowledge/path_graph.sqlite).",
    )
    parser.add_argument("--path-graph-user-key", type=str, default="default", help="User key for path graph preferences.")
    parser.add_argument("--path-graph-scope", type=str, default="global", help="Preference scope for path graph preferences.")
    parser.add_argument(
        "--path-graph-persist-preference-updates",
        action="store_true",
        help="Persist preference updates from successful runs (explicit opt-in).",
    )
    args = parser.parse_args()

    prompt = (args.prompt or "").strip()
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    if not prompt and not args.plan_file:
        parser.error("Provide --prompt/--prompt-file or --plan-file.")

    selected_dir = Path(args.selected_dir).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    if prompt and not args.plan_file:
        inferred_request_root = infer_request_data_root(prompt, project_root=PROJECT_ROOT)
        if inferred_request_root:
            inferred_path = Path(inferred_request_root).expanduser().resolve()
            default_data_roots = {
                WORKSPACE_ROOT.resolve(),
                (WORKSPACE_ROOT / "inputs_readonly").resolve(),
                selected_dir.resolve(),
            }
            if data_root in default_data_roots or _path_is_within_root(inferred_path, data_root):
                data_root = inferred_path
    selected_dir.mkdir(parents=True, exist_ok=True)
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    READONLY_LINKS_ROOT.mkdir(parents=True, exist_ok=True)
    graph_db = (
        Path(args.path_graph_db).expanduser().resolve()
        if str(args.path_graph_db).strip()
        else default_path_graph_db_path(selected_dir)
    )

    normalized_policy = normalize_benchmark_policy(args.benchmark_policy)
    default_self_heal = normalized_policy == SCIENTIFIC_HARNESS_POLICY

    return HarnessConfig(
        prompt=prompt,
        selected_dir=selected_dir,
        data_root=data_root,
        workspace_root=WORKSPACE_ROOT.resolve(),
        max_repairs=max(0, int(args.max_repairs)),
        heartbeat_seconds=max(1, int(args.heartbeat_seconds)),
        stall_timeout_seconds=max(5, int(args.stall_timeout_seconds)),
        live_process_grace_seconds=max(0, int(args.live_process_grace_seconds)),
        model_name=(args.model_name.strip() or None),
        host=(args.host.strip() or None),
        llm_backend=(args.llm_backend.strip() or None),
        auto_install_missing_tools=default_self_heal if args.auto_install_missing_tools is None else bool(args.auto_install_missing_tools),
        allow_replan=not bool(args.no_replan),
        allow_canonicalize=not bool(args.no_canonicalize),
        benchmark_policy=normalized_policy,
        plan_path=(Path(args.plan_file).expanduser().resolve() if args.plan_file else None),
        result_json=(Path(args.result_json).expanduser().resolve() if args.result_json else None),
        quiet=bool(args.quiet),
        print_plan=bool(args.print_plan),
        path_graph_db=graph_db,
        path_graph_user_key=str(args.path_graph_user_key or "default").strip() or "default",
        path_graph_scope=str(args.path_graph_scope or "global").strip() or "global",
        path_graph_persist_preference_updates=bool(args.path_graph_persist_preference_updates),
        auto_setup_isolated_tools=default_self_heal if args.auto_setup_isolated_tools is None else bool(args.auto_setup_isolated_tools),
        execution_mode=str(args.execution_mode or "batch").strip().lower() or "batch",
        analysis_type=str(args.analysis_type or "").strip(),
    )


def _build_failure_payload(
    harness: AgentE2EHarness,
    cfg: HarnessConfig,
    *,
    error: str,
) -> dict[str, object]:
    """Build a machine-readable failure payload for the current harness state."""

    run = harness.run if isinstance(getattr(harness, "run", {}), dict) else {}
    run_files = run.get("run_files", {}) if isinstance(run.get("run_files", {}), dict) else {}
    diagnosis_run = dict(run)
    diagnosis_run["status"] = "failed"
    diagnosis_run["error"] = str(error or "").strip() or str(run.get("error", "") or "")
    diagnosis = build_failure_diagnosis(diagnosis_run)
    return {
        "run_id": str(run.get("run_uid", "") or ""),
        "status": "failed",
        "error": str(error or "").strip() or "Harness terminated without a result.",
        "benchmark_policy": normalize_benchmark_policy(cfg.benchmark_policy),
        "run_dir": str(run_files.get("run_dir", "") or ""),
        "state_file": str(run_files.get("state", "") or ""),
        "events_file": str(run_files.get("events", "") or ""),
        "assistance_manifest_file": str(run_files.get("assistance_manifest", "") or ""),
        "failure_diagnosis": diagnosis,
        "input_quality": run.get("input_quality", {}),
        "in_run_quality_summary": run.get("in_run_quality_summary", {}),
        "research_report": run.get("research_report", {}),
    }


def _write_result_json(path: Path | None, payload: dict[str, object]) -> None:
    """Persist one result payload when a result path is configured."""

    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps_safe(payload, indent=2), encoding="utf-8")


def _persist_failure_state(
    harness: AgentE2EHarness,
    *,
    error: str,
) -> None:
    """Persist failed harness state and exit artifacts when run files exist."""

    if not isinstance(getattr(harness, "run", {}), dict):
        return
    if not harness.run.get("run_files"):
        return
    harness.run["status"] = "failed"
    harness.run["error"] = str(error or "").strip()
    harness.run["finished_at"] = _now_utc_iso()
    try:
        harness._persist_state()
        harness._write_exit()
    except Exception:
        pass


def _ensure_result_json(
    harness: AgentE2EHarness,
    cfg: HarnessConfig,
    *,
    result: dict[str, object] | None = None,
    fallback_error: str = "",
) -> None:
    """Backfill the result artifact when the process exits without writing it."""

    if cfg.result_json is None or cfg.result_json.exists():
        return
    payload = result if isinstance(result, dict) else _build_failure_payload(
        harness,
        cfg,
        error=fallback_error or "Harness exited without writing result JSON.",
    )
    _write_result_json(cfg.result_json, payload)


def main() -> int:
    cfg = _parse_args()
    harness = AgentE2EHarness(cfg)
    result: dict[str, object] | None = None
    termination_reason = ""
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _handle_sigterm(_signum: int, _frame: object) -> None:
        nonlocal termination_reason
        if termination_reason:
            raise SystemExit(143)
        termination_reason = "Harness received SIGTERM and is shutting down."
        _persist_failure_state(harness, error=termination_reason)
        payload = _build_failure_payload(harness, cfg, error=termination_reason)
        _write_result_json(cfg.result_json, payload)
        print(_json_dumps_safe(payload, indent=2), flush=True)
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        if is_explicit_research_prompt(cfg.prompt):
            result = handle_explicit_research_prompt(
                harness,
                benchmark_policy=normalize_benchmark_policy(cfg.benchmark_policy),
            )
        else:
            result = harness.run_end_to_end()
    except Exception as exc:
        _persist_failure_state(harness, error=str(exc))
        fail_payload = _build_failure_payload(harness, cfg, error=str(exc))
        _write_result_json(cfg.result_json, fail_payload)
        print(_json_dumps_safe(fail_payload, indent=2), flush=True)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
        _ensure_result_json(
            harness,
            cfg,
            result=result,
            fallback_error=termination_reason,
        )

    _write_result_json(cfg.result_json, result)
    print(_json_dumps_safe(result, indent=2), flush=True)
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
