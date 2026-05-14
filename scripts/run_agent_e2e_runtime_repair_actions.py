from __future__ import annotations

from bio_harness.core.runtime_repair_support import (
    build_runtime_result_payload,
    try_auto_install_tools,
    try_auto_setup_isolated_tools,
    write_runtime_receipt,
)
from scripts.run_agent_e2e_runtime_repair_policy_support import (
    apply_runtime_mutation_repair_ladder,
    direct_skill_smoke_guard,
    unrecoverable_signature_guard,
)
from scripts.run_agent_e2e_runtime_cycle_support import (
    apply_successful_repair_cycle,
    record_preflight_failure,
)
from scripts.run_agent_e2e_runtime_repair_branch_support import (
    maybe_resume_from_existing_artifacts,
    maybe_substitute_failed_tool_from_context,
    maybe_substitute_missing_tool,
    merge_resume_metadata,
)
from scripts.run_agent_e2e_runtime_replan_support import (
    evaluate_runtime_replan_candidate,
)
from scripts.run_agent_e2e_support import (
    Any,
    PROJECT_ROOT,
    Path,
    TOOL_EQUIVALENCE_MAP,
    _apply_repaired_plan_with_resume,
    _clean_stale_tmp_cache_paths,
    _discover_fastq_files,
    _emit,
    _missing_local_scripts_for_plan,
    _now_utc_iso,
    _repair_missing_references_in_plan,
    can_attempt_repair,
    canonicalize_execution_plan,
    classify_failure,
    classify_failure_with_context,
    json,
)


