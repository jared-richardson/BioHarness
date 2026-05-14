from __future__ import annotations

import json

from bio_harness.reporting.run_context import (
    build_completed_run_context_payload,
    build_live_result_payload,
)
from scripts.run_agent_e2e_support import (
    Any,
    CAPABILITY_CATALOG_PATH,
    HarnessConfig,
    Orchestrator,
    Path,
    PathGraphStore,
    SKILLS_DEFINITIONS,
    SKILLS_LIBRARY,
    TEMPLATE_COMPILER_TYPES,
    _capability_specs_from_catalog,
    _json_safe_default,
    _now_utc_iso,
    _parse_log_channel,
    append_event,
    append_line,
    build_ranked_fallback_catalog,
    datetime,
    default_path_graph_db_path,
    deterministic_prompt_hash,
    filter_forbidden_benchmark_sources,
    init_run_artifacts,
    is_bioagentbench_planning_strict_policy,
    is_blind_bioagentbench_policy,
    is_official_bioagentbench_policy,
    load_capability_catalog,
    normalize_benchmark_policy,
    write_exit,
    write_manifest,
    write_path_decisions,
    write_state,
)
from bio_harness.core.env_bootstrap import bootstrap_environment
from bio_harness.core.metaharness_flags import environment_bootstrap_enabled
from bio_harness.core.template_assistance_policy import (
    protocol_normalization_policy,
    protocol_template_assistance_enabled,
)


def _generic_template_fallback_used(fallback_selection: Any) -> bool:
    """Return whether run state reflects an actually applied generic fallback."""

    if not isinstance(fallback_selection, dict) or not fallback_selection:
        return False

    selected_pipeline_id = str(fallback_selection.get("selected_pipeline_id", "") or "").strip()
    if selected_pipeline_id:
        return True

    selected_pipeline_ids = [
        str(item).strip()
        for item in (fallback_selection.get("selected_pipeline_ids", []) or [])
        if str(item).strip()
    ]
    if selected_pipeline_ids:
        return True

    selection = (
        fallback_selection.get("selection", {})
        if isinstance(fallback_selection.get("selection", {}), dict)
        else {}
    )
    nested_pipeline_id = str(selection.get("pipeline_id", "") or "").strip()
    if nested_pipeline_id:
        return True
    if str(selection.get("action", "") or "").strip() == "create":
        return True

    composition = (
        fallback_selection.get("composition", {})
        if isinstance(fallback_selection.get("composition", {}), dict)
        else {}
    )
    if bool(composition.get("applied", False)):
        return True

    return str(fallback_selection.get("why", "") or "").strip() in {
        "ranked_fallback_template_selected",
        "created_stub_plan",
        "catalog_selection_unavailable",
    }


