from __future__ import annotations

import logging

from bio_harness.core.stage_dag import repair_stage_dag
from bio_harness.core.tool_registry import default_tool_registry
from bio_harness.harness.plan_semantic_guards import (
    repair_ambiguous_bcftools_expression_bash_run_commands,
    repair_invalid_bcftools_isec_bash_run_commands,
    repair_invalid_bcftools_view_bash_run_commands,
)
from scripts.run_agent_e2e_preexecution_repair_support import (
    adopt_preexecution_candidate_if_valid,
    assess_plan_semantic_guards_with_bash_placeholders,
    protocol_repair_strategy,
)
from scripts.run_agent_e2e_support import (
    Any,
    _assess_plan_semantic_guards,
    _emit,
    _is_actionable_executable_plan,
    _is_empty_contract,
    _repair_shared_variant_csv_exports_with_analysis_spec,
    assess_protocol_grounding,
    deterministic_protocol_repair,
    mp,  # noqa: F401
)
from bio_harness.core.template_assistance_policy import protocol_template_assistance_enabled

logger = logging.getLogger(__name__)


def _apply_deterministic_preexecution_semantic_repairs(
    *,
    plan: dict[str, Any],
    analysis_spec: dict[str, Any],
    cwd: str,
    path_graph: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Apply chained deterministic semantic repairs until they reach a fixpoint."""

    current_plan = plan if isinstance(plan, dict) else {}
    applied_repairs: list[dict[str, Any]] = []
    current_plan, current_validation, _placeholder_sidecar = (
        assess_plan_semantic_guards_with_bash_placeholders(
            plan=current_plan,
            assess_semantic_guards=lambda candidate: _assess_plan_semantic_guards(
                candidate,
                analysis_spec=analysis_spec,
                cwd=cwd,
            ),
            path_graph=path_graph,
            selected_dir=cwd,
        )
    )
    repair_specs = (
        (
            "preexecution_semantic_stage_dag_repair",
            lambda issues: any(
                isinstance(issue, dict)
                and issue.get("issue")
                in {
                    "consumer_before_producer",
                    "missing_stage_producer",
                    "duplicate_equivalent_step",
                    "invalid_stage_transition",
                    "cycle_detected",
                }
                for issue in issues
            ),
            lambda candidate: (
                lambda result: (
                    result.plan,
                    {
                        "changed": result.repair_applied,
                        "stage_repairs": result.as_sidecar(),
                    },
                )
            )(repair_stage_dag(candidate, registry=default_tool_registry()))
        ),
        (
            "preexecution_semantic_bcftools_view_cli_repair",
            lambda issues: any(
                isinstance(issue, dict) and issue.get("issue") == "invalid_bcftools_view_cli"
                for issue in issues
            ),
            lambda candidate: repair_invalid_bcftools_view_bash_run_commands(candidate),
        ),
        (
            "preexecution_semantic_bcftools_isec_output_repair",
            lambda issues: any(
                isinstance(issue, dict) and issue.get("issue") == "invalid_bcftools_isec_output_mode"
                for issue in issues
            ),
            lambda candidate: repair_invalid_bcftools_isec_bash_run_commands(candidate),
        ),
        (
            "preexecution_semantic_bcftools_expression_namespace_repair",
            lambda issues: any(
                isinstance(issue, dict)
                and issue.get("issue")
                in {
                    "ambiguous_bcftools_expression_namespace",
                    "missing_bcftools_expression_namespace_field",
                }
                for issue in issues
            ),
            lambda candidate: repair_ambiguous_bcftools_expression_bash_run_commands(
                candidate,
                cwd=cwd,
            ),
        ),
        (
            "preexecution_semantic_shared_variant_export_repair",
            lambda issues: any(
                isinstance(issue, dict) and issue.get("issue") == "annotation_filter_before_annotation"
                for issue in issues
            ),
            lambda candidate: _repair_shared_variant_csv_exports_with_analysis_spec(
                candidate,
                analysis_spec=analysis_spec,
            ),
        ),
    )

    while True:
        issues = current_validation.get("issues", []) if isinstance(current_validation.get("issues", []), list) else []
        if current_validation.get("passed", False) or not issues:
            break

        repaired_any = False
        for action, predicate, repair_fn in repair_specs:
            if not predicate(issues):
                continue
            candidate, repair_meta = repair_fn(current_plan)
            if not (repair_meta.get("changed", False) and isinstance(candidate, dict)):
                continue
            current_plan = candidate
            applied_repairs.append(
                {
                    "action": action,
                    "repair_meta": repair_meta,
                }
            )
            current_plan, current_validation, _placeholder_sidecar = (
                assess_plan_semantic_guards_with_bash_placeholders(
                    plan=current_plan,
                    assess_semantic_guards=lambda candidate: _assess_plan_semantic_guards(
                        candidate,
                        analysis_spec=analysis_spec,
                        cwd=cwd,
                    ),
                    path_graph=path_graph,
                    selected_dir=cwd,
                )
            )
            repaired_any = True
            break

        if not repaired_any:
            break

    return current_plan, applied_repairs, current_validation


class AgentE2EPreexecutionRepairMixin:
    def _supervised_model_replan(
        self, *, prompt: str, strategy: str, use_fast_model: bool = True,
    ) -> dict[str, Any]:
        """Run a supervised repair replan with hierarchical-first fallback.

        Repair prompts often elicit workflow-skeleton answers from stronger
        models. Letting the normal planner heuristics see those prompts keeps
        branch-local workflow structure available for hierarchical expansion,
        while a direct-mode fallback preserves the older repair path.
        """

        model_override = self.cfg.model_name if use_fast_model else None
        seed_plan = self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else None
        last_candidate: dict[str, Any] = {}
        last_error: Exception | None = None
        for planner_mode in ("auto", "direct"):
            try:
                candidate, _elapsed = self._planner_attempt_with_heartbeat(
                    prompt=prompt,
                    strategy=strategy,
                    attempt_num=0,
                    planner_mode=planner_mode,
                    seed_plan=seed_plan,
                    model_override=model_override,
                )
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    raise
                last_error = exc
                continue
            if isinstance(candidate, dict):
                last_candidate = candidate
                if _is_actionable_executable_plan(candidate):
                    return candidate
        if last_candidate:
            return last_candidate
        if last_error is not None:
            raise last_error
        return {}

    def _attempt_preexecution_contract_repair(
        self,
        *,
        contract: dict[str, Any],
        validation: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if self._strict_llm_planning_enabled():
            return False, "strict_llm_planning", {"why": "strict_llm_planning_enabled"}
        if _is_empty_contract(contract) or validation.get("passed", False):
            return False, "contract_already_satisfied", {"why": "no_missing_contract_requirements"}

        # Deterministic template-first repair before execution.
        fallback_plan, fallback_action, fallback_details = self._build_contract_template_repair("contract_mismatch")
        if fallback_plan is not None:
            accepted = adopt_preexecution_candidate_if_valid(
                run=self.run,
                candidate=fallback_plan,
                normalize_plan_for_execution=self._normalize_plan_for_execution,
                validate_plan=lambda plan: self._assess_contract_for_plan(plan, contract),
                mark_planned=False,
                clear_error=False,
                include_diff_summary=False,
            )
            if accepted is not None:
                self.run["contract_validation"] = accepted["validation_after"]
                return True, f"preexecution_{fallback_action}", {
                    **fallback_details,
                    "why": "contract_template_before_execution",
                    "canonicalization": accepted["canonicalization"],
                    "featurecounts_normalization": accepted["featurecounts_normalization"],
                    "contract_validation_after": accepted["validation_after"],
                }

        # General-purpose repair: force a focused contract-aware replan before any execution.
        try:
            candidate = self._supervised_model_replan(
                prompt=self._contract_replan_prompt(
                    contract=contract,
                    validation=validation,
                    plan=self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
                ),
                strategy="preexecution_contract_repair",
            )
        except Exception as exc:
            candidate = None
            replan_error = str(exc)
        else:
            replan_error = ""

        if isinstance(candidate, dict) and _is_actionable_executable_plan(candidate):
            accepted = adopt_preexecution_candidate_if_valid(
                run=self.run,
                candidate=candidate,
                normalize_plan_for_execution=self._normalize_plan_for_execution,
                validate_plan=lambda plan: self._assess_contract_for_plan(plan, contract),
                mark_planned=False,
                clear_error=False,
                include_diff_summary=True,
            )
            if accepted is not None:
                self.run["contract_validation"] = accepted["validation_after"]
                return True, "preexecution_contract_replan", {
                    "why": "contract_replan_before_execution",
                    "missing_capabilities_before": validation.get("missing_capabilities", []),
                    "missing_tool_hints_before": validation.get("missing_tool_hints", []),
                    "canonicalization": accepted["canonicalization"],
                    "featurecounts_normalization": accepted["featurecounts_normalization"],
                    "contract_validation_after": accepted["validation_after"],
                    "diff_summary": accepted["diff_summary"],
                }

        return False, "contract_repair_unavailable", {
            "why": "preexecution_contract_repair_failed",
            "replan_error": replan_error,
            "missing_capabilities": validation.get("missing_capabilities", []),
            "missing_tool_hints": validation.get("missing_tool_hints", []),
            "template_fallback_action": fallback_action,
            "template_fallback": fallback_details,
        }

    def _attempt_preexecution_protocol_repair(
        self,
        *,
        analysis_spec: dict[str, Any],
        validation: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        """Three-tier protocol repair:

        Tier 1 – Guided patch: patch the LLM plan with template knowledge
                 (parameters, missing steps, export fixes) while preserving
                 LLM plan structure.
        Tier 2 – LLM replan: ask the model to generate a new plan with
                 protocol grounding hints.
        Tier 3 – Full template fallback: use the deterministic compiled plan
                 as an absolute last resort.
        """
        if validation.get("passed", False):
            return False, "protocol_already_satisfied", {"why": "no_missing_protocol_requirements"}

        deterministic_template_assistance_enabled = protocol_template_assistance_enabled(
            getattr(self.cfg, "benchmark_policy", None)
        )
        self.run["protocol_repair_attempted"] = deterministic_template_assistance_enabled
        # -- Tier 1: Guided patch (LLM plan + template) -----------------------
        deterministic_meta: dict[str, Any] = {
            "changed": False,
            "why": "disabled_by_scientific_template_ablation",
        }
        full_template = None
        if deterministic_template_assistance_enabled:
            deterministic_candidate, deterministic_meta = deterministic_protocol_repair(
                self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
                analysis_spec=analysis_spec,
                selected_dir=self.cfg.selected_dir,
                data_root=self.cfg.data_root,
            )
            full_template = deterministic_meta.get("_full_template")

            if deterministic_meta.get("changed", False) and isinstance(deterministic_candidate, dict):
                accepted = adopt_preexecution_candidate_if_valid(
                    run=self.run,
                    candidate=deterministic_candidate,
                    normalize_plan_for_execution=self._normalize_plan_for_execution,
                    validate_plan=lambda plan: assess_protocol_grounding(plan, analysis_spec),
                    mark_planned=True,
                    clear_error=True,
                    include_diff_summary=True,
                )
                if accepted is not None:
                    strategy = protocol_repair_strategy(deterministic_meta)
                    action = f"preexecution_protocol_{strategy}"
                    self.run["protocol_repair_applied"] = True
                    self.run["protocol_repair_action"] = action
                    return True, action, {
                        "why": f"protocol_{strategy}_before_execution",
                        "missing_required_tools_before": validation.get("missing_required_tools", []),
                        "missing_plan_signals_before": validation.get("missing_plan_signals", []),
                        "canonicalization": accepted["canonicalization"],
                        "featurecounts_normalization": accepted["featurecounts_normalization"],
                        "protocol_validation_after": accepted["validation_after"],
                        "deterministic_protocol_repair": deterministic_meta,
                        "diff_summary": accepted["diff_summary"],
                    }

        # -- Tier 2: LLM replan ------------------------------------------------
        try:
            candidate = self._supervised_model_replan(
                prompt=self._protocol_replan_prompt(
                    analysis_spec=analysis_spec,
                    validation=validation,
                    plan=self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
                ),
                strategy="preexecution_protocol_repair",
            )
        except Exception as exc:
            candidate = None
            _emit(f"LLM replan failed: {exc}", quiet=self.cfg.quiet)

        if isinstance(candidate, dict) and _is_actionable_executable_plan(candidate):
            accepted = adopt_preexecution_candidate_if_valid(
                run=self.run,
                candidate=candidate,
                normalize_plan_for_execution=self._normalize_plan_for_execution,
                validate_plan=lambda plan: assess_protocol_grounding(plan, analysis_spec),
                mark_planned=True,
                clear_error=True,
                include_diff_summary=True,
            )
            if accepted is not None:
                self.run["protocol_repair_applied"] = True
                self.run["protocol_repair_action"] = "preexecution_protocol_replan"
                return True, "preexecution_protocol_replan", {
                    "why": "protocol_replan_before_execution",
                    "missing_required_tools_before": validation.get("missing_required_tools", []),
                    "missing_plan_signals_before": validation.get("missing_plan_signals", []),
                    "canonicalization": accepted["canonicalization"],
                    "featurecounts_normalization": accepted["featurecounts_normalization"],
                    "protocol_validation_after": accepted["validation_after"],
                    "diff_summary": accepted["diff_summary"],
                }

        # -- Tier 3: Full template fallback (absolute last resort) -------------
        if deterministic_template_assistance_enabled and isinstance(full_template, dict) and full_template.get("plan"):
            accepted = adopt_preexecution_candidate_if_valid(
                run=self.run,
                candidate=full_template,
                normalize_plan_for_execution=self._normalize_plan_for_execution,
                validate_plan=lambda plan: assess_protocol_grounding(plan, analysis_spec),
                mark_planned=True,
                clear_error=True,
                include_diff_summary=True,
            )
            if accepted is not None:
                self.run["protocol_repair_applied"] = True
                self.run["protocol_repair_action"] = "preexecution_protocol_template_fallback"
                return True, "preexecution_protocol_template_fallback", {
                    "why": "template_fallback_after_patch_and_replan_failed",
                    "missing_required_tools_before": validation.get("missing_required_tools", []),
                    "missing_plan_signals_before": validation.get("missing_plan_signals", []),
                    "canonicalization": accepted["canonicalization"],
                    "featurecounts_normalization": accepted["featurecounts_normalization"],
                    "protocol_validation_after": accepted["validation_after"],
                    "diff_summary": accepted["diff_summary"],
                }

        return False, "protocol_repair_unavailable", {
            "why": "preexecution_protocol_repair_failed",
            "missing_required_tools": validation.get("missing_required_tools", []),
            "missing_plan_signals": validation.get("missing_plan_signals", []),
            "deterministic_protocol_repair": deterministic_meta,
        }

    def _attempt_preexecution_semantic_repair(
        self,
        *,
        analysis_spec: dict[str, Any],
        validation: dict[str, Any],
    ) -> tuple[bool, str, dict[str, Any]]:
        if validation.get("passed", False):
            return False, "semantic_already_satisfied", {"why": "no_semantic_issues"}

        issues = validation.get("issues", []) if isinstance(validation.get("issues", []), list) else []
        deterministic_candidate, deterministic_repairs, deterministic_validation = (
            _apply_deterministic_preexecution_semantic_repairs(
                plan=self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
                analysis_spec=self._run_analysis_spec_dict(),
                cwd=str(self.cfg.selected_dir),
                path_graph=self.path_graph,
            )
        )
        if deterministic_repairs and isinstance(deterministic_candidate, dict):
            accepted = adopt_preexecution_candidate_if_valid(
                run=self.run,
                candidate=deterministic_candidate,
                normalize_plan_for_execution=self._normalize_plan_for_execution,
                validate_plan=lambda plan: assess_plan_semantic_guards_with_bash_placeholders(
                    plan=plan,
                    assess_semantic_guards=lambda candidate: _assess_plan_semantic_guards(
                        candidate,
                        analysis_spec=self._run_analysis_spec_dict(),
                        cwd=self.cfg.selected_dir,
                    ),
                    path_graph=self.path_graph,
                    selected_dir=str(self.cfg.selected_dir),
                )[1],
                mark_planned=True,
                clear_error=True,
                include_diff_summary=True,
            )
            if accepted is not None:
                if len(deterministic_repairs) == 1:
                    action = str(deterministic_repairs[0]["action"]).strip()
                    why = action.removeprefix("preexecution_").replace("_repair", "_before_execution")
                else:
                    action = "preexecution_semantic_deterministic_chain_repair"
                    why = "semantic_deterministic_chain_before_execution"
                stage_repair_sidecar = {}
                for repair in deterministic_repairs:
                    repair_meta = repair.get("repair_meta", {}) if isinstance(repair, dict) else {}
                    sidecar = repair_meta.get("stage_repairs", {}) if isinstance(repair_meta, dict) else {}
                    if isinstance(sidecar, dict) and sidecar:
                        stage_repair_sidecar = sidecar
                        break
                if stage_repair_sidecar:
                    self.run["preexecution_stage_repairs"] = stage_repair_sidecar
                    logger.info("Preexecution stage repairs: %s", stage_repair_sidecar)
                return True, action, {
                    "why": why,
                    "issues_before": issues,
                    "deterministic_semantic_repairs": deterministic_repairs,
                    "semantic_validation_candidate": deterministic_validation,
                    "canonicalization": accepted["canonicalization"],
                    "featurecounts_normalization": accepted["featurecounts_normalization"],
                    "semantic_validation_after": accepted["validation_after"],
                    "diff_summary": accepted["diff_summary"],
                }
        stage_repair_sidecar = {}
        for repair in deterministic_repairs:
            repair_meta = repair.get("repair_meta", {}) if isinstance(repair, dict) else {}
            sidecar = repair_meta.get("stage_repairs", {}) if isinstance(repair_meta, dict) else {}
            if isinstance(sidecar, dict) and sidecar:
                stage_repair_sidecar = sidecar
                break
        if stage_repair_sidecar:
            self.run["preexecution_stage_repairs"] = stage_repair_sidecar
            logger.info("Preexecution stage repairs: %s", stage_repair_sidecar)

        try:
            candidate = self._supervised_model_replan(
                prompt=self._semantic_replan_prompt(
                    analysis_spec=analysis_spec,
                    validation=validation,
                    plan=self.run.get("plan", {}) if isinstance(self.run.get("plan", {}), dict) else {},
                ),
                strategy="preexecution_semantic_repair",
            )
        except Exception as exc:
            return False, "semantic_repair_failed", {
                "why": "preexecution_semantic_repair_failed",
                "replan_error": str(exc),
                "issues": validation.get("issues", []),
            }

        if isinstance(candidate, dict) and _is_actionable_executable_plan(candidate):
            accepted = adopt_preexecution_candidate_if_valid(
                run=self.run,
                candidate=candidate,
                normalize_plan_for_execution=self._normalize_plan_for_execution,
                validate_plan=lambda plan: assess_plan_semantic_guards_with_bash_placeholders(
                    plan=plan,
                    assess_semantic_guards=lambda candidate_plan: _assess_plan_semantic_guards(
                        candidate_plan,
                        analysis_spec=self._run_analysis_spec_dict(),
                        cwd=self.cfg.selected_dir,
                    ),
                    path_graph=self.path_graph,
                    selected_dir=str(self.cfg.selected_dir),
                )[1],
                mark_planned=True,
                clear_error=True,
                include_diff_summary=True,
            )
            if accepted is not None:
                return True, "preexecution_semantic_replan", {
                    "why": "semantic_replan_before_execution",
                    "issues_before": validation.get("issues", []),
                    "canonicalization": accepted["canonicalization"],
                    "featurecounts_normalization": accepted["featurecounts_normalization"],
                    "semantic_validation_after": accepted["validation_after"],
                    "diff_summary": accepted["diff_summary"],
                }

        return False, "semantic_repair_unavailable", {
            "why": "preexecution_semantic_repair_failed",
            "issues": validation.get("issues", []),
        }