class AgentE2ERuntimeRepairActionMixin:
    def _write_runtime_receipt(self, prefix: str, payload: dict[str, Any]) -> str | None:
        return write_runtime_receipt(
            self.run,
            prefix=prefix,
            payload=payload,
        )

    def _try_auto_install_tools(self) -> tuple[bool, str]:
        return try_auto_install_tools(
            self.run,
            project_root=PROJECT_ROOT,
        )

    def _try_auto_setup_isolated_tools(self) -> tuple[bool, str]:
        return try_auto_setup_isolated_tools(
            self.run,
            project_root=PROJECT_ROOT,
        )

    def _maybe_replan_for_failure(self, failure_class: str, reason: str) -> tuple[bool, str, dict[str, Any]]:
        if self._direct_skill_smoke_run():
            return False, "direct_skill_smoke_repair_disabled", {
                "why": "direct_skill_smoke_requires_reporting_the_requested_skill_result_without_workflow_repair",
                "failure_class": failure_class,
            }
        if (
            failure_class == "runtime_step_failure"
            and self._blind_benchmark_policy()
            and bool(self.run.get("execution_started", False))
        ):
            return False, "benchmark_no_late_replan", {
                "why": "blind benchmark mode disables late runtime replanning after execution has started",
                "failure_class": failure_class,
                "execution_started": True,
            }
        if not self.cfg.allow_replan:
            return False, "replan_disabled", {"why": "allow_replan=false"}

        if failure_class in {"contract_mismatch", "runtime_step_failure"} and (
            failure_class == "contract_mismatch" or bool(self.run.get("planner_timeout_detected", False))
        ):
            fallback_plan, fallback_action, fallback_details = self._build_contract_template_repair(failure_class)
            if fallback_plan is not None:
                self.run["contract_validation"] = fallback_details.get(
                    "contract_validation",
                    self.run.get("contract_validation", {}),
                )
                resume_meta = _apply_repaired_plan_with_resume(self.run, fallback_plan)
                return True, fallback_action, {**fallback_details, "resume": resume_meta}

        before_steps = len((self.run.get("plan") or {}).get("plan", [])) if isinstance(self.run.get("plan"), dict) else 0
        contract = self.run.get("plan_contract", {})
        attempt_rows: list[dict[str, Any]] = []
        last_guard: dict[str, Any] = {}
        last_validation: dict[str, Any] = {}
        last_failure_action = "replan_failed"

        for attempt_num, focus_mode in enumerate(self._runtime_replan_focus_modes(), start=1):
            strategy = f"runtime_repair_{failure_class}_{focus_mode}_{attempt_num}"
            prompt = self._replan_prompt_for_failure(
                failure_class,
                reason,
                focus_mode=focus_mode,
                attempt_num=attempt_num,
            )
            try:
                candidate = self._supervised_model_replan(
                    prompt=prompt,
                    strategy=strategy,
                )
            except Exception as exc:
                attempt_rows.append(
                    {
                        "attempt": attempt_num,
                        "focus_mode": focus_mode,
                        "strategy": strategy,
                        "status": "exception",
                        "reason": str(exc),
                    }
                )
                last_failure_action = "replan_failed"
                continue

            evaluation = evaluate_runtime_replan_candidate(
                run=self.run,
                candidate=candidate,
                failure_class=failure_class,
                focus_mode=focus_mode,
                attempt_num=attempt_num,
                strategy=strategy,
                before_steps=before_steps,
                data_root=str(self.cfg.data_root),
                selected_dir=self.cfg.selected_dir,
                canonicalize_plan=canonicalize_execution_plan,
                prune_candidate=self._prune_and_bound_replan_candidate,
                missing_scripts_for_plan=_missing_local_scripts_for_plan,
                assess_contract=lambda plan: self._assess_repair_contract_for_plan(plan, contract),
                apply_repaired_plan_with_resume=_apply_repaired_plan_with_resume,
            )
            attempt_rows.append(evaluation.attempt_row)
            last_guard = evaluation.guard
            last_validation = evaluation.validation
            if not evaluation.applied:
                reason_code = str(evaluation.attempt_row.get("reason", "") or "")
                failure_action_map = {
                    "model returned empty/non-actionable plan": "replan_invalid",
                    "step_growth_exceeded": "replan_step_growth_exceeded",
                    "heavy_steps_reintroduced": "replan_reintroduces_heavy_steps",
                    "plan became non-actionable after prune": "replan_invalid_after_prune",
                    "missing_local_scripts": "replan_missing_local_scripts",
                    "contract_validation_failed": "replan_contract_failed",
                }
                last_failure_action = failure_action_map.get(reason_code, "replan_failed")
                continue

            self.run["contract_validation"] = evaluation.details.get(
                "contract_validation",
                self.run.get("contract_validation", {}),
            )
            return True, "replan_with_failure_context", {
                **evaluation.details,
                "runtime_replan_attempts": attempt_rows,
            }

        fallback_plan, fallback_action, fallback_details = self._build_contract_template_repair(failure_class)
        if fallback_plan is not None:
            self.run["contract_validation"] = fallback_details.get(
                "contract_validation",
                self.run.get("contract_validation", {}),
            )
            resume_meta = _apply_repaired_plan_with_resume(self.run, fallback_plan)
            return True, fallback_action, {
                **fallback_details,
                "runtime_replan_attempts": attempt_rows,
                "resume": resume_meta,
            }
        return False, last_failure_action, {
            "why": "all_runtime_replan_attempts_failed",
            "runtime_replan_attempts": attempt_rows,
            "last_guard": last_guard,
            "last_validation": last_validation,
        }

    def _apply_repair_action(self, failure_class: str) -> tuple[bool, str, dict[str, Any]]:
        repair_scope = self._repair_scope_summary()
        details: dict[str, Any] = {"why": f"repair_map:{failure_class}", "diff_summary": {}, "repair_scope": repair_scope}
        repaired, action, guarded_details = direct_skill_smoke_guard(
            failure_class=failure_class,
            is_direct_skill_smoke=self._direct_skill_smoke_run(),
            details=details,
        )
        if action:
            return repaired, action, guarded_details
        details = guarded_details
        artifact_signatures = self._augment_failure_signatures_from_artifacts()
        signatures = {str(x).strip().lower() for x in self.run.get("failure_signatures", []) if str(x).strip()}
        if artifact_signatures:
            details["artifact_signatures"] = artifact_signatures
        repaired, action, guarded_details = unrecoverable_signature_guard(
            signatures=signatures,
            details=details,
        )
        if action:
            return repaired, action, guarded_details
        details = guarded_details

        repaired, action, ladder_details = apply_runtime_mutation_repair_ladder(
            failure_class=failure_class,
            details=details,
            runtime_plan_mutation_guard=self._runtime_plan_mutation_repair_guard,
            repair_steps=[
                ("signature_repair_bash_placeholders", self._apply_bash_placeholder_signature_repair),
                ("deterministic_repair_bcftools_view_cli", self._apply_bcftools_view_cli_repair),
                ("signature_repair_bcftools_expression_namespace", self._apply_bcftools_expression_signature_repair),
                ("deterministic_repair_bcftools_isec_output", self._apply_bcftools_isec_output_repair),
                ("signature_repair_snpeff_codon_table", self._apply_snpeff_codon_table_signature_repair),
                ("typed_output_adapter_repair", self._apply_output_adapter_tail_repair),
                ("signature_repair_shared_variant_export", self._apply_vcf_shared_export_signature_repair),
                ("signature_repair_featurecounts_paired_mode", self._apply_featurecounts_paired_signature_repair),
                ("signature_repair_deseq2_metadata", self._apply_deseq2_metadata_signature_repair),
                ("signature_repair_flye_resource_settings", self._apply_flye_resource_signature_repair),
            ],
        )
        if action:
            return repaired, action, ladder_details
        details = ladder_details

        # --- Artifact-aware recovery: skip step if outputs already exist ---
        if failure_class in {"runtime_step_failure", "unknown_failure"}:
            ctx = classify_failure_with_context(
                self.run,
                selected_dir=str(self.cfg.selected_dir),
                plan=self.run.get("plan") or {},
            )
            details["recovery_context"] = ctx
            repaired, action, branch_details = maybe_resume_from_existing_artifacts(
                self.run,
                selected_dir=str(self.cfg.selected_dir),
                recovery_context=ctx,
                emit=_emit,
                quiet=self.cfg.quiet,
            )
            if repaired:
                details.update(branch_details)
                return True, action, details

            repaired, action, branch_details = maybe_substitute_failed_tool_from_context(
                self.run,
                recovery_context=ctx,
                emit=_emit,
                quiet=self.cfg.quiet,
            )
            if repaired:
                details.update(branch_details)
                return True, action, details

        if failure_class == "tool_missing":
            missing_tools = list(self.run.get("missing_tools_detected", []))
            repaired, action, branch_details = maybe_substitute_missing_tool(
                self.run,
                missing_tools=missing_tools,
                tool_equivalence_map=TOOL_EQUIVALENCE_MAP,
                emit=_emit,
                quiet=self.cfg.quiet,
            )
            if repaired:
                details.update(branch_details)
                return True, action, details
            if self.cfg.auto_install_missing_tools:
                ok, action = self._try_auto_install_tools()
                details["tools"] = missing_tools
                if ok:
                    return ok, action, details
            if self.cfg.auto_setup_isolated_tools:
                ok, action = self._try_auto_setup_isolated_tools()
                details["tools"] = missing_tools
                return ok, action, details
            return False, "tool_missing_no_remediation", details

        if failure_class == "missing_reference":
            missing_refs = list(self.run.get("missing_reference_detected", []))
            repair_res = _repair_missing_references_in_plan(
                self.run.get("plan") or {},
                missing_refs,
                str(self.run.get("user_request", "")),
            )
            if repair_res.get("changed", False):
                details.update(
                    {
                        "replacements": repair_res.get("replacements", []),
                        "diff_summary": {"replacement_count": len(repair_res.get("replacements", []))},
                    }
                )
                return True, "replace_missing_references", details
            return False, "missing_reference_unrepaired", details

        if failure_class == "stale_tmp_cache":
            cleaned = _clean_stale_tmp_cache_paths(
                self.run.get("plan") or {},
                self.cfg.selected_dir,
                self.cfg.workspace_root,
            )
            if cleaned.get("changed", False):
                details.update(cleaned)
                return True, "clear_stale_tmp_cache", details
            return False, "no_stale_tmp_cache_found", details

        if failure_class in {"validation_block", "policy_block", "format_input_error"} and self.cfg.allow_canonicalize:
            canonical_plan, meta = canonicalize_execution_plan(
                self.run.get("plan") or {},
                data_root=str(self.cfg.data_root),
            )
            if meta.get("changed", False):
                resume_meta = _apply_repaired_plan_with_resume(self.run, canonical_plan)
                details.update(
                    {
                        "changed": True,
                        "diff_summary": merge_resume_metadata(
                            canonicalization=meta,
                            resume=resume_meta,
                        ),
                        "canonicalization": meta,
                        "resume": resume_meta,
                    }
                )
                return True, "canonicalize_plan", details

        if failure_class in {
            "contract_mismatch",
            "runtime_step_failure",
            "unknown_failure",
            "validation_block",
            "policy_block",
            "format_input_error",
        }:
            reason = self.run.get("error", "") or failure_class
            ok, action, repl_details = self._maybe_replan_for_failure(failure_class, str(reason))
            details.update(repl_details)
            return ok, action, details

        return False, "no_action", details

    def _maybe_auto_recover_data_root(self) -> bool:
        if not self.run.get("no_fastq_found", False):
            return False
        auto_root = self.cfg.workspace_root / "inputs_readonly"
        candidates = _discover_fastq_files(str(auto_root), True, "", 2000)
        parent_counts: dict[str, int] = {}
        for fp in candidates:
            parent = str(Path(fp).parent)
            parent_counts[parent] = parent_counts.get(parent, 0) + 1
        if not parent_counts:
            return False
        best_parent = max(parent_counts.items(), key=lambda kv: kv[1])[0]
        self.cfg.data_root = Path(best_parent)
        rebuilt_plan = False
        fallback_plan, fallback_action, fallback_details = self._build_contract_template_repair("format_input_error")
        if fallback_plan is not None:
            self.run["plan"] = fallback_plan
            contract = self.run.get("plan_contract", {}) if isinstance(self.run.get("plan_contract", {}), dict) else {}
            self.run["contract_validation"] = self._assess_contract_for_plan(fallback_plan, contract)
            self.run["step_statuses"] = ["pending"] * len(
                fallback_plan.get("plan", []) if isinstance(fallback_plan, dict) else []
            )
            self.run["next_step_idx"] = 0
            rebuilt_plan = True
        _emit(
            f"[recovery] Data root switched to {best_parent} ({parent_counts[best_parent]} FASTQ files).",
            quiet=self.cfg.quiet,
        )
        self._append_event(
            step_id=None,
            agent="RecoveryAgent",
            event_type="RECOVERY_RESULT",
            severity="info",
            payload={"status": "success", "resolved_root": best_parent, "plan_rebuilt": rebuilt_plan},
        )
        if rebuilt_plan:
            self._append_event(
                step_id=None,
                agent="RecoveryAgent",
                event_type="REPAIR_APPLIED",
                severity="warning",
                payload={
                    "run_id": self.run.get("run_uid", ""),
                    "failure_class": "format_input_error",
                    "attempt": int(self.run.get("auto_repair_attempts", {}).get("format_input_error", 0)),
                    "action": f"data_root_rebuild_{fallback_action}",
                    "details": {
                        "why": "replanned_after_data_root_switch",
                        "resolved_root": best_parent,
                        **fallback_details,
                    },
                },
            )
        return True

    def run_end_to_end(self) -> dict[str, Any]:
        if hasattr(self, "_stepwise_execution_mode") and bool(self._stepwise_execution_mode()):
            return self._run_end_to_end_stepwise()

        self._init_run()
        self._persist_state()

        _emit(f"Run ID: {self.run['run_uid']}", quiet=self.cfg.quiet)
        _emit("Planning execution...", quiet=self.cfg.quiet)
        self._prepare_plan()
        if self.cfg.print_plan:
            print(json.dumps(self.run.get("plan", {}), indent=2), flush=True)
        self._persist_state()

        preflight_ok, preflight_message = self._preflight()
        if not preflight_ok:
            record_preflight_failure(
                self.run,
                message=preflight_message,
                append_event=self._append_event,
            )
            self._record_graph_outcome()
            self._persist_state()
            self._write_exit()
            return self._result_payload()

        repair_cycles = 0
        while True:
            _emit(
                f"Starting execution cycle {repair_cycles + 1} (max repairs={self.cfg.max_repairs})",
                quiet=self.cfg.quiet,
            )
            self._execute_once()
            if self.run.get("status") == "completed":
                break

            # Fast path: recover data root immediately if execution reported empty input scope.
            if self._maybe_auto_recover_data_root():
                self.run["status"] = "planned"
                self.run["error"] = ""
                repair_cycles += 1
                if repair_cycles > self.cfg.max_repairs:
                    break
                continue

            failure_class = classify_failure(self.run)
            self.run["auto_repair_last_class"] = failure_class
            attempts = dict(self.run.get("auto_repair_attempts", {}))
            if not can_attempt_repair(attempts, failure_class):
                _emit(f"Auto-repair limit reached for class={failure_class}", quiet=self.cfg.quiet)
                break

            repaired, action, details = self._apply_repair_action(failure_class)
            if not repaired:
                _emit(f"Auto-repair not applied ({action})", quiet=self.cfg.quiet)
                break

            apply_successful_repair_cycle(
                self.run,
                failure_class=failure_class,
                action=action,
                details=details,
                selected_dir=self.cfg.selected_dir,
                append_event=self._append_event,
                emit=_emit,
                quiet=self.cfg.quiet,
            )
            self._persist_state()
            repair_cycles += 1
            if repair_cycles > self.cfg.max_repairs:
                _emit("Maximum repair cycles exhausted.", quiet=self.cfg.quiet)
                self.run["status"] = "failed"
                if not str(self.run.get("error", "")).strip():
                    self.run["error"] = "Maximum repair cycles exhausted before reaching a completed run."
                break

        if self.run.get("status") == "planned":
            self.run["status"] = "failed"
            if not str(self.run.get("error", "")).strip():
                self.run["error"] = "Execution ended without completion."

        self.run["finished_at"] = _now_utc_iso()
        self._record_graph_outcome()
        self._persist_state()
        self._write_exit()
        return self._result_payload()

    def _result_payload(self) -> dict[str, Any]:
        assistance_manifest = self._assistance_manifest_payload()
        return build_runtime_result_payload(
            run=self.run,
            data_root=self.cfg.data_root,
            selected_dir=self.cfg.selected_dir,
            path_graph_db_path=self.path_graph.db_path,
            path_graph_user_key=str(self.cfg.path_graph_user_key),
            path_graph_scope=str(self.cfg.path_graph_scope),
            benchmark_policy=self._benchmark_policy(),
            assistance_manifest=assistance_manifest,
        )