def _json_safe_state_value(value: Any) -> Any:
    """Return a JSON-ready copy of persisted run-state values.

    Args:
        value: Arbitrary value stored in the in-memory run dictionary.

    Returns:
        A JSON-serializable representation suitable for artifact schemas.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe_state_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_state_value(item) for item in value]
    try:
        converted = _json_safe_default(value)
    except TypeError:
        return str(value)
    return _json_safe_state_value(converted)


class AgentE2EStateMixin:
    def __init__(self, config: HarnessConfig):
        self.cfg = config
        self.catalog = load_capability_catalog(CAPABILITY_CATALOG_PATH)
        self.capability_specs = _capability_specs_from_catalog(self.catalog)
        graph_db = config.path_graph_db or default_path_graph_db_path(config.selected_dir)
        self.path_graph = PathGraphStore(graph_db)
        self.path_graph.ensure_catalog_paths(build_ranked_fallback_catalog())
        self.orchestrator = Orchestrator(
            skills_dir=SKILLS_DEFINITIONS,
            skill_library_dir=SKILLS_LIBRARY,
            model_name=config.model_name,
            host=config.host,
            llm_backend=config.llm_backend,
        )
        self._planner_prewarm_attempted = False
        self.run: dict[str, Any] = {}

    def _init_run(self) -> None:
        files = init_run_artifacts(self.cfg.workspace_root, self.cfg.prompt)
        benchmark_policy = self._benchmark_policy()
        deterministic_template_assistance_enabled = protocol_template_assistance_enabled(
            benchmark_policy
        )
        protocol_normalization_enabled = (
            not self._blind_benchmark_policy()
        ) and deterministic_template_assistance_enabled
        self.run = {
            "run_uid": str(files["run_id"]),
            "run_files": {k: str(v) for k, v in files.items()},
            "user_request": self.cfg.prompt,
            "prompt_hash": deterministic_prompt_hash(self.cfg.prompt),
            "benchmark_policy": benchmark_policy,
            "execution_mode": str(getattr(self.cfg, "execution_mode", "batch") or "batch").strip().lower() or "batch",
            "plan": {},
            "analysis_spec": {},
            "plan_contract": {},
            "contract_validation": {},
            "protocol_validation": {},
            "semantic_validation": {},
            "preexecution_stage_repairs": {
                "repair_applied": False,
                "removed_step_ids": [],
                "moved_step_ids": [],
                "rebinds": [],
                "unresolved_issues": [],
            },
            "bash_placeholder_resolutions": [],
            "protocol_repair_enabled": (
                not self._blind_benchmark_policy()
            ) and deterministic_template_assistance_enabled,
            "protocol_repair_attempted": False,
            "protocol_repair_applied": False,
            "protocol_repair_action": "",
            "protocol_repair_skip_reason": "",
            "protocol_normalization_enabled": protocol_normalization_enabled,
            "protocol_normalization_meta": {},
            "fallback_catalog_size": 0,
            "fallback_catalog_summary": [],
            "fallback_selection": {},
            "excluded_fallback_pipeline_ids": [],
            "selected_path_id": "",
            "status": "initialized",
            "error": "",
            "logs": [],
            "started_at": _now_utc_iso(),
            "finished_at": "",
            "last_executor_event_ts": 0.0,
            "last_queue_activity_ts": 0.0,
            "stall_event_emitted": False,
            "missing_tools_detected": [],
            "missing_reference_detected": [],
            "missing_sample_groups": [],
            "missing_sample_group_signals": [],
            "observed_sample_groups": [],
            "observed_sample_group_sources": {},
            "no_fastq_found": False,
            "empty_bams_detected": [],
            "policy_block_detected": False,
            "validation_block_detected": False,
            "stale_tmp_cache_detected": False,
            "format_input_error_detected": False,
            "planner_timeout_detected": False,
            "planner_failopen_used": False,
            "local_model_loopback_blocked_detected": False,
            "execution_stalled_detected": False,
            "generic_template_fallback_blocked": False,
            "generic_template_fallback_block_reason": "",
            "failure_signatures": [],
            "failure_diagnosis": {},
            "input_quality": {},
            "environment_snapshot": {},
            "research_report": {},
            "literature_planning_support": {},
            "planning_attempts": [],
            "planner_strategy_used": "",
            "planner_trace_dir": str(files.get("planner", "")),
            "stepwise_turns": [],
            "stepwise_rejected_candidates": [],
            "execution_started": False,
            "stepwise_last_step_failed": False,
            "in_planner_phase": False,
            "planner_phase_started_at": 0.0,
            "planner_phase_timeout_seconds": 0,
            "planner_phase_strategy": "",
            "planner_phase_pid": 0,
            "auto_repair_attempts": {},
            "auto_repair_history": [],
            "auto_repair_last_class": "",
            "next_step_idx": 0,
            "step_statuses": [],
            "process_monitor_last": {},
            "stream_counters": {},
            "recent_stream_markers": [],
            "last_artifact_probe": {},
            "in_run_quality_summary": {},
            "in_run_quality_recent_events": [],
            "in_run_quality_seen_files": {},
            "in_run_quality_emitted_event_keys": [],
        }
        write_manifest(
            Path(self.run["run_files"]["manifest"]),
            {
                "run_id": self.run["run_uid"],
                "created_at": datetime.now().isoformat(),
                "mode": "cli_e2e_harness",
                "selected_dir": str(self.cfg.selected_dir),
                "data_root": str(self.cfg.data_root),
                "model_name": self.cfg.model_name or "",
                "host": self.cfg.host or "",
                "llm_backend": self.cfg.llm_backend or "",
                "benchmark_policy": benchmark_policy,
                "execution_mode": str(getattr(self.cfg, "execution_mode", "batch") or "batch").strip().lower() or "batch",
                "path_graph_db": str(self.path_graph.db_path),
                "path_graph_user_key": str(self.cfg.path_graph_user_key),
                "path_graph_scope": str(self.cfg.path_graph_scope),
            },
        )
        write_path_decisions(
            Path(self.run["run_files"]["path_decisions"]),
            user_requested_root=str(self.cfg.data_root),
            resolved_root=str(self.cfg.data_root),
            resolution_reason="cli_arg_data_root",
            rejected_candidates=[],
        )
        self._write_assistance_manifest()

    def _refresh_environment_snapshot(self) -> None:
        """Refresh the bounded environment snapshot stored in run state."""

        if not environment_bootstrap_enabled():
            self.run["environment_snapshot"] = {}
            return
        try:
            self.run["environment_snapshot"] = bootstrap_environment(
                data_root=self.cfg.data_root,
                benchmark_policy=self._benchmark_policy(),
                analysis_spec=self._run_analysis_spec_dict(),
                check_versions=False,
            )
        except Exception:
            self.run["environment_snapshot"] = {}

    def _benchmark_policy(self) -> str:
        cfg = getattr(self, "cfg", None)
        return normalize_benchmark_policy(getattr(cfg, "benchmark_policy", None))

    def _blind_benchmark_policy(self) -> bool:
        cfg = getattr(self, "cfg", None)
        return is_blind_bioagentbench_policy(getattr(cfg, "benchmark_policy", None))

    def _official_benchmark_policy(self) -> bool:
        cfg = getattr(self, "cfg", None)
        return is_official_bioagentbench_policy(getattr(cfg, "benchmark_policy", None))

    def _planning_strict_benchmark_policy(self) -> bool:
        cfg = getattr(self, "cfg", None)
        return is_bioagentbench_planning_strict_policy(getattr(cfg, "benchmark_policy", None))

    def _run_plan_dict(self) -> dict[str, Any]:
        """Return the current run plan as a dictionary."""

        plan = self.run.get("plan", {})
        return plan if isinstance(plan, dict) else {}

    def _run_analysis_spec_dict(self) -> dict[str, Any]:
        """Return the current run analysis spec as a dictionary."""

        analysis_spec = self.run.get("analysis_spec", {})
        return analysis_spec if isinstance(analysis_spec, dict) else {}

    def _runtime_binding_analysis_spec(self) -> dict[str, Any]:
        """Return analysis-spec context enriched with harness-owned runtime paths."""

        analysis_spec = dict(self._run_analysis_spec_dict())
        analysis_spec.setdefault("selected_dir", str(self.cfg.selected_dir))
        analysis_spec.setdefault("data_root", str(self.cfg.data_root))
        analysis_spec.setdefault("requested_data_root", str(self.cfg.data_root))
        analysis_spec.setdefault("benchmark_policy", self._benchmark_policy())
        if not str(analysis_spec.get("analysis_type", "") or "").strip():
            analysis_type = self._infer_runtime_binding_analysis_type()
            if analysis_type:
                analysis_spec["analysis_type"] = analysis_type
        return analysis_spec

    def _infer_runtime_binding_analysis_type(self) -> str:
        """Infer analysis type for deterministic runtime artifact binding."""

        try:
            from bio_harness.core.analysis_spec import infer_analysis_type

            contract = self.run.get("plan_contract", {})
            if not isinstance(contract, dict):
                contract = {}
            user_request = str(
                self.run.get("user_request", "") or getattr(self.cfg, "prompt", "") or ""
            )
            inferred = str(infer_analysis_type(user_request, contract, None) or "").strip()
        except Exception:  # pragma: no cover - defensive fallback
            inferred = ""

        if inferred not in {"", "generic_analysis", "variant_calling"}:
            return inferred
        if self._runtime_plan_looks_like_bacterial_evolution():
            return "bacterial_evolution_variant_calling"
        return inferred

    def _runtime_plan_looks_like_bacterial_evolution(self) -> bool:
        """Return whether current wrapper sequence matches evolution scaffolding."""

        raw_steps = self._run_plan_dict().get("plan", [])
        if not isinstance(raw_steps, list):
            return False
        tools = {
            str(step.get("tool_name", "") or "").strip().lower()
            for step in raw_steps
            if isinstance(step, dict)
        }
        if not {"spades_assemble", "freebayes_call"}.issubset(tools):
            return False
        return bool(
            tools
            & {
                "bcftools_isec_run",
                "shared_variants_export_run",
                "snpeff_annotate",
            }
        )

    def _direct_skill_smoke_run(self) -> bool:
        analysis_type = str(self._run_analysis_spec_dict().get("analysis_type", "") or "").strip()
        return analysis_type == "direct_skill_smoke"

    def _strict_protocol_grounding_must_raise(
        self,
        analysis_type: str,
        *,
        planning_strict_benchmark_policy: bool,
    ) -> bool:
        """Decide whether strict planning must abort on protocol grounding failure."""

        return (
            planning_strict_benchmark_policy
            or analysis_type not in TEMPLATE_COMPILER_TYPES
        )

    def _protocol_normalization_policy(
        self,
        *,
        blind_benchmark_policy: bool,
        has_compiler: bool,
        planning_strict_benchmark_policy: bool,
        protocol_source_files: list[str],
    ) -> tuple[bool, dict[str, Any]]:
        """Return whether protocol normalization is allowed under current policy."""

        return protocol_normalization_policy(
            benchmark_policy=self._benchmark_policy(),
            has_compiler=has_compiler,
            planning_strict_benchmark_policy=planning_strict_benchmark_policy,
            protocol_source_files=protocol_source_files,
        )

    def _assistance_manifest_payload(self) -> dict[str, Any]:
        analysis_spec = self._run_analysis_spec_dict()
        protocol_grounding = (
            analysis_spec.get("protocol_grounding", {})
            if isinstance(analysis_spec.get("protocol_grounding", {}), dict)
            else {}
        )
        source_files = [str(path).strip() for path in (protocol_grounding.get("source_files", []) or []) if str(path).strip()]
        forbidden_sources = filter_forbidden_benchmark_sources(source_files)
        protocol_repair_action = str(self.run.get("protocol_repair_action", "") or "").strip()
        fallback_selection = self.run.get("fallback_selection", {})
        literature_support = (
            analysis_spec.get("literature_planning_support", {})
            if isinstance(analysis_spec.get("literature_planning_support", {}), dict)
            else {}
        )
        return {
            "run_id": str(self.run.get("run_uid", "")),
            "benchmark_policy": self._benchmark_policy(),
            "leakage_guard_active": self._blind_benchmark_policy(),
            "protocol_grounding_present": bool(protocol_grounding),
            "protocol_grounding_source_files": source_files,
            "forbidden_benchmark_sources_visible": bool(forbidden_sources),
            "forbidden_benchmark_sources": forbidden_sources,
            "protocol_guidance_visible_to_planner": not self._blind_benchmark_policy(),
            "protocol_template_assistance_enabled": protocol_template_assistance_enabled(
                self._benchmark_policy()
            ),
            "deterministic_protocol_repair_enabled": bool(self.run.get("protocol_repair_enabled", False)),
            "deterministic_protocol_repair_attempted": bool(self.run.get("protocol_repair_attempted", False)),
            "deterministic_protocol_repair_applied": bool(self.run.get("protocol_repair_applied", False)),
            "protocol_repair_action": protocol_repair_action,
            "protocol_repair_skip_reason": str(self.run.get("protocol_repair_skip_reason", "") or ""),
            "protocol_template_fallback_used": "template_fallback" in protocol_repair_action,
            "protocol_normalization_enabled": bool(self.run.get("protocol_normalization_enabled", False)),
            "protocol_normalization_meta": self.run.get("protocol_normalization_meta", {}),
            "planner_failopen_used": bool(self.run.get("planner_failopen_used", False)),
            "generic_template_fallback_used": _generic_template_fallback_used(fallback_selection),
            "generic_template_fallback": fallback_selection if isinstance(fallback_selection, dict) else {},
            "generic_template_fallback_blocked": bool(self.run.get("generic_template_fallback_blocked", False)),
            "generic_template_fallback_block_reason": str(self.run.get("generic_template_fallback_block_reason", "") or ""),
            "literature_planning_support_status": str(literature_support.get("status", "") or ""),
            "literature_planning_support_visible_to_planner": bool(literature_support.get("visible_to_planner", False)),
            "literature_planning_support_query_class": str(literature_support.get("query_class", "") or ""),
            "literature_planning_support_reason": str(literature_support.get("trigger_reason", "") or ""),
            "literature_planning_support_json": str(literature_support.get("json_path", "") or ""),
            "literature_planning_support_markdown": str(literature_support.get("markdown_path", "") or ""),
        }

    def _write_assistance_manifest(self) -> None:
        try:
            write_manifest(
                Path(self.run["run_files"]["assistance_manifest"]),
                self._assistance_manifest_payload(),
            )
        except Exception:
            pass

    def _set_planner_phase(
        self,
        *,
        active: bool,
        strategy: str = "",
        timeout_seconds: int = 0,
        worker_pid: int = 0,
    ) -> None:
        """Record whether the harness is actively waiting on planner work."""

        if active:
            self.run["in_planner_phase"] = True
            self.run["planner_phase_started_at"] = float(datetime.now().timestamp())
            self.run["planner_phase_timeout_seconds"] = max(0, int(timeout_seconds))
            self.run["planner_phase_strategy"] = str(strategy or "")
            self.run["planner_phase_pid"] = max(0, int(worker_pid))
            return
        self.run["in_planner_phase"] = False
        self.run["planner_phase_started_at"] = 0.0
        self.run["planner_phase_timeout_seconds"] = 0
        self.run["planner_phase_strategy"] = ""
        self.run["planner_phase_pid"] = 0

    def _update_planner_phase_pid(self, worker_pid: int) -> None:
        """Update the tracked planner worker PID while a planner phase is active."""

        if not bool(self.run.get("in_planner_phase", False)):
            return
        self.run["planner_phase_pid"] = max(0, int(worker_pid))

    def _persist_state(self) -> None:
        data = {
            "run_id": self.run.get("run_uid", ""),
            "status": self.run.get("status", "unknown"),
            "error": self.run.get("error", ""),
            "benchmark_policy": self._benchmark_policy(),
            "execution_mode": str(self.run.get("execution_mode", "") or "batch").strip().lower() or "batch",
            "plan": self.run.get("plan", {}),
            "next_step_idx": self.run.get("next_step_idx", 0),
            "step_statuses": self.run.get("step_statuses", []),
            "stepwise_pending_candidate_steps": self.run.get("stepwise_pending_candidate_steps", []),
            "auto_repair_attempts": self.run.get("auto_repair_attempts", {}),
            "auto_repair_last_class": self.run.get("auto_repair_last_class", ""),
            "auto_repair_history": self.run.get("auto_repair_history", []),
            "plan_contract": self.run.get("plan_contract", {}),
            "analysis_spec": _json_safe_state_value(
                self.run.get("analysis_spec", {})
            ),
            "contract_validation": self.run.get("contract_validation", {}),
            "protocol_validation": self.run.get("protocol_validation", {}),
            "semantic_validation": self.run.get("semantic_validation", {}),
            "preexecution_stage_repairs": self.run.get("preexecution_stage_repairs", {}),
            "bash_placeholder_resolutions": self.run.get("bash_placeholder_resolutions", []),
            "protocol_repair_enabled": bool(self.run.get("protocol_repair_enabled", False)),
            "protocol_repair_attempted": bool(self.run.get("protocol_repair_attempted", False)),
            "protocol_repair_applied": bool(self.run.get("protocol_repair_applied", False)),
            "protocol_repair_action": str(self.run.get("protocol_repair_action", "") or ""),
            "protocol_repair_skip_reason": str(self.run.get("protocol_repair_skip_reason", "") or ""),
            "protocol_normalization_enabled": bool(self.run.get("protocol_normalization_enabled", False)),
            "protocol_normalization_meta": self.run.get("protocol_normalization_meta", {}),
            "fallback_catalog_size": int(self.run.get("fallback_catalog_size", 0)),
            "fallback_catalog_summary": self.run.get("fallback_catalog_summary", []),
            "fallback_selection": self.run.get("fallback_selection", {}),
            "excluded_fallback_pipeline_ids": self.run.get("excluded_fallback_pipeline_ids", []),
            "selected_path_id": self.run.get("selected_path_id", ""),
            "prompt_hash": self.run.get("prompt_hash", ""),
            "path_graph_db": str(self.path_graph.db_path),
            "path_graph_user_key": str(self.cfg.path_graph_user_key),
            "path_graph_scope": str(self.cfg.path_graph_scope),
            "missing_tools_detected": self.run.get("missing_tools_detected", []),
            "missing_reference_detected": self.run.get("missing_reference_detected", []),
            "missing_sample_groups": self.run.get("missing_sample_groups", []),
            "missing_sample_group_signals": self.run.get("missing_sample_group_signals", []),
            "observed_sample_groups": self.run.get("observed_sample_groups", []),
            "observed_sample_group_sources": self.run.get("observed_sample_group_sources", {}),
            "no_fastq_found": bool(self.run.get("no_fastq_found", False)),
            "policy_block_detected": bool(self.run.get("policy_block_detected", False)),
            "validation_block_detected": bool(self.run.get("validation_block_detected", False)),
            "stale_tmp_cache_detected": bool(self.run.get("stale_tmp_cache_detected", False)),
            "format_input_error_detected": bool(self.run.get("format_input_error_detected", False)),
            "planner_timeout_detected": bool(self.run.get("planner_timeout_detected", False)),
            "planner_failopen_used": bool(self.run.get("planner_failopen_used", False)),
            "local_model_loopback_blocked_detected": bool(self.run.get("local_model_loopback_blocked_detected", False)),
            "execution_stalled_detected": bool(self.run.get("execution_stalled_detected", False)),
            "execution_started": bool(self.run.get("execution_started", False)),
            "stepwise_last_step_failed": bool(self.run.get("stepwise_last_step_failed", False)),
            "in_planner_phase": bool(self.run.get("in_planner_phase", False)),
            "planner_phase_started_at": float(self.run.get("planner_phase_started_at", 0.0) or 0.0),
            "planner_phase_timeout_seconds": int(self.run.get("planner_phase_timeout_seconds", 0) or 0),
            "planner_phase_strategy": str(self.run.get("planner_phase_strategy", "") or ""),
            "planner_phase_pid": int(self.run.get("planner_phase_pid", 0) or 0),
            "failure_signatures": self.run.get("failure_signatures", []),
            "failure_diagnosis": self.run.get("failure_diagnosis", {}),
            "input_quality": self.run.get("input_quality", {}),
            "research_report": self.run.get("research_report", {}),
            "literature_planning_support": self.run.get("literature_planning_support", {}),
            "planning_attempts": self.run.get("planning_attempts", []),
            "planner_strategy_used": str(self.run.get("planner_strategy_used", "")),
            "planner_trace_dir": str(self.run.get("planner_trace_dir", "")),
            "stepwise_turns": self.run.get("stepwise_turns", []),
            "stepwise_rejected_candidates": self.run.get(
                "stepwise_rejected_candidates",
                [],
            ),
            "process_monitor_last": self.run.get("process_monitor_last", {}),
            "stream_counters": self.run.get("stream_counters", {}),
            "recent_stream_markers": self.run.get("recent_stream_markers", []),
            "last_artifact_probe": self.run.get("last_artifact_probe", {}),
            "in_run_quality_summary": self.run.get("in_run_quality_summary", {}),
            "in_run_quality_recent_events": self.run.get("in_run_quality_recent_events", []),
            "in_run_quality_seen_files": self.run.get("in_run_quality_seen_files", {}),
            "in_run_quality_emitted_event_keys": self.run.get("in_run_quality_emitted_event_keys", []),
            "started_at": self.run.get("started_at", ""),
            "finished_at": self.run.get("finished_at", ""),
            "updated_at": datetime.now().isoformat(),
        }
        write_state(Path(self.run["run_files"]["state"]), data)
        self._persist_completed_run_context(data)
        self._write_assistance_manifest()

    def _persist_completed_run_context(self, state_payload: dict[str, Any]) -> None:
        """Persist completed-run context for terminal runs.

        Args:
            state_payload: State payload about to be written to ``state.json``.
        """

        status = str(self.run.get("status", "") or "").strip().lower()
        if status not in {"completed", "failed"}:
            return
        run_files = self.run.get("run_files", {})
        if not isinstance(run_files, dict):
            return
        context_path_text = str(run_files.get("completed_run_context", "") or "").strip()
        manifest_path_text = str(run_files.get("manifest", "") or "").strip()
        run_dir_text = str(run_files.get("run_dir", "") or "").strip()
        if not context_path_text or not manifest_path_text or not run_dir_text:
            return

        manifest_path = Path(manifest_path_text)
        run_dir = Path(run_dir_text)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
        except (OSError, ValueError):
            manifest = {}

        analysis_type = str(
            (self._run_analysis_spec_dict().get("analysis_type", "") or "")
        ).strip()
        result_payload = build_live_result_payload(
            run=self.run,
            selected_dir=self.cfg.selected_dir,
            run_dir=run_dir,
            benchmark_policy=self._benchmark_policy(),
            data_root=self.cfg.data_root,
            analysis_type=analysis_type,
            result_path=self.cfg.result_json if self.cfg.result_json is not None else self.cfg.selected_dir / "result.json",
        )
        context_payload = build_completed_run_context_payload(
            selected_dir=self.cfg.selected_dir,
            run_dir=run_dir,
            result=result_payload,
            manifest=manifest,
            state=state_payload,
            final_plan=self._run_plan_dict(),
            preexecution_stage_repairs=(
                self.run.get("preexecution_stage_repairs", {})
                if isinstance(self.run.get("preexecution_stage_repairs", {}), dict)
                else {}
            ),
            bash_placeholder_resolutions=(
                self.run.get("bash_placeholder_resolutions", [])
                if isinstance(self.run.get("bash_placeholder_resolutions", []), list)
                else []
            ),
            result_path=self.cfg.result_json if self.cfg.result_json is not None else self.cfg.selected_dir / "result.json",
            manifest_path=manifest_path,
            state_path=Path(str(run_files.get("state", "") or "")) if str(run_files.get("state", "") or "").strip() else None,
            validator_log_path=(self.cfg.selected_dir / "validator.log"),
            harness_log_path=(self.cfg.selected_dir / "harness.log"),
            events_path=Path(str(run_files.get("events", "") or "")) if str(run_files.get("events", "") or "").strip() else None,
            execution_log_path=Path(str(run_files.get("exec", "") or "")) if str(run_files.get("exec", "") or "").strip() else None,
        )
        try:
            Path(context_path_text).write_text(
                json.dumps(
                    context_payload,
                    indent=2,
                    sort_keys=True,
                    default=_json_safe_default,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def _write_exit(self) -> None:
        """Persist the latest exit snapshot for the run.

        Nonterminal snapshots intentionally omit terminal fields so monitors do
        not mistake an in-flight stepwise run for a finished run.
        """

        self._write_assistance_manifest()
        status = str(self.run.get("status", "") or "").strip()
        terminal = status.lower() in {"completed", "failed", "blocked_missing_tools"}
        started_at = str(self.run.get("started_at", "") or "").strip() or None
        finished_at = str(self.run.get("finished_at", "") or "").strip() if terminal else ""
        error = str(self.run.get("error", "") or "").strip() if terminal else ""
        write_exit(
            Path(self.run["run_files"]["exit"]),
            {
                "run_id": self.run.get("run_uid", ""),
                "status": status,
                "started_at": started_at,
                "error": error,
                "finished_at": finished_at or None,
            },
        )
        if terminal:
            self._write_terminal_summary()

    def _write_terminal_summary(self) -> None:
        """Persist the human-readable summary for a terminal CLI run."""

        run_files = self.run.get("run_files", {})
        if not isinstance(run_files, dict):
            return
        summary_path_text = str(run_files.get("summary", "") or "").strip()
        if not summary_path_text:
            return
        statuses = list(self.run.get("step_statuses", []) or [])
        completed_steps = sum(
            1
            for status in statuses
            if str(status).strip().lower()
            in {"succeeded", "success", "completed", "ok", "done"}
        )
        failed_steps = sum(
            1 for status in statuses if str(status).strip().lower() == "failed"
        )
        total_steps = len(statuses)
        contract_validation = (
            self.run.get("contract_validation", {})
            if isinstance(self.run.get("contract_validation", {}), dict)
            else {}
        )
        contract_label = "passed" if contract_validation.get("passed", False) else "not passed"
        lines = [
            "# Run Summary",
            "",
            f"- Run ID: {self.run.get('run_uid', '')}",
            f"- Status: {self.run.get('status', '')}",
            f"- Error: {self.run.get('error', '') or 'none'}",
            f"- Contract validation: {contract_label}",
            f"- Started: {self.run.get('started_at', '') or 'unknown'}",
            f"- Finished: {self.run.get('finished_at', '') or 'unknown'}",
            f"- Steps completed: {completed_steps}/{total_steps}",
        ]
        if self.run.get("status") == "completed" and failed_steps:
            lines.append(f"- Repaired failed attempts retained in trace: {failed_steps}")
        rejected = list(self.run.get("stepwise_rejected_candidates", []) or [])
        if rejected:
            lines.append(f"- Rejected candidates: {len(rejected)}")
        try:
            Path(summary_path_text).write_text(
                "\n".join(lines).rstrip() + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def _append_event(self, *, step_id: int | None, agent: str, event_type: str, severity: str, payload: dict[str, Any]) -> None:
        append_event(
            Path(self.run["run_files"]["events"]),
            run_id=self.run.get("run_uid", ""),
            step_id=step_id,
            agent=agent,
            event_type=event_type,
            severity=severity,
            payload=payload,
        )

    def _append_log(self, line: str) -> None:
        self.run.setdefault("logs", []).append(line)
        append_line(Path(self.run["run_files"]["exec"]), line)
        channel, body = _parse_log_channel(line)
        if channel == "stdout":
            append_line(Path(self.run["run_files"]["stdout"]), body)
        elif channel == "stderr":
            append_line(Path(self.run["run_files"]["stderr"]), body)

    def _note_failure_signature(self, signature: str) -> None:
        sig = str(signature or "").strip().lower()
        if not sig:
            return
        existing = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if sig in existing:
            return
        self.run["failure_signatures"] = sorted(existing.union({sig}))
