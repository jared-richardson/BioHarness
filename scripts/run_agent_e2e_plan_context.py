"""Plan-context helpers for contract-aware execution normalization."""

from __future__ import annotations

from pathlib import Path

from bio_harness.core.artifact_role_validator import (
    repair_artifact_role_violations,
    summarize_artifact_role_violations,
    validate_artifact_role_invariants,
)
from bio_harness.core.direct_wrapper_completeness import (
    assess_direct_wrapper_plan_completeness,
)
from bio_harness.core.literature_planning_support import (
    generate_literature_planning_support,
)
from bio_harness.harness.execution_contract_scope import (
    is_compatible_tool_hint,
    scope_contract_to_execution_mode,
)
from bio_harness.harness.path_graph_run_support import (
    build_active_preference_profile,
    infer_selected_path_id,
    record_graph_outcome,
    record_graph_selection,
)
from scripts.run_agent_e2e_plan_normalization_support import (
    PlanNormalizationContext,
    normalize_plan_for_execution,
)
from scripts.run_agent_e2e_support import (
    Any,
    PROJECT_ROOT,
    _infer_observed_groups_from_plan_artifacts,
    _is_empty_contract,
    _mark_group_observed,
    _normalize_group_label,
    analysis_spec_preference_profile,
    assess_plan_contract,
    deterministic_prompt_hash,
)


