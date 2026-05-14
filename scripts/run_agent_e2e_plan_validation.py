from __future__ import annotations

from scripts.run_agent_e2e_plan_application_support import plan_step_count
from scripts.run_agent_e2e_plan_bootstrap_support import (
    acquire_initial_plan,
    initialize_plan_preparation_state,
    validate_initial_plan_shape,
)
from scripts.run_agent_e2e_postplan_validation_support import (
    apply_fastq_rebinding_if_changed,
    apply_runtime_fallback_if_distinct,
    filter_missing_plan_inputs,
)
from scripts.run_agent_e2e_preexecution_repair_support import (
    assess_plan_semantic_guards_with_bash_placeholders,
)
from scripts.run_agent_e2e_validation_phase_support import (
    append_repair_applied_event,
    build_protocol_normalization_snapshot,
    format_strict_contract_validation_error,
    format_strict_protocol_grounding_error,
    format_strict_semantic_validation_error,
    protocol_normalization_debug_message,
    should_attempt_protocol_normalization,
)
from scripts.run_agent_e2e_support import (
    Any,
    TEMPLATE_COMPILER_TYPES,
    _assess_plan_semantic_guards,
    _emit,
    _infer_request_contract,
    _missing_exec_tools_for_plan,
    _missing_input_paths_for_plan,
    _missing_local_scripts_for_plan,
    _repair_missing_fastq_inputs_in_plan,
    assess_protocol_grounding,
    deterministic_protocol_repair,
    ranked_fallback_catalog_metadata,
)


