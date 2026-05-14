from __future__ import annotations

from scripts.run_agent_e2e_support import (
    Any,
    DEFAULT_PLANNER_HEARTBEAT_SECONDS,
    DEFAULT_PLANNER_MAX_ATTEMPTS,
    Orchestrator,
    TEMPLATE_COMPILER_TYPES,
    TOOL_STALL_GRACE_HINTS,
    _json_dumps_safe,
    _is_empty_contract,
    _plan_summary_for_repair_prompt,
    build_repair_context,
    deterministic_protocol_repair,
    is_loopback_permission_error,
    mp,
    os,
    sys,
    time,
)
from bio_harness.core.env_bootstrap import format_bootstrap_for_prompt
from bio_harness.core.metaharness_flags import environment_bootstrap_enabled


class AgentE2EPlannerSettingsMixin:
    def _initial_planning_prompt(self) -> str:
        """Return the initial planner prompt with bounded environment context."""

        base_prompt = str(self.cfg.prompt or "").strip()
        if not environment_bootstrap_enabled():
            return base_prompt
        snapshot = self.run.get("environment_snapshot", {})
        prompt_block = format_bootstrap_for_prompt(snapshot if isinstance(snapshot, dict) else {})
        if not prompt_block:
            return base_prompt
        return f"{base_prompt}\n\n{prompt_block}"

    def _contract_replan_prompt(
        self,
        *,
        contract: dict[str, Any],
        validation: dict[str, Any],
        plan: dict[str, Any],
    ) -> str:
        plan_summary = _plan_summary_for_repair_prompt(plan)
        repair_context = self._build_repair_prompt_context(
            failure_class="contract_mismatch",
            reason="contract validation failed before execution",
            validation=validation,
            focus_mode="full_plan",
        )
        return (
            "You are creating an executable bioinformatics plan.\n"
            "Return ONLY JSON with keys `thought_process` and `plan`.\n"
            "The plan must satisfy the request contract and use runnable steps.\n\n"
            f"Original user request:\n{self.cfg.prompt}\n\n"
            f"Required contract:\n{_json_dumps_safe(contract, indent=2)}\n\n"
            f"Current plan contract gaps:\n{_json_dumps_safe(validation, indent=2)}\n\n"
            "Rules:\n"
            "- Keep existing valid steps where possible.\n"
            "- Add missing capabilities/tool hints explicitly.\n"
            "- Treat `required_tool_hints` as hard requirements.\n"
            "- Treat `required_output_paths` as requested final deliverables, not undocumented tool arguments.\n"
            "- Use concrete file paths and executable tool arguments.\n\n"
            "The concrete seed plan is supplied out-of-band; use it to preserve valid paths and arguments.\n\n"
            f"Focused repair context:\n{_json_dumps_safe(repair_context, indent=2)}\n\n"
            f"Current plan summary:\n{_json_dumps_safe(plan_summary, indent=2)}\n"
        )

    def _protocol_replan_prompt(
        self,
        *,
        analysis_spec: dict[str, Any],
        validation: dict[str, Any],
        plan: dict[str, Any],
    ) -> str:
        protocol_grounding = (
            analysis_spec.get("protocol_grounding", {})
            if isinstance(analysis_spec, dict) and isinstance(analysis_spec.get("protocol_grounding", {}), dict)
            else {}
        )
        plan_summary = _plan_summary_for_repair_prompt(plan)
        repair_context = self._build_repair_prompt_context(
            failure_class="protocol_grounding",
            reason="protocol grounding validation failed before execution",
            validation=validation,
            focus_mode="full_plan",
        )
        return (
            "You are repairing an executable bioinformatics plan to satisfy task-local protocol grounding.\n"
            "Return ONLY JSON with keys `thought_process` and `plan`.\n"
            "The repaired plan must be runnable and must satisfy the protocol constraints.\n\n"
            f"Original user request:\n{self.cfg.prompt}\n\n"
            f"Analysis spec:\n{_json_dumps_safe(analysis_spec, indent=2)}\n\n"
            f"Protocol grounding:\n{_json_dumps_safe(protocol_grounding, indent=2)}\n\n"
            f"Current protocol gaps:\n{_json_dumps_safe(validation, indent=2)}\n\n"
            "Rules:\n"
            "- Keep existing valid steps where possible.\n"
            "- Preserve task-local reference and annotation provenance.\n"
            "- Satisfy protocol-required tools and signals explicitly.\n"
            "- For shared-comparison tasks, preserve separate comparison branches instead of collapsing samples into one path.\n"
            "- Do not parse structured VCF/annotation outputs with ad hoc shell or inline Python when a typed exporter/adapter is available.\n"
            "- Use concrete file paths and executable tool arguments.\n\n"
            "The concrete seed plan is supplied out-of-band; use it to preserve valid paths and arguments.\n\n"
            f"Focused repair context:\n{_json_dumps_safe(repair_context, indent=2)}\n\n"
            f"Current plan summary:\n{_json_dumps_safe(plan_summary, indent=2)}\n"
        )

    def _semantic_replan_prompt(
        self,
        *,
        analysis_spec: dict[str, Any],
        validation: dict[str, Any],
        plan: dict[str, Any],
    ) -> str:
        plan_summary = _plan_summary_for_repair_prompt(plan)
        repair_context = self._build_repair_prompt_context(
            failure_class="semantic_validation",
            reason="semantic validation failed before execution",
            validation=validation,
            focus_mode="full_plan",
        )
        return (
            "You are repairing an executable bioinformatics plan to satisfy semantic plan guards.\n"
            "Return ONLY JSON with keys `thought_process` and `plan`.\n"
            "The repaired plan must be runnable and biologically coherent.\n\n"
            f"Original user request:\n{self.cfg.prompt}\n\n"
            f"Analysis spec:\n{_json_dumps_safe(analysis_spec, indent=2)}\n\n"
            f"Semantic validation issues:\n{_json_dumps_safe(validation, indent=2)}\n\n"
            "Rules:\n"
            "- Keep existing valid steps where possible.\n"
            "- Do not reference annotation-derived fields before an annotation step has produced them.\n"
            "- Preserve valid local file paths under the selected run directory.\n"
            "- Use concrete file paths and executable tool arguments.\n\n"
            "The concrete seed plan is supplied out-of-band; use it to preserve valid paths and arguments.\n\n"
            f"Focused repair context:\n{_json_dumps_safe(repair_context, indent=2)}\n\n"
            f"Current plan summary:\n{_json_dumps_safe(plan_summary, indent=2)}\n"
        )

    def _planner_heartbeat_seconds(self) -> int:
        raw = os.getenv("BIO_HARNESS_PLANNER_HEARTBEAT_SECONDS", str(DEFAULT_PLANNER_HEARTBEAT_SECONDS))
        try:
            val = int(raw)
        except Exception:
            val = DEFAULT_PLANNER_HEARTBEAT_SECONDS
        return max(1, min(30, val))

    def _planner_connectivity_wait_seconds(self) -> int:
        raw = os.getenv("BIO_HARNESS_PLANNER_CONNECTIVITY_WAIT_SECONDS", "30")
        try:
            val = int(float(raw))
        except Exception:
            val = 30
        return max(0, min(300, val))

    def _planner_max_attempts(self) -> int:
        raw = os.getenv("BIO_HARNESS_PLANNER_MAX_ATTEMPTS", str(DEFAULT_PLANNER_MAX_ATTEMPTS))
        try:
            val = int(raw)
        except Exception:
            val = DEFAULT_PLANNER_MAX_ATTEMPTS
        return max(1, min(5, val))

    def _runtime_replan_max_attempts(self) -> int:
        """Return the maximum number of LLM runtime repair attempts."""
        raw = os.getenv("BIO_HARNESS_RUNTIME_REPLAN_MAX_ATTEMPTS", "3")
        try:
            val = int(raw)
        except Exception:
            val = 3
        return max(1, min(3, val))

    def _runtime_replan_focus_modes(self) -> list[str]:
        """Return progressive repair focus scopes for runtime replans."""
        focus_ladder = ["step_local", "subgraph_local", "full_plan"]
        return focus_ladder[: self._runtime_replan_max_attempts()]

    def _build_repair_prompt_context(
        self,
        *,
        failure_class: str,
        reason: str,
        validation: dict[str, Any] | None = None,
        focus_mode: str = "step_local",
    ) -> dict[str, Any]:
        """Build structured repair context for focused repair prompts."""
        try:
            available_skills = self.orchestrator._available_skill_metadata()
        except Exception:
            available_skills = []
        return build_repair_context(
            run=self.run,
            selected_dir=self.cfg.selected_dir,
            data_root=getattr(self.cfg, "data_root", None),
            available_skills=available_skills,
            failure_class=failure_class,
            reason=reason,
            validation=validation,
            focus_mode=focus_mode,
        )

    def _strict_llm_planning_enabled(self) -> bool:
        raw = str(os.getenv("BIO_HARNESS_STRICT_LLM_PLANNING", "0") or "0").strip().lower()
        if not raw:
            return False
        return raw in {"1", "true", "yes", "on"}

    def _planner_template_fastpath_enabled(self) -> bool:
        if self._strict_llm_planning_enabled():
            return False
        raw = str(os.getenv("BIO_HARNESS_PLANNER_TEMPLATE_FASTPATH", "0") or "0").strip().lower()
        if not raw:
            return False
        return raw in {"1", "true", "yes", "on"}

    def _planner_template_fastpath_candidate(
        self,
        *,
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not self._planner_template_fastpath_enabled():
            return None, {"why": "fastpath_disabled"}
        if _is_empty_contract(contract):
            return None, {"why": "empty_contract"}
        compiler_plan, compiler_meta = self._planner_protocol_compiler_fastpath_candidate(
            contract=contract
        )
        if isinstance(compiler_plan, dict):
            return compiler_plan, compiler_meta
        plan, action, details = self._build_contract_template_repair("runtime_step_failure")
        if not isinstance(plan, dict):
            return None, {"why": "no_template_candidate", "action": action, "details": details}
        validation = self._assess_contract_for_plan(plan, contract)
        if not validation.get("passed", False):
            return None, {
                "why": "template_contract_failed",
                "action": action,
                "details": details,
                "contract_validation": validation,
            }
        meta = {
            "why": "template_fastpath_selected",
            "action": action,
            "details": details,
            "contract_validation": validation,
        }
        return plan, meta

    def _planner_protocol_compiler_fastpath_candidate(
        self,
        *,
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Return a deterministic compiler-backed fastpath plan when available."""

        if not bool(self.run.get("protocol_repair_enabled", False)):
            return None, {"why": "protocol_template_assistance_disabled"}
        analysis_spec = (
            self.run.get("analysis_spec", {})
            if isinstance(self.run.get("analysis_spec", {}), dict)
            else {}
        )
        analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip()
        if analysis_type not in TEMPLATE_COMPILER_TYPES:
            return None, {"why": "no_template_compiler_for_analysis_type"}
        candidate_plan, repair_meta = deterministic_protocol_repair(
            {},
            analysis_spec=analysis_spec,
            selected_dir=self.cfg.selected_dir,
            data_root=self.cfg.data_root,
        )
        if not (isinstance(candidate_plan, dict) and isinstance(candidate_plan.get("plan", []), list) and candidate_plan.get("plan")):
            return None, {
                "why": "compiler_fastpath_unavailable",
                "analysis_type": analysis_type,
                "repair_meta": repair_meta,
            }
        validation = self._assess_contract_for_plan(candidate_plan, contract)
        if not validation.get("passed", False):
            return None, {
                "why": "compiler_fastpath_contract_failed",
                "analysis_type": analysis_type,
                "contract_validation": validation,
                "repair_meta": repair_meta,
            }
        return candidate_plan, {
            "why": "compiler_fastpath_selected",
            "analysis_type": analysis_type,
            "contract_validation": validation,
            "repair_meta": repair_meta,
        }

    def _adaptive_live_process_grace_seconds(self, *, active_tool_name: str, active_command: str) -> int:
        grace = int(self.cfg.live_process_grace_seconds)
        tool_key = str(active_tool_name or "").strip().lower()
        if tool_key:
            grace = max(grace, int(TOOL_STALL_GRACE_HINTS.get(tool_key, grace)))
        cmd_l = str(active_command or "").lower()
        if cmd_l:
            if "/bio_harness/pipeline_scripts/" in cmd_l:
                grace = max(grace, 3600)
            if "rscript " in cmd_l or "rscript\t" in cmd_l:
                grace = max(grace, 600)
            if "deseq2" in cmd_l or "edger" in cmd_l or "limma" in cmd_l:
                grace = max(grace, 600)
            if "seurat" in cmd_l:
                grace = max(grace, 900)
            if "scanpy" in cmd_l:
                grace = max(grace, 600)
            if "stringtie " in cmd_l or "stringtie_quant" in cmd_l:
                grace = max(grace, 600)
            if "bcftools mpileup" in cmd_l or "bcftools call" in cmd_l:
                grace = max(grace, 1800)
            if "freebayes" in cmd_l:
                grace = max(grace, 1800)
            if "gatk " in cmd_l or "haplotypecaller" in cmd_l or "mutect2" in cmd_l:
                grace = max(grace, 3600)
            if "spades.py" in cmd_l or "spades-hammer" in cmd_l:
                grace = max(grace, 3600)
            if "prokka " in cmd_l:
                grace = max(grace, 2400)
            if "star " in cmd_l or "hisat2 " in cmd_l or "bwa " in cmd_l or "minimap2 " in cmd_l:
                grace = max(grace, 2700)
            if "rmats" in cmd_l or "featurecounts" in cmd_l:
                grace = max(grace, 1800)
        return max(60, min(14400, int(grace)))

    def _stall_monitor_uses_full_idle_grace(self, *, active_tool_name: str, active_command: str) -> bool:
        tool_key = str(active_tool_name or "").strip().lower()
        if tool_key in {
            "subread_align",
            "star_align",
            "star_2pass_align",
            "hisat2_align",
            "bwa_mem_align",
            "bowtie2_align",
            "minimap2_align",
            "fastp_run",
            "spades_assemble",
            "prokka_annotate",
            "deseq2_run",
            "edger_run",
            "limma_voom_run",
            "dexseq_run",
            "scanpy_workflow",
            "sc_count_and_cluster",
            "stringtie_quant",
            "seurat_rscript_workflow",
        }:
            return True
        cmd_l = str(active_command or "").lower()
        if not cmd_l:
            return False
        if "/bio_harness/pipeline_scripts/" in cmd_l:
            return True
        probes = (
            "subjunc",
            "subread-align",
            "star ",
            "hisat2 ",
            "bwa ",
            "bowtie2 ",
            "minimap2 ",
            "fastp ",
            "featurecounts ",
            "gatk ",
            "spades.py",
            "spades-hammer",
            "prokka ",
            "rscript ",
            "deseq2",
            "edger",
            "limma",
            "seurat",
            "scanpy",
            "stringtie ",
        )
        return any(token in cmd_l for token in probes)

    def _is_model_server_connectivity_error(self, error_message: str) -> bool:
        msg = str(error_message or "").strip().lower()
        if not msg:
            return False
        probes = (
            "failed to connect to ollama",
            "cannot connect to ollama",
            "is ollama running",
            "openai-compatible",
            "failed to connect",
            "connection refused",
            "connection error",
            "connecterror",
        )
        return any(token in msg for token in probes)

    def _is_local_model_loopback_blocked(self, exc: Exception) -> bool:
        llm = getattr(self.orchestrator, "biollm", None)
        host = str(getattr(llm, "host", "") or "")
        if is_loopback_permission_error(host, exc):
            return True
        msg = str(exc or "").lower()
        return "local loopback access" in msg or "localhost network permission" in msg

    def _planner_isolate_process_enabled(self) -> bool:
        raw = str(os.getenv("BIO_HARNESS_PLANNER_ISOLATE_PROCESS", "1") or "1").strip().lower()
        if not raw:
            return True
        return raw not in {"0", "false", "no", "off"}

    def _planner_process_isolation_allowed(self) -> bool:
        """Return True when planner process isolation can preserve current behavior.

        Planner supervision normally spawns a fresh ``Orchestrator`` in a child
        process. If the live harness instance has an overridden ``think``
        callable, spawning a fresh worker would ignore that override and change
        semantics. In that case, fall back to the in-process planner path.
        """

        if not self._planner_isolate_process_enabled():
            return False
        think_callable = getattr(self.orchestrator, "think", None)
        if think_callable is None:
            return False
        bound_self = getattr(think_callable, "__self__", None)
        bound_func = getattr(think_callable, "__func__", None)
        return bound_self is self.orchestrator and bound_func is Orchestrator.think

    def _planner_process_start_method(self) -> str:
        """Return the safest multiprocessing start method for planner workers."""

        try:
            available_methods = {
                str(method).strip().lower()
                for method in mp.get_all_start_methods()
                if str(method).strip()
            }
        except Exception:
            available_methods = set()
        if not available_methods:
            return "spawn"

        preferred_methods = ["fork", "spawn"]
        if sys.platform == "darwin":
            # Planner workers run alongside heartbeat and monitoring threads; on
            # macOS a clean spawn is materially safer than forking that state.
            preferred_methods = ["spawn", "fork"]
        for method in preferred_methods:
            if method in available_methods:
                return method
        return sorted(available_methods)[0]

    def _planner_prewarm_enabled(self) -> bool:
        raw = str(os.getenv("BIO_HARNESS_PLANNER_PREWARM", "1") or "1").strip().lower()
        if not raw:
            return True
        return raw not in {"0", "false", "no", "off"}

    def _planner_prewarm_timeout_seconds(self) -> float:
        raw = str(os.getenv("BIO_HARNESS_PLANNER_PREWARM_TIMEOUT_SECONDS", "2") or "2").strip()
        try:
            val = float(raw)
        except Exception:
            val = 2.0
        return max(1.0, min(30.0, val))

    def _planner_prewarm_mode(self) -> str:
        raw = str(os.getenv("BIO_HARNESS_PLANNER_PREWARM_MODE", "availability") or "availability").strip().lower()
        if raw in {"chat", "generate"}:
            return "chat"
        return "availability"

    def _maybe_prewarm_planner(self) -> None:
        if self._planner_prewarm_attempted:
            return
        self._planner_prewarm_attempted = True
        if not self._planner_prewarm_enabled():
            return
        llm = getattr(self.orchestrator, "biollm", None)
        model_name = str(getattr(llm, "model_name", "") or "").strip()
        if not model_name:
            return
        started = time.time()
        timeout_s = self._planner_prewarm_timeout_seconds()
        ok = False
        err = ""
        mode = self._planner_prewarm_mode()
        try:
            ok, err = llm.prewarm(mode=mode, timeout_seconds=float(timeout_s))
        except Exception as exc:
            err = str(exc).strip()

        elapsed = round(max(0.0, time.time() - started), 3)
        if (not ok) and ("timed out" in err.lower() or "timeout" in err.lower()):
            self._append_event(
                step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_PREWARM_TIMEOUT",
                severity="warning",
                payload={"timeout_seconds": float(timeout_s), "elapsed_seconds": elapsed, "error": err},
            )
            return
        self._append_event(
            step_id=None,
                agent="PlannerSupervisor",
                event_type="PLANNER_PREWARM_FINISHED",
                severity="info" if ok else "warning",
                payload={"ok": bool(ok), "elapsed_seconds": elapsed, "error": err, "mode": mode},
            )

    def _planner_attempt_timeout_seconds(self, *, strategy: str = "", prompt: str = "") -> int:
        raw = str(os.getenv("BIO_HARNESS_PLANNER_ATTEMPT_TIMEOUT_SECONDS", "") or "").strip()
        if raw:
            try:
                val = int(float(raw))
            except Exception:
                val = 0
            if val > 0:
                return max(1, min(1800, val))

        strategy_key = str(strategy or "").strip().lower()
        strategy_env_map = {
            "direct_user_prompt": "BIO_HARNESS_PLANNER_TIMEOUT_DIRECT_SECONDS",
            "contract_focus_prompt": "BIO_HARNESS_PLANNER_TIMEOUT_CONTRACT_FOCUS_SECONDS",
            "contract_repair_prompt": "BIO_HARNESS_PLANNER_TIMEOUT_CONTRACT_REPAIR_SECONDS",
            "preexecution_contract_repair": "BIO_HARNESS_PLANNER_TIMEOUT_PREEXEC_CONTRACT_SECONDS",
            "preexecution_protocol_repair": "BIO_HARNESS_PLANNER_TIMEOUT_PREEXEC_PROTOCOL_SECONDS",
            "preexecution_semantic_repair": "BIO_HARNESS_PLANNER_TIMEOUT_PREEXEC_SEMANTIC_SECONDS",
        }
        strategy_env = strategy_env_map.get(strategy_key, "")
        if not strategy_env and strategy_key.startswith("runtime_repair_"):
            strategy_env = "BIO_HARNESS_PLANNER_TIMEOUT_RUNTIME_REPAIR_SECONDS"
        if strategy_env:
            raw_strategy = str(os.getenv(strategy_env, "") or "").strip()
            if raw_strategy:
                try:
                    val = int(float(raw_strategy))
                except Exception:
                    val = 0
                if val > 0:
                    return max(1, min(1800, val))

        llm_raw = str(os.getenv("BIO_HARNESS_LLM_TIMEOUT_SECONDS", "90") or "90").strip()
        try:
            llm_timeout = int(float(llm_raw))
        except Exception:
            llm_timeout = 90
        llm_timeout = max(15, min(1800, llm_timeout))

        # Complex grounded planning and repair prompts are materially slower than a first-pass plan.
        strategy_floor_map = {
            "direct_user_prompt": 900,
            "contract_focus_prompt": 900,
            "contract_repair_prompt": 900,
            "preexecution_contract_repair": 900,
            "preexecution_protocol_repair": 900,
            "preexecution_semantic_repair": 900,
        }
        floor = strategy_floor_map.get(strategy_key, 180)
        if strategy_key.startswith("runtime_repair_"):
            floor = 900
        if self._is_simple_grounded_planner_shape():
            simple_floor_map = {
                "direct_user_prompt": 90,
                "contract_focus_prompt": 120,
                "contract_repair_prompt": 120,
            }
            floor = min(floor, simple_floor_map.get(strategy_key, floor))

        timeout_s = max(llm_timeout + 15, floor)

        prompt_chars = len(str(prompt or ""))
        if prompt_chars > 4000:
            timeout_s += min(120, ((prompt_chars - 4000) // 2000 + 1) * 15)

        analysis_spec = self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {}
        protocol_grounding = analysis_spec.get("protocol_grounding", {}) if isinstance(analysis_spec.get("protocol_grounding", {}), dict) else {}
        if bool(protocol_grounding.get("grounded", False)):
            timeout_s += 30
        if self._strict_llm_planning_enabled():
            timeout_s += 30
        two_stage_mode = str(os.getenv("BIO_HARNESS_PLANNER_TWO_STAGE_MODE", "auto") or "auto").strip().lower()
        if two_stage_mode == "always":
            timeout_s += 45

        return max(1, min(1800, int(timeout_s)))

    def _planner_progress_grace_seconds(self, *, base_timeout: int = 0) -> int:
        """Return the extra liveness window granted after planner trace progress."""

        raw = str(os.getenv("BIO_HARNESS_PLANNER_PROGRESS_GRACE_SECONDS", "") or "").strip()
        if raw:
            try:
                val = int(float(raw))
            except Exception:
                val = 0
            return max(0, min(1800, val))

        default_grace = 90
        if base_timeout > 0:
            default_grace = max(45, min(120, int(base_timeout)))
        return max(0, min(1800, default_grace))

    def _planner_progress_max_extension_seconds(self, *, base_timeout: int = 0) -> int:
        """Return the maximum timeout extension allowed for active planner progress."""

        raw = str(os.getenv("BIO_HARNESS_PLANNER_PROGRESS_MAX_EXTENSION_SECONDS", "") or "").strip()
        if raw:
            try:
                val = int(float(raw))
            except Exception:
                val = 0
            return max(0, min(1800, val))

        if base_timeout <= 0:
            return 0
        default_extension = max(180, min(600, int(base_timeout) * 2))
        return max(0, min(1800, default_extension))

    def _planner_progress_poll_seconds(self) -> float:
        """Return how often planner trace progress should be probed."""

        raw = str(os.getenv("BIO_HARNESS_PLANNER_PROGRESS_POLL_SECONDS", "0.5") or "0.5").strip()
        try:
            val = float(raw)
        except Exception:
            val = 0.5
        return max(0.1, min(5.0, val))

    def _is_simple_grounded_planner_shape(self) -> bool:
        """Return True for small grounded workflows that should fail fast.

        One-step or two-step grounded assay templates should not inherit the
        same planning timeout floor as long hierarchical tasks like evolution.
        """
        analysis_spec = self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {}
        protocol_grounding = (
            analysis_spec.get("protocol_grounding", {})
            if isinstance(analysis_spec.get("protocol_grounding", {}), dict)
            else {}
        )
        if not bool(protocol_grounding.get("grounded", False)):
            return False
        plan_skeleton = analysis_spec.get("plan_skeleton", [])
        if not isinstance(plan_skeleton, list):
            return False
        skeleton_len = len(plan_skeleton)
        if skeleton_len < 1 or skeleton_len > 2:
            return False
        required_tools = [
            str(tool).strip()
            for tool in (protocol_grounding.get("required_tools", []) or [])
            if str(tool).strip()
        ]
        return len(required_tools) <= 2

    def _planner_timeout_failopen_enabled(self) -> bool:
        if self._strict_llm_planning_enabled():
            return False
        raw = str(os.getenv("BIO_HARNESS_PLANNER_TIMEOUT_FAILOPEN", "1") or "1").strip().lower()
        if not raw:
            return True
        return raw not in {"0", "false", "no", "off"}

    def _planner_timeout_failopen_candidate(
        self,
        *,
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
        if not self._planner_timeout_failopen_enabled():
            return None, "planner_failopen_disabled", {"why": "BIO_HARNESS_PLANNER_TIMEOUT_FAILOPEN=0"}
        if _is_empty_contract(contract):
            return None, "planner_failopen_skipped", {"why": "empty_contract"}

        fallback_plan, fallback_action, fallback_details = self._build_contract_template_repair("runtime_step_failure")
        if not isinstance(fallback_plan, dict):
            return None, "planner_failopen_unavailable", {
                "why": "no_timeout_failopen_template",
                "fallback_action": fallback_action,
                "fallback_details": fallback_details,
            }

        validation = self._assess_contract_for_plan(fallback_plan, contract)
        if not validation.get("passed", False):
            return None, "planner_failopen_contract_failed", {
                "why": "timeout_failopen_contract_validation_failed",
                "contract_validation": validation,
                "fallback_action": fallback_action,
                "fallback_details": fallback_details,
            }

        return fallback_plan, "timeout_failopen_template", {
            "why": "timeout_failopen_template_selected",
            "fallback_action": fallback_action,
            "fallback_details": fallback_details,
            "contract_validation": validation,
        }

    def _planner_contract_focus_prompt(
        self,
        *,
        contract: dict[str, Any],
        latest_validation: dict[str, Any],
        prior_plan: dict[str, Any] | None,
    ) -> str:
        repair_context = self._build_repair_prompt_context(
            failure_class="contract_mismatch",
            reason="contract validation failed before execution",
            validation=latest_validation,
            focus_mode="full_plan",
        )
        return (
            "Create an executable bioinformatics plan as JSON.\n"
            "Output ONLY JSON with keys `thought_process` and `plan`.\n"
            "Prioritize a reliable run-ready plan over exploratory options.\n\n"
            f"User request:\n{self.cfg.prompt}\n\n"
            f"Required contract:\n{_json_dumps_safe(contract, indent=2)}\n\n"
            f"Latest contract gaps:\n{_json_dumps_safe(latest_validation, indent=2)}\n\n"
            "Rules:\n"
            "- Use only runnable tools/steps.\n"
            "- Include explicit group/control/treatment handling when requested.\n"
            "- Treat `required_tool_hints` as hard requirements.\n"
            "- Treat `required_output_paths` as requested final deliverables, not undocumented tool arguments.\n"
            "- Every selected-dir input must be produced by an earlier step or live under an earlier declared output root.\n"
            "- Reuse exact produced artifact paths instead of inventing renamed aliases.\n"
            "- Prefer deterministic scripts/wrappers over ad-hoc shell logic.\n"
            "- Keep step count compact and execution-oriented.\n\n"
            f"Focused repair context:\n{_json_dumps_safe(repair_context, indent=2)}\n\n"
            f"Prior plan (if any):\n{_json_dumps_safe(prior_plan or {}, indent=2)}\n"
        )