class AgentE2EPlanContextMixin:
    def _artifact_role_issue_strings(self, plan: dict[str, Any]) -> list[str]:
        """Return stable artifact-role issue strings for one candidate plan."""

        violations = validate_artifact_role_invariants(
            plan,
            selected_dir=self.cfg.selected_dir,
            allowed_input_roots=[self.cfg.data_root],
        )
        return summarize_artifact_role_violations(violations)

    def _stabilize_artifact_roles(
        self,
        plan: dict[str, Any],
        *,
        source_plan: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Restore corrupted input/reference bindings from the source plan."""

        repaired, meta = repair_artifact_role_violations(
            plan,
            source_plan=source_plan,
            selected_dir=self.cfg.selected_dir,
            allowed_input_roots=[self.cfg.data_root],
        )
        issues = self._artifact_role_issue_strings(repaired)
        if issues:
            meta = dict(meta or {})
            meta["issues"] = issues
        return repaired, meta

    def _preserved_tool_names_for_execution_normalization(self) -> set[str]:
        """Return tool names whose explicit arguments must survive early normalization."""

        analysis_spec = self._run_analysis_spec_dict()
        intent = (
            analysis_spec.get("explicit_execution_intent", {})
            if isinstance(analysis_spec.get("explicit_execution_intent", {}), dict)
            else {}
        )
        raw_tools = intent.get("preserve_existing_values_for_tools", [])
        if not isinstance(raw_tools, list):
            raw_tools = []
        preserved = {
            str(tool).strip().lower()
            for tool in raw_tools
            if str(tool).strip()
        }
        if preserved:
            return preserved
        locked = intent.get("locked_tools", [])
        if not isinstance(locked, list):
            return set()
        return {
            str(tool).strip().lower()
            for tool in locked
            if str(tool).strip()
        }

    def _active_preference_profile(self) -> dict[str, Any]:
        stored = self.path_graph.get_user_preferences(
            user_key=str(self.cfg.path_graph_user_key),
            scope=str(self.cfg.path_graph_scope),
        )
        analysis_pref = analysis_spec_preference_profile(self.run.get("analysis_spec", {}))
        return build_active_preference_profile(
            stored_preferences=stored,
            analysis_preferences=analysis_pref,
        )

    def _prepare_analysis_spec(self, contract: dict[str, Any]) -> None:
        planner_trace_dir = str(self.run.get("run_files", {}).get("planner", "") or "")
        planner_trace_context = {
            "run_id": str(self.run.get("run_uid", "")),
            "stage": "analysis_review",
        }
        self.orchestrator.configure_planner_trace(planner_trace_dir, planner_trace_context)
        spec = self.orchestrator.build_analysis_spec(
            self.cfg.prompt,
            contract=contract,
            selected_dir=str(self.cfg.selected_dir),
            data_root=str(self.cfg.data_root),
            project_root=str(PROJECT_ROOT),
            benchmark_policy=self._benchmark_policy(),
            analysis_type_override=str(getattr(self.cfg, "analysis_type", "") or "").strip(),
        )
        self.run["analysis_spec"] = spec if isinstance(spec, dict) else {}
        if isinstance(self.run["analysis_spec"], dict):
            self.run["analysis_spec"]["benchmark_policy"] = self._benchmark_policy()
            # Fix #23: ensure every downstream binder call sees a real
            # ``requested_data_root`` regardless of analysis type. The
            # plan-normalization code path in
            # ``run_agent_e2e_plan_normalization_support.py`` already
            # injects this value before rebinding, but the stepwise loop
            # (and any other caller that goes through
            # ``bind_step_spec_for_strict_mode`` directly) would otherwise
            # see an empty string — which, combined with the
            # ``Path('')`` → ``Path('.')`` degeneration, caused the binder
            # to rewrite correct absolute read paths to cwd-rooted
            # (repo-root) fakes in exp38/exp39.
            try:
                cfg_data_root = str(getattr(self.cfg, "data_root", "") or "").strip()
                if cfg_data_root and not str(
                    self.run["analysis_spec"].get("requested_data_root", "") or ""
                ).strip():
                    self.run["analysis_spec"]["requested_data_root"] = str(
                        Path(cfg_data_root).expanduser().resolve(strict=False)
                    )
            except Exception:  # pragma: no cover — defensive
                pass
            literature_support = generate_literature_planning_support(
                user_query=self.cfg.prompt,
                analysis_spec=self.run["analysis_spec"],
                benchmark_policy=self._benchmark_policy(),
                run_dir=Path(str(self.run.get("run_files", {}).get("run_dir", self.cfg.selected_dir))),
                librarian=self.orchestrator._get_librarian(),
                artifact_paths={
                    "json": Path(
                        str(
                            self.run.get("run_files", {}).get(
                                "literature_planning_support_json",
                                Path(str(self.run.get("run_files", {}).get("run_dir", self.cfg.selected_dir)))
                                / "literature_planning_support.json",
                            )
                        )
                    ),
                    "md": Path(
                        str(
                            self.run.get("run_files", {}).get(
                                "literature_planning_support_md",
                                Path(str(self.run.get("run_files", {}).get("run_dir", self.cfg.selected_dir)))
                                / "literature_planning_support.md",
                            )
                        )
                    ),
                },
            )
            self.run["analysis_spec"]["literature_planning_support"] = literature_support
            self.run["literature_planning_support"] = literature_support
            self._append_event(
                step_id=None,
                agent="AnalysisReview",
                event_type="LITERATURE_PLANNING_SUPPORT_EVALUATED",
                severity="info" if str(literature_support.get("status", "")) != "failed" else "warning",
                payload=literature_support,
            )
        self._append_event(
            step_id=None,
            agent="AnalysisReview",
            event_type="ANALYSIS_SPEC_PREPARED",
            severity="info",
            payload={
                "analysis_type": str((self.run.get("analysis_spec", {}) or {}).get("analysis_type", "")),
                "chosen_method": str((self.run.get("analysis_spec", {}) or {}).get("chosen_method", "")),
                "benchmark_policy": self._benchmark_policy(),
                "preferred_tools": list((self.run.get("analysis_spec", {}) or {}).get("preferred_tools", []) or []),
                "protocol_grounding": (self.run.get("analysis_spec", {}) or {}).get("protocol_grounding", {}),
                "literature_planning_support": (self.run.get("analysis_spec", {}) or {}).get("literature_planning_support", {}),
            },
        )

    def _infer_selected_path_id(self, plan: dict[str, Any]) -> str:
        return infer_selected_path_id(
            plan=plan,
            fallback_selection=self.run.get("fallback_selection", {}),
            prompt_hash_fallback=deterministic_prompt_hash(self.cfg.prompt)[:12],
        )

    def _record_graph_selection(self) -> None:
        plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {}
        path_id = self._infer_selected_path_id(plan)
        record_graph_selection(
            path_graph=self.path_graph,
            run=self.run,
            path_id=path_id,
        )

    def _record_graph_outcome(self) -> None:
        record_graph_outcome(
            path_graph=self.path_graph,
            run=self.run,
            persist_preference_updates=bool(self.cfg.path_graph_persist_preference_updates),
            path_graph_user_key=str(self.cfg.path_graph_user_key),
            path_graph_scope=str(self.cfg.path_graph_scope),
        )

    def _augment_group_observation_from_plan_artifacts(self) -> None:
        tracked = {
            _normalize_group_label(x)
            for x in (
                list(self.run.get("missing_sample_group_signals", []))
                + list(self.run.get("missing_sample_groups", []))
            )
            if str(x).strip()
        }
        tracked_groups = sorted([g for g in tracked if g])
        if not tracked_groups:
            return
        inferred = _infer_observed_groups_from_plan_artifacts(
            self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
            self.cfg.selected_dir,
            tracked_groups,
        )
        for group in sorted(inferred):
            _mark_group_observed(self.run, group, source="plan_artifact_inference")

    def _normalize_plan_for_execution(self, plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        context = PlanNormalizationContext(
            selected_dir=self.cfg.selected_dir,
            data_root=self.cfg.data_root,
            benchmark_policy=str(self.run.get("benchmark_policy", "") or ""),
            user_request=str(self.run.get("user_request", "") or ""),
            analysis_spec=self.run.get("analysis_spec", {}) if isinstance(self.run.get("analysis_spec", {}), dict) else {},
            runtime_binding_analysis_spec=self._runtime_binding_analysis_spec(),
            plan_contract=self.run.get("plan_contract", {}) if isinstance(self.run.get("plan_contract", {}), dict) else {},
            preserved_tool_names=self._preserved_tool_names_for_execution_normalization(),
            freeze_completed_prefix=bool(
                getattr(self, "_stepwise_freeze_completed_prefix", False)
            ),
        )
        return normalize_plan_for_execution(
            plan,
            context=context,
            stabilize_artifact_roles=lambda candidate, source_plan: self._stabilize_artifact_roles(
                candidate,
                source_plan=source_plan,
            ),
            artifact_role_issue_strings=self._artifact_role_issue_strings,
        )

    def _assess_contract_for_plan(self, plan: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
        direct_wrapper_validation = assess_direct_wrapper_plan_completeness(
            plan,
            analysis_spec=self._run_analysis_spec_dict(),
        )
        artifact_role_issues = self._artifact_role_issue_strings(plan)

        def _attach_direct_wrapper_validation(payload: dict[str, Any]) -> dict[str, Any]:
            enriched = dict(payload)
            enriched["direct_wrapper_issues"] = list(direct_wrapper_validation.get("issues", []))
            enriched["direct_wrapper_compatible_tools"] = list(
                direct_wrapper_validation.get("compatible_tools", [])
            )
            enriched["artifact_role_issues"] = list(artifact_role_issues)
            if not direct_wrapper_validation.get("passed", True):
                enriched["passed"] = False
            if artifact_role_issues:
                enriched["passed"] = False
            return enriched

        if _is_empty_contract(contract):
            return _attach_direct_wrapper_validation(
                {"passed": True, "missing_capabilities": [], "missing_tool_hints": []}
            )
        scoped_contract, compatible_tools = scope_contract_to_execution_mode(
            contract,
            self._run_analysis_spec_dict(),
            preference_profile=self._active_preference_profile(),
        )
        if _is_empty_contract(scoped_contract):
            steps = plan.get("plan", []) if isinstance(plan, dict) else []
            return _attach_direct_wrapper_validation(
                {
                    "passed": bool(steps),
                    "missing_capabilities": [],
                    "missing_required_tool_hints": [],
                    "missing_tool_hints": [],
                }
            )
        result = assess_plan_contract(
            plan,
            scoped_contract,
            capability_specs=self.capability_specs,
        )
        if compatible_tools:
            result["missing_tool_hints"] = [
                hint
                for hint in result.get("missing_tool_hints", [])
                if is_compatible_tool_hint(hint, compatible_tools)
            ]
            result["missing_required_tool_hints"] = [
                hint
                for hint in result.get("missing_required_tool_hints", [])
                if is_compatible_tool_hint(hint, compatible_tools)
            ]
            steps = plan.get("plan", []) if isinstance(plan, dict) else []
            result["passed"] = bool(steps) and not result.get("missing_capabilities") and not result.get("missing_required_tool_hints")
        return _attach_direct_wrapper_validation(result)