class AgentE2EPlanValidationMixin:
    def _run_protocol_grounding_phase(
        self,
        *,
        strict_llm_planning: bool,
        blind_benchmark_policy: bool,
        planning_strict_benchmark_policy: bool,
    ) -> None:
        """Run protocol grounding validation and optional repair before execution."""

        analysis_spec = self._run_analysis_spec_dict()
        self.run["protocol_validation"] = assess_protocol_grounding(
            self._run_plan_dict(),
            analysis_spec,
        )
        if self.run["protocol_validation"].get("passed", False):
            return

        if blind_benchmark_policy:
            self.run["protocol_repair_skip_reason"] = self._benchmark_policy()
            _emit(
                f"Protocol template repair disabled by {self._benchmark_policy()} policy; keeping planner output unchanged.",
                quiet=self.cfg.quiet,
            )
        else:
            repaired, action, details = self._attempt_preexecution_protocol_repair(
                analysis_spec=analysis_spec,
                validation=self.run["protocol_validation"],
            )
            if repaired:
                _emit(f"Applied pre-execution protocol repair: {action}", quiet=self.cfg.quiet)
                analysis_spec = self._run_analysis_spec_dict()
                self.run["protocol_validation"] = assess_protocol_grounding(
                    self._run_plan_dict(),
                    analysis_spec,
                )
                append_repair_applied_event(
                    append_event=self._append_event,
                    run=self.run,
                    failure_class="protocol_grounding",
                    action=action,
                    details=details,
                )
            elif strict_llm_planning:
                analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip()
                if self._strict_protocol_grounding_must_raise(
                    analysis_type,
                    planning_strict_benchmark_policy=planning_strict_benchmark_policy,
                ):
                    raise ValueError(
                        format_strict_protocol_grounding_error(self.run["protocol_validation"])
                    )
                _emit(
                    f"[DEBUG] Deferring strict protocol grounding check: template compiler ({analysis_type}) will normalize plan",
                    quiet=self.cfg.quiet,
                )

        if blind_benchmark_policy and strict_llm_planning:
            analysis_type = str(self._run_analysis_spec_dict().get("analysis_type", "") or "").strip()
            if self._strict_protocol_grounding_must_raise(
                analysis_type,
                planning_strict_benchmark_policy=planning_strict_benchmark_policy,
            ):
                raise ValueError(
                    format_strict_protocol_grounding_error(self.run["protocol_validation"])
                )

    def _run_protocol_normalization_phase(
        self,
        *,
        blind_benchmark_policy: bool,
        planning_strict_benchmark_policy: bool,
    ) -> dict[str, Any]:
        """Run protocol normalization policy and return compiler metadata."""

        analysis_spec = self._run_analysis_spec_dict()
        snapshot = build_protocol_normalization_snapshot(
            analysis_spec,
            protocol_validation=self.run["protocol_validation"],
            template_compiler_types=TEMPLATE_COMPILER_TYPES,
        )
        _emit(
            protocol_normalization_debug_message(snapshot),
            quiet=self.cfg.quiet,
        )
        self.run["protocol_normalization_enabled"], norm_meta = (
            self._protocol_normalization_policy(
                blind_benchmark_policy=blind_benchmark_policy,
                has_compiler=snapshot.has_compiler,
                planning_strict_benchmark_policy=planning_strict_benchmark_policy,
                protocol_source_files=list(snapshot.protocol_source_files),
            )
        )
        if should_attempt_protocol_normalization(
            snapshot,
            normalization_enabled=self.run["protocol_normalization_enabled"],
        ):
            norm_candidate, norm_meta = deterministic_protocol_repair(
                self._run_plan_dict(),
                analysis_spec=analysis_spec,
                selected_dir=self.cfg.selected_dir,
                data_root=self.cfg.data_root,
            )
            _emit(
                "[DEBUG] Protocol normalization result: "
                f"changed={norm_meta.get('changed', False)}, "
                f"why={norm_meta.get('why', 'unknown')}",
                quiet=self.cfg.quiet,
            )
            if norm_meta.get("changed", False) and isinstance(norm_candidate, dict):
                if snapshot.has_compiler:
                    self.run["plan"] = norm_candidate
                    self.run["protocol_validation"] = {
                        "passed": True,
                        "reason": "template_compiler_authoritative",
                    }
                    _emit(
                        f"Accepted template-compiled plan ({snapshot.analysis_type})",
                        quiet=self.cfg.quiet,
                    )
                else:
                    post_norm_validation = assess_protocol_grounding(
                        norm_candidate,
                        analysis_spec,
                    )
                    if post_norm_validation.get("passed", False):
                        self.run["plan"] = norm_candidate
                        self.run["protocol_validation"] = post_norm_validation
                        _emit(
                            "Applied protocol normalization (path/param fix on passing plan)",
                            quiet=self.cfg.quiet,
                        )
                    else:
                        _emit(
                            "Protocol normalization rejected (would break validation); keeping original plan",
                            quiet=self.cfg.quiet,
                        )
        self.run["protocol_normalization_meta"] = norm_meta
        return {
            "analysis_type": snapshot.analysis_type,
            "has_compiler": snapshot.has_compiler,
            "norm_meta": norm_meta,
        }

    def _run_contract_validation_phase(
        self,
        *,
        contract: dict[str, Any],
        strict_llm_planning: bool,
        analysis_type: str,
        has_compiler: bool,
        norm_meta: dict[str, Any],
    ) -> None:
        """Run contract validation and optional contract repair before execution."""

        skip_contract_repair = has_compiler and norm_meta.get("changed", False)
        if skip_contract_repair:
            _emit(
                f"[DEBUG] Skipping contract repair: template compiler ({analysis_type}) already produced plan",
                quiet=self.cfg.quiet,
            )
        self.run["contract_validation"] = self._assess_contract_for_plan(
            self._run_plan_dict(),
            contract,
        )
        if self.run["contract_validation"].get("passed", False) or skip_contract_repair:
            return

        repaired, action, details = self._attempt_preexecution_contract_repair(
            contract=contract,
            validation=self.run["contract_validation"],
        )
        if repaired:
            _emit(f"Applied pre-execution contract repair: {action}", quiet=self.cfg.quiet)
            append_repair_applied_event(
                append_event=self._append_event,
                run=self.run,
                failure_class="contract_mismatch",
                action=action,
                details=details,
            )
        if strict_llm_planning and not self.run["contract_validation"].get("passed", False):
            raise ValueError(
                format_strict_contract_validation_error(self.run["contract_validation"])
            )

    def _run_semantic_validation_phase(self, *, strict_llm_planning: bool) -> None:
        """Run semantic validation and optional semantic repair before execution."""

        resolved_plan, semantic_validation, placeholder_sidecar = (
            assess_plan_semantic_guards_with_bash_placeholders(
                plan=self._run_plan_dict(),
                assess_semantic_guards=lambda candidate: _assess_plan_semantic_guards(
                    candidate,
                    analysis_spec=self._run_analysis_spec_dict(),
                    cwd=self.cfg.selected_dir,
                ),
                path_graph=self.path_graph,
                selected_dir=str(self.cfg.selected_dir),
            )
        )
        self.run["plan"] = resolved_plan
        self.run["bash_placeholder_resolutions"] = placeholder_sidecar
        self.run["semantic_validation"] = semantic_validation
        if self.run["semantic_validation"].get("passed", False):
            return

        repaired, action, details = self._attempt_preexecution_semantic_repair(
            analysis_spec=self._run_analysis_spec_dict(),
            validation=self.run["semantic_validation"],
        )
        if repaired:
            _emit(f"Applied pre-execution semantic repair: {action}", quiet=self.cfg.quiet)
            resolved_plan, semantic_validation, placeholder_sidecar = (
                assess_plan_semantic_guards_with_bash_placeholders(
                    plan=self._run_plan_dict(),
                    assess_semantic_guards=lambda candidate: _assess_plan_semantic_guards(
                        candidate,
                        analysis_spec=self._run_analysis_spec_dict(),
                        cwd=self.cfg.selected_dir,
                    ),
                    path_graph=self.path_graph,
                    selected_dir=str(self.cfg.selected_dir),
                )
            )
            self.run["plan"] = resolved_plan
            self.run["bash_placeholder_resolutions"] = placeholder_sidecar
            self.run["semantic_validation"] = semantic_validation
            append_repair_applied_event(
                append_event=self._append_event,
                run=self.run,
                failure_class="semantic_validation",
                action=action,
                details=details,
            )
            return

        if strict_llm_planning or self._planning_strict_benchmark_policy():
            self.run["validation_block_detected"] = True
            raise ValueError(
                format_strict_semantic_validation_error(
                    benchmark_policy=self._benchmark_policy(),
                    validation=self.run["semantic_validation"],
                )
            )

    def _apply_runtime_fallback_if_distinct(
        self,
        *,
        contract: dict[str, Any],
        fallback_plan: dict[str, Any] | None,
        fallback_action: str,
        fallback_details: dict[str, Any],
        message: str,
        detail_key: str,
        detail_value: list[str],
    ) -> None:
        """Apply a deterministic fallback plan when it differs from the current plan."""

        apply_runtime_fallback_if_distinct(
            run=self.run,
            current_plan=self._run_plan_dict(),
            contract=contract,
            fallback_plan=fallback_plan,
            fallback_action=fallback_action,
            fallback_details=fallback_details,
            message=message,
            detail_key=detail_key,
            detail_value=detail_value,
            quiet=self.cfg.quiet,
            assess_contract_for_plan=self._assess_contract_for_plan,
            append_event=self._append_event,
        )

    def _run_missing_tool_validation_phase(
        self,
        *,
        contract: dict[str, Any],
        strict_llm_planning: bool,
    ) -> None:
        """Validate referenced executable tools before execution."""

        missing_plan_tools = _missing_exec_tools_for_plan(self._run_plan_dict())
        if not missing_plan_tools:
            return
        existing = set(self.run.get("missing_tools_detected", []))
        self.run["missing_tools_detected"] = sorted(existing.union(missing_plan_tools))
        if strict_llm_planning:
            raise ValueError(
                "Strict LLM planning is enabled and plan references unavailable executable tools: "
                + ", ".join(missing_plan_tools)
            )
        fallback_plan, fallback_action, fallback_details = (
            self._build_contract_template_repair("runtime_step_failure")
        )
        self._apply_runtime_fallback_if_distinct(
            contract=contract,
            fallback_plan=fallback_plan,
            fallback_action=fallback_action,
            fallback_details=fallback_details,
            message=(
                "Applied deterministic fallback due to missing executable tools: "
                + ", ".join(missing_plan_tools)
            ),
            detail_key="missing_plan_tools",
            detail_value=missing_plan_tools,
        )

    def _run_fastq_rebinding_phase(self) -> None:
        """Rebind guessed FASTQ inputs to discovered task-local files."""

        fastq_repaired_plan, fastq_repair_meta = _repair_missing_fastq_inputs_in_plan(
            self._run_plan_dict(),
            self.cfg.selected_dir,
            self.cfg.data_root,
        )
        apply_fastq_rebinding_if_changed(
            run=self.run,
            repaired_plan=fastq_repaired_plan,
            repair_meta=fastq_repair_meta,
            quiet=self.cfg.quiet,
            append_event=self._append_event,
        )

    def _filter_missing_plan_inputs(self, missing_plan_inputs: list[str]) -> list[str]:
        """Drop missing inputs that are actually outputs of prior plan steps."""

        return filter_missing_plan_inputs(
            missing_plan_inputs,
            plan=self._run_plan_dict(),
            selected_dir=self.cfg.selected_dir,
            quiet=self.cfg.quiet,
        )

    def _run_missing_input_validation_phase(
        self,
        *,
        contract: dict[str, Any],
        strict_llm_planning: bool,
    ) -> None:
        """Validate missing input paths after FASTQ rebinding."""

        missing_plan_inputs = _missing_input_paths_for_plan(
            self._run_plan_dict(),
            self.cfg.selected_dir,
            self.cfg.data_root,
        )
        missing_plan_inputs = self._filter_missing_plan_inputs(missing_plan_inputs)
        if not missing_plan_inputs:
            return

        deferred = False
        if strict_llm_planning:
            analysis_type = str(
                self._run_analysis_spec_dict().get("analysis_type", "") or ""
            ).strip()
            if analysis_type in TEMPLATE_COMPILER_TYPES:
                _emit(
                    "[DEBUG] Deferring missing-input check: template compiler "
                    f"({analysis_type}) plan has intermediate outputs",
                    quiet=self.cfg.quiet,
                )
                deferred = True
            else:
                raise ValueError(
                    "Strict LLM planning is enabled and plan references missing inputs: "
                    + ", ".join(missing_plan_inputs[:8])
                )
        if deferred:
            return

        fallback_plan, fallback_action, fallback_details = (
            self._build_contract_template_repair("runtime_step_failure")
        )
        self._apply_runtime_fallback_if_distinct(
            contract=contract,
            fallback_plan=fallback_plan,
            fallback_action=fallback_action,
            fallback_details=fallback_details,
            message=(
                "Applied deterministic fallback due to missing plan inputs: "
                + ", ".join(missing_plan_inputs[:4])
            ),
            detail_key="missing_plan_inputs",
            detail_value=missing_plan_inputs,
        )

    def _run_missing_script_validation_phase(
        self,
        *,
        contract: dict[str, Any],
        strict_llm_planning: bool,
    ) -> None:
        """Validate local script references in the executable plan."""

        missing_plan_scripts = _missing_local_scripts_for_plan(
            self._run_plan_dict(),
            self.cfg.selected_dir,
        )
        if not missing_plan_scripts:
            return
        if strict_llm_planning:
            raise ValueError(
                "Strict LLM planning is enabled and plan references missing local scripts: "
                + ", ".join(missing_plan_scripts[:8])
            )
        fallback_plan, fallback_action, fallback_details = (
            self._build_contract_template_repair("runtime_step_failure")
        )
        self._apply_runtime_fallback_if_distinct(
            contract=contract,
            fallback_plan=fallback_plan,
            fallback_action=fallback_action,
            fallback_details=fallback_details,
            message=(
                "Applied deterministic fallback due to missing local scripts: "
                + ", ".join(missing_plan_scripts[:4])
            ),
            detail_key="missing_plan_scripts",
            detail_value=missing_plan_scripts,
        )

    def _run_postplan_runtime_validation_phase(
        self,
        *,
        contract: dict[str, Any],
        strict_llm_planning: bool,
    ) -> None:
        """Run pre-execution runtime validations after plan-phase checks."""

        self._run_missing_tool_validation_phase(
            contract=contract,
            strict_llm_planning=strict_llm_planning,
        )
        self._run_fastq_rebinding_phase()
        self._run_missing_input_validation_phase(
            contract=contract,
            strict_llm_planning=strict_llm_planning,
        )
        self._run_missing_script_validation_phase(
            contract=contract,
            strict_llm_planning=strict_llm_planning,
        )

    def _prepare_plan(self) -> None:
        """Prepare, normalize, and validate the executable plan before execution."""

        strict_llm_planning = self._strict_llm_planning_enabled()
        blind_benchmark_policy = self._blind_benchmark_policy()
        planning_strict_benchmark_policy = self._planning_strict_benchmark_policy()
        contract = _infer_request_contract(self.cfg.prompt, self.catalog)
        self.run["plan_contract"] = contract
        self._prepare_analysis_spec(contract)
        self._refresh_environment_snapshot()
        catalog_summary = ranked_fallback_catalog_metadata()
        initialize_plan_preparation_state(
            self.run,
            catalog_summary=catalog_summary,
        )
        acquisition = acquire_initial_plan(
            cfg=self.cfg,
            run=self.run,
            contract=contract,
            strict_llm_planning=strict_llm_planning,
            generate_plan_with_supervision=self._generate_plan_with_supervision,
            build_contract_template_repair=self._build_contract_template_repair,
            is_local_model_loopback_blocked=self._is_local_model_loopback_blocked,
            note_failure_signature=self._note_failure_signature,
            append_event=self._append_event,
            emit=_emit,
            biollm=self.orchestrator.biollm,
        )
        if acquisition.planner_strategy_used:
            self.run["planner_strategy_used"] = acquisition.planner_strategy_used
        plan = acquisition.plan
        validate_initial_plan_shape(plan)
        plan, meta, fc_meta = self._normalize_plan_for_execution(plan)
        if meta.get("changed", False):
            _emit(f"Canonicalized plan before execution: {meta.get('diff_summary', {})}", quiet=self.cfg.quiet)
        if fc_meta.get("changed", False):
            _emit(
                f"Normalized featureCounts to paired-end mode: {fc_meta.get('diff_summary', {})}",
                quiet=self.cfg.quiet,
            )
        self.run["plan"] = plan
        self._run_protocol_grounding_phase(
            strict_llm_planning=strict_llm_planning,
            blind_benchmark_policy=blind_benchmark_policy,
            planning_strict_benchmark_policy=planning_strict_benchmark_policy,
        )
        normalization_meta = self._run_protocol_normalization_phase(
            blind_benchmark_policy=blind_benchmark_policy,
            planning_strict_benchmark_policy=planning_strict_benchmark_policy,
        )
        self._run_contract_validation_phase(
            contract=contract,
            strict_llm_planning=strict_llm_planning,
            analysis_type=str(normalization_meta.get("analysis_type", "") or ""),
            has_compiler=bool(normalization_meta.get("has_compiler", False)),
            norm_meta=(
                normalization_meta.get("norm_meta", {})
                if isinstance(normalization_meta.get("norm_meta", {}), dict)
                else {}
            ),
        )
        self._run_semantic_validation_phase(
            strict_llm_planning=strict_llm_planning,
        )
        self._run_postplan_runtime_validation_phase(
            contract=contract,
            strict_llm_planning=strict_llm_planning,
        )
        self.run["step_statuses"] = ["pending"] * plan_step_count(self.run.get("plan", {}))
        self.run["next_step_idx"] = 0
        self._record_graph_selection()
        self.run["status"] = "planned"
