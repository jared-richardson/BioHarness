"""Stepwise next-step planning loop for the CLI end-to-end harness.

This mixin adds a turn-by-turn execution mode where the planner emits exactly
one next step at a time, the harness validates that step against the current
run prefix, then executes only the newly appended step. The completed prefix is
treated as immutable history: stepwise validation may normalize and inspect the
candidate cumulative plan, but it may not silently rewrite already executed
steps.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from pathlib import Path
from typing import Any

from bio_harness.core.analysis_spec import build_analysis_brief
from bio_harness.core.branch_stage_progress import (
    render_branch_stage_progress_hint,
    summarize_branch_stage_progress,
)
from scripts.run_agent_e2e_plan_bootstrap_support import (
    initialize_plan_preparation_state,
)
from scripts.run_agent_e2e_preexecution_repair_support import (
    assess_plan_semantic_guards_with_bash_placeholders,
)
from scripts.run_agent_e2e_support import (
    _assess_plan_semantic_guards,
    _emit,
    _infer_request_contract,
    _json_dumps_safe,
    _missing_exec_tools_for_plan,
    _missing_input_paths_for_plan,
    _missing_local_scripts_for_plan,
    _now_utc_iso,
    _plan_summary_for_repair_prompt,
    assess_protocol_grounding,
    classify_failure,
    ranked_fallback_catalog_metadata,
)

_STEPWISE_PROTOCOL_SOFT_ISSUES = frozenset(
    {
        "insufficient_comparison_branches",
        "missing_benchmark_annotation_stage",
    }
)
_STEPWISE_VARIANT_ANNOTATION_TOOLS = frozenset({"snpeff_annotate", "vep_annotate"})
_STEPWISE_REFERENCE_ANNOTATION_PRODUCERS = ("prodigal_annotate", "prokka_annotate")


def _value_matches_any_suffix(value: str, suffixes: tuple[str, ...]) -> bool:
    """Return whether a path-like value has one of the expected suffixes."""

    text = str(value or "").strip().lower()
    return bool(text) and any(text.endswith(str(suffix).lower()) for suffix in suffixes)


def _stepwise_analysis_spec_is_compiled_pipeline(analysis_spec: dict[str, Any]) -> bool:
    """Return whether the runtime spec should enforce its workflow seed.

    Historically this gate only covered explicit ``compiled_pipeline`` specs.
    Protocol-grounded benchmark specs can also carry a compact ``plan_skeleton``
    even when their execution contract remains ``direct_wrapper``. In stepwise
    mode that skeleton is still the ordered frontier: it prevents downstream
    headline tools from hiding required support steps such as alignment,
    filtering, or normalization.
    """

    for key in ("execution_contract", "protocol_grounding"):
        mapping = analysis_spec.get(key, {})
        if not isinstance(mapping, dict):
            continue
        mode = str(mapping.get("execution_mode", "") or "").strip()
        if mode == "compiled_pipeline":
            return True
    skeleton = analysis_spec.get("plan_skeleton", [])
    if not isinstance(skeleton, list) or not skeleton:
        return False
    protocol = analysis_spec.get("protocol_grounding", {})
    protocol_dict = protocol if isinstance(protocol, dict) else {}
    return bool(protocol_dict.get("grounded", False))


def _stepwise_next_plan_skeleton_tool(
    *,
    analysis_spec: dict[str, Any],
    completed_step_count: int,
) -> str:
    """Return the next expected tool from a compact workflow seed."""

    skeleton = analysis_spec.get("plan_skeleton", [])
    if not isinstance(skeleton, list):
        return ""
    index = max(0, int(completed_step_count))
    if index >= len(skeleton):
        return ""
    entry = skeleton[index]
    if isinstance(entry, (list, tuple)) and entry:
        return str(entry[0] or "").strip().lower()
    if isinstance(entry, dict):
        return str(entry.get("tool_name", "") or "").strip().lower()
    return ""


def _stepwise_next_plan_skeleton_entry(
    *,
    analysis_spec: dict[str, Any],
    completed_step_count: int,
) -> dict[str, Any]:
    """Return the next compact workflow-seed entry in dict form."""

    skeleton = analysis_spec.get("plan_skeleton", [])
    if not isinstance(skeleton, list):
        return {}
    index = max(0, int(completed_step_count))
    if index >= len(skeleton):
        return {}
    entry = skeleton[index]
    if isinstance(entry, dict):
        return dict(entry)
    if isinstance(entry, (list, tuple)) and entry:
        return {
            "tool_name": str(entry[0] or "").strip(),
            "objective": str(entry[1] or "").strip() if len(entry) > 1 else "",
            "parameter_hints": dict(entry[2])
            if len(entry) > 2 and isinstance(entry[2], dict)
            else {},
        }
    return {}


def _stepwise_plan_skeleton_entry_tool_name(entry: Any) -> str:
    """Return the normalized tool name for one compact workflow-seed entry."""

    if isinstance(entry, dict):
        return str(entry.get("tool_name", "") or "").strip().lower()
    if isinstance(entry, (list, tuple)) and entry:
        return str(entry[0] or "").strip().lower()
    return ""


def _stepwise_plan_skeleton_entry_dict(entry: Any) -> dict[str, Any]:
    """Return one compact workflow-seed entry in dict form."""

    if isinstance(entry, dict):
        return dict(entry)
    if isinstance(entry, (list, tuple)) and entry:
        return {
            "tool_name": str(entry[0] or "").strip(),
            "objective": str(entry[1] or "").strip() if len(entry) > 1 else "",
            "parameter_hints": dict(entry[2])
            if len(entry) > 2 and isinstance(entry[2], dict)
            else {},
        }
    return {}


def _stepwise_completed_tool_counts(
    *,
    steps: list[dict[str, Any]],
    statuses: list[Any],
) -> dict[str, int]:
    """Return completed tool counts from the accepted stepwise prefix."""

    completed_labels = {"succeeded", "success", "completed", "ok", "done"}
    counts: dict[str, int] = {}
    has_statuses = bool(statuses)
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if has_statuses:
            if index >= len(statuses):
                continue
            if _stepwise_status_label(statuses[index]) not in completed_labels:
                continue
        tool_name = str(step.get("tool_name", "") or "").strip().lower()
        if tool_name:
            counts[tool_name] = counts.get(tool_name, 0) + 1
    return counts


def _stepwise_status_label(status: Any) -> str:
    """Return the status label from a string or persisted status record."""

    if isinstance(status, dict):
        for key in ("status", "state", "step_status"):
            label = str(status.get(key, "") or "").strip().lower()
            if label:
                return label
        return ""
    return str(status).strip().lower()


def _stepwise_next_unsatisfied_plan_skeleton_entry(
    *,
    analysis_spec: dict[str, Any],
    steps: list[dict[str, Any]],
    statuses: list[Any],
) -> dict[str, Any]:
    """Return the first workflow-seed entry not yet satisfied by the prefix.

    Branch-local fan-out can execute the same skeleton row many times (for
    example one RNA-seq alignment row per sample). Raw prefix length therefore
    cannot be used as the skeleton index; the correct frontier is the first
    skeleton tool whose completed occurrence count has not consumed that row.
    """

    skeleton = analysis_spec.get("plan_skeleton", [])
    if not isinstance(skeleton, list):
        return {}
    remaining_counts = _stepwise_completed_tool_counts(
        steps=steps,
        statuses=statuses,
    )
    for entry in skeleton:
        tool_name = _stepwise_plan_skeleton_entry_tool_name(entry)
        if not tool_name:
            continue
        count = int(remaining_counts.get(tool_name, 0) or 0)
        if count > 0:
            remaining_counts[tool_name] = count - 1
            continue
        return _stepwise_plan_skeleton_entry_dict(entry)
    return {}


def _stepwise_graph_pipeline_tools(analysis_spec: dict[str, Any]) -> set[str]:
    """Return alternative graph-seeded tools for the same immediate workflow."""

    tools: set[str] = set()
    family = _stepwise_normalized_analysis_token(
        str(analysis_spec.get("analysis_type", "") or "")
    )
    protocol = (
        analysis_spec.get("protocol_grounding", {})
        if isinstance(analysis_spec.get("protocol_grounding", {}), dict)
        else {}
    )
    family = _stepwise_normalized_analysis_token(
        str(protocol.get("analysis_family", "") or "")
    ) or family
    for entry in analysis_spec.get("graph_pipeline_skeleton", []) or []:
        if not isinstance(entry, dict):
            continue
        capability = _stepwise_normalized_analysis_token(
            str(entry.get("capability", "") or "")
        )
        if capability and family and capability not in family and family not in capability:
            continue
        tool_name = str(entry.get("tool_name", "") or "").strip().lower()
        if tool_name:
            tools.add(tool_name)
    return tools


def _stepwise_normalized_analysis_token(value: str) -> str:
    """Return a loose token for comparing analysis families and capabilities."""

    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace("metagenomics", "metagenomic")
    )


def _stepwise_rejection_gate_name(rejection_reason: str) -> str:
    """Return a stable gate label for a stepwise rejection reason."""

    reason = str(rejection_reason or "").lower()
    if "outside the workflow seed" in reason:
        return "workflow_seed"
    if "duplicates completed" in reason or "duplicate_equivalent_step" in reason:
        return "duplicate_detector"
    if "branch-stage frontier" in reason:
        return "branch_stage_frontier"
    if "reference gene annotation gff" in reason:
        return "annotation_prerequisite"
    if "references inputs that are not available" in reason or "missing inputs" in reason:
        return "missing_inputs"
    if "missing required argument" in reason:
        return "required_arguments"
    if "mutated the already executed plan prefix" in reason:
        return "completed_prefix_mutation"
    if "protocol grounding" in reason:
        return "protocol_grounding"
    if "semantic validation" in reason:
        return "semantic_validation"
    if "contract issues" in reason:
        return "contract_validation"
    if "unavailable executable tools" in reason:
        return "missing_tools"
    if "missing local scripts" in reason:
        return "missing_local_scripts"
    return "candidate_evaluator"


class AgentE2EStepwiseExecutionMixin:
    """Add a one-step-at-a-time planning and execution mode."""

    def _record_stepwise_candidate_rejection(
        self,
        *,
        candidate_plan: dict[str, Any],
        rejection_reason: str,
        turn_num: int,
        attempt_num: int,
        strategy: str,
        source: str,
    ) -> dict[str, Any]:
        """Persist a structured rejected-candidate audit record.

        Args:
            candidate_plan: Raw candidate payload returned by the planner or
                pending-tail cache.
            rejection_reason: Rejection reason surfaced to the next planner
                attempt.
            turn_num: Stepwise turn number.
            attempt_num: Attempt number within the turn.
            strategy: Planner strategy label for the attempt.
            source: Candidate source label.

        Returns:
            JSON-compatible rejection record.
        """

        raw_steps = (
            candidate_plan.get("plan", [])
            if isinstance(candidate_plan.get("plan", []), list)
            else []
        )
        raw_step = (
            deepcopy(raw_steps[0])
            if raw_steps and isinstance(raw_steps[0], dict)
            else {}
        )
        bound_step = self._stepwise_rebind_candidate_step_for_gate(raw_step) if raw_step else {}
        branch_progress = summarize_branch_stage_progress(
            steps=self._stepwise_plan_steps(),
            statuses=list(self.run.get("step_statuses", [])),
            analysis_spec=self._runtime_binding_analysis_spec(),
        )
        gate = _stepwise_rejection_gate_name(rejection_reason)
        record = {
            "turn": int(turn_num),
            "attempt": int(attempt_num),
            "strategy": str(strategy or ""),
            "source": str(source or ""),
            "status": "rejected",
            "tool_name": str(raw_step.get("tool_name", "") or ""),
            "gate": gate,
            "rejection_reason": str(rejection_reason or ""),
            "raw_candidate": deepcopy(candidate_plan),
            "raw_candidate_step": raw_step,
            "bound_candidate_step": bound_step,
            "bound_candidate_changed": bound_step != raw_step,
            "frontier_state": branch_progress,
            "frontier_hint": render_branch_stage_progress_hint(branch_progress),
            "fixture_seed": {
                "kind": "candidate_gate",
                "prefix_state": {
                    "plan": deepcopy(self._run_plan_dict()),
                    "step_statuses": list(self.run.get("step_statuses", [])),
                    "analysis_spec": deepcopy(self._run_analysis_spec_dict()),
                },
                "candidate": deepcopy(candidate_plan),
                "expected_outcome": {
                    "accepted": False,
                    "rejection_gate": gate,
                    "rejection_reason_contains": [
                        str(rejection_reason or "")[:240],
                    ],
                },
            },
        }
        rejected = list(self.run.get("stepwise_rejected_candidates", []) or [])
        rejected.append(record)
        self.run["stepwise_rejected_candidates"] = rejected
        self._append_event(
            step_id=None,
            agent="StepwisePlanner",
            event_type="STEPWISE_CANDIDATE_REJECTED",
            severity="info",
            payload={
                "turn": int(turn_num),
                "attempt": int(attempt_num),
                "strategy": str(strategy or ""),
                "source": str(source or ""),
                "tool_name": str(raw_step.get("tool_name", "") or ""),
                "gate": gate,
                "rejection_reason": str(rejection_reason or ""),
                "frontier_state": branch_progress,
            },
        )
        return record

    def _stepwise_selected_planner_skills(
        self,
        *,
        selection_query: str,
        excluded_tool_names: set[str] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """Return the aligned stepwise planner skill subset and metadata.

        Args:
            selection_query: Stepwise planning prompt text without the
                recommended/installed tool list sections.
            excluded_tool_names: Optional tool names to drop from both the
                returned recommended-skill subset and the full available-skill
                list. Used by the stepwise planner to mask a tool the LLM has
                already duplicate-rejected on earlier attempts this turn,
                forcing it to pick a different next step.
            allowed_tool_names: Optional hard allowlist for the current
                branch-stage frontier. When set, both the recommended and
                installed tool lists are restricted to these wrappers.

        Returns:
            Tuple of ``(selected_skills, selection_meta, available_skills)``.
        """

        try:
            available_skills = self.orchestrator._available_skill_metadata()
        except Exception:
            available_skills = []
        selected_skills, selection_meta = self.orchestrator._select_planner_skill_metadata(
            selection_query,
            available_skills,
            analysis_spec=self._run_analysis_spec_dict(),
        )
        excluded = {
            str(name).strip()
            for name in (excluded_tool_names or set())
            if str(name).strip()
        }
        allowed = {
            str(name).strip()
            for name in (allowed_tool_names or set())
            if str(name).strip()
        }
        if allowed:
            def _allowed_keep(skill: Any) -> bool:
                if not isinstance(skill, dict):
                    return False
                name = str(skill.get("name", "") or "").strip()
                return name in allowed

            selected_skills = [skill for skill in selected_skills if _allowed_keep(skill)]
            available_skills = [skill for skill in available_skills if _allowed_keep(skill)]
            if not selected_skills:
                selected_skills = [dict(skill) for skill in available_skills if isinstance(skill, dict)]
            selection_meta = dict(selection_meta)
            selection_meta["hard_allowed_tool_names"] = sorted(allowed)
            selection_meta["selected_skill_names"] = [
                str(skill.get("name", "")).strip()
                for skill in selected_skills
                if isinstance(skill, dict) and str(skill.get("name", "")).strip()
            ]
        if excluded:
            def _keep(skill: Any) -> bool:
                if not isinstance(skill, dict):
                    return True
                name = str(skill.get("name", "") or "").strip()
                return name not in excluded

            selected_skills = [skill for skill in selected_skills if _keep(skill)]
            available_skills = [skill for skill in available_skills if _keep(skill)]
            selection_meta = dict(selection_meta)
            selection_meta["selected_skill_names"] = [
                str(skill.get("name", "")).strip()
                for skill in selected_skills
                if isinstance(skill, dict) and str(skill.get("name", "")).strip()
            ]
        return selected_skills, selection_meta, available_skills

    def _stepwise_current_allowed_tool_names(
        self,
        *,
        contract_progress: dict[str, Any],
    ) -> set[str]:
        """Return the hard tool allowlist for the current stepwise turn."""

        frontier_allowed_tool_names = self._stepwise_branch_frontier_allowed_tool_names()
        allowed_tool_names = self._stepwise_annotation_prerequisite_allowed_tool_names(
            frontier_allowed_tool_names=frontier_allowed_tool_names,
        )
        if allowed_tool_names:
            return allowed_tool_names
        if frontier_allowed_tool_names:
            return set(frontier_allowed_tool_names)
        workflow_seed_allowed_tool_names = self._stepwise_workflow_seed_allowed_tool_names()
        if workflow_seed_allowed_tool_names:
            return workflow_seed_allowed_tool_names
        return self._stepwise_contract_allowed_tool_names(
            contract_progress=contract_progress,
        )

    def _stepwise_prompt_body(
        self,
        *,
        contract: dict[str, Any],
        contract_progress: dict[str, Any],
        turn_num: int,
        retry_reason: str = "",
        excluded_tool_names: set[str] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> str:
        """Build the shared stepwise prompt body without tool list sections.

        Args:
            contract: Request contract for the run.
            contract_progress: Current contract assessment for the accepted
                prefix.
            turn_num: Current stepwise turn number.
            retry_reason: Optional previous rejection reason.
            excluded_tool_names: Optional tool names the planner has already
                duplicate-rejected this turn; surfaced as a hard "Forbidden
                tools for this turn" directive so the LLM picks a different
                next step instead of re-proposing the same completed tool.
            allowed_tool_names: Optional branch-frontier allowlist surfaced as
                a hard "Required tools" directive for this turn.

        Returns:
            Prompt body used for both selection and planner prompting.
        """

        current_plan = self._run_plan_dict()
        recent_history = self._stepwise_recent_history()
        recent_step_details = self._stepwise_recent_step_details()
        last_failure = self._stepwise_last_failure_context()
        current_plan_summary = _plan_summary_for_repair_prompt(current_plan)
        current_protocol = deepcopy(self.run.get("protocol_validation", {}))
        analysis_spec = self._run_analysis_spec_dict()
        analysis_block = self._stepwise_analysis_brief_block(analysis_spec)
        workflow_seed = self._stepwise_workflow_seed_from_analysis_spec(analysis_spec)
        branching_guidance = self._stepwise_plan_skeleton_branching_guidance(analysis_spec)
        pending_hint = self._stepwise_pending_work_hint()
        pending_hint_block = (
            f"Pending work you must address next:\n{pending_hint}\n\n"
            if pending_hint
            else ""
        )
        forbidden = sorted(
            {
                str(name).strip()
                for name in (excluded_tool_names or set())
                if str(name).strip()
            }
        )
        forbidden_block = (
            "Forbidden tools for this turn (already completed with identical "
            "arguments; re-proposing them will be rejected): "
            + ", ".join(f"`{name}`" for name in forbidden)
            + ". Pick a different tool that advances unfinished work.\n\n"
            if forbidden
            else ""
        )
        allowed = sorted(
            {
                str(name).strip()
                for name in (allowed_tool_names or set())
                if str(name).strip()
            }
        )
        allowed_block = (
            "Required tools for this turn: "
            + ", ".join(f"`{name}`" for name in allowed)
            + ". Do not emit any other tool until this required work is complete.\n\n"
            if allowed
            else ""
        )
        return (
            f"Turn number:\n{turn_num}\n\n"
            f"Original user request:\n{self.cfg.prompt}\n\n"
            + analysis_block
            + branching_guidance
            + "\n"
            + pending_hint_block
            + allowed_block
            + forbidden_block
            + f"Request contract:\n{_json_dumps_safe(contract, indent=2)}\n\n"
            f"Current contract progress:\n{_json_dumps_safe(contract_progress, indent=2)}\n\n"
            f"Current protocol progress:\n{_json_dumps_safe(current_protocol, indent=2)}\n\n"
            f"Executed history:\n{_json_dumps_safe(recent_history, indent=2)}\n\n"
            f"Recent executed step details:\n{_json_dumps_safe(recent_step_details, indent=2)}\n\n"
            f"Workflow seed from analysis spec:\n{_json_dumps_safe(workflow_seed, indent=2)}\n\n"
            f"Current plan summary:\n{_json_dumps_safe(current_plan_summary, indent=2)}\n\n"
            f"Most recent failure context:\n{_json_dumps_safe(last_failure, indent=2)}\n\n"
            + (
                f"Previous rejection reason:\n{retry_reason}\n\n"
                if retry_reason
                else ""
            )
        )

    def _stepwise_analysis_brief_block(self, analysis_spec: dict[str, Any] | None) -> str:
        """Return one compact analysis-brief block for prompt context."""

        brief = build_analysis_brief(analysis_spec)
        if not brief:
            return ""
        return f"\nAnalysis brief:\n{brief}\n"

    def _stepwise_atomic_wrapper_examples(self, available_skills: list[dict[str, Any]]) -> str:
        """Return compact atomic-wrapper examples visible to the planner."""

        visible = {
            str(skill.get("name", "")).strip()
            for skill in available_skills
            if isinstance(skill, dict) and str(skill.get("name", "")).strip()
        }
        preferred = (
            "bcftools_filter_run",
            "bcftools_norm_run",
            "bcftools_isec_run",
            "tabix_index_run",
            "shared_variants_export_run",
            "snpeff_annotate",
            "freebayes_call",
            "featurecounts_run",
        )
        examples = [f"`{name}`" for name in preferred if name in visible]
        if not examples:
            return (
                "`bcftools_filter_run`, `bcftools_norm_run`, `bcftools_isec_run`, "
                "`tabix_index_run`, `shared_variants_export_run`"
            )
        return ", ".join(examples[:8])

    def _stepwise_plan_skeleton_branching_guidance(self, analysis_spec: dict[str, Any] | None) -> str:
        """Return one note clarifying how branch-local stages expand."""

        spec = analysis_spec if isinstance(analysis_spec, dict) else {}
        skeleton = spec.get("plan_skeleton", [])
        if not isinstance(skeleton, list) or not skeleton:
            return ""
        protocol_grounding = (
            spec.get("protocol_grounding", {})
            if isinstance(spec.get("protocol_grounding", {}), dict)
            else {}
        )
        shared_comparison = bool(protocol_grounding.get("requires_shared_comparison", False))
        min_variant_branches = int(protocol_grounding.get("min_variant_branches", 0) or 0)

        branch_markers: list[str] = []
        for entry in skeleton:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                branch_markers.append(str(entry[1] or ""))
            elif isinstance(entry, dict):
                branch_markers.append(str(entry.get("purpose", "") or entry.get("objective", "") or ""))
        branchy_stage = any(
            marker in text.lower()
            for text in branch_markers
            for marker in ("each evolved line", "each sample", "each branch", "per branch", "per sample")
        )
        if not shared_comparison and min_variant_branches < 2 and not branchy_stage:
            return ""
        return (
            "\nIf a listed skeleton stage applies separately to multiple branches, cohorts, or samples, "
            "emit one concrete step per branch while preserving the listed stage order. "
            "The numbered skeleton is a logical stage sequence, not a hard exact step count.\n"
        )

    def _stepwise_workflow_seed_from_analysis_spec(self, analysis_spec: dict[str, Any] | None) -> dict[str, Any]:
        """Build one compact workflow seed from the analysis skeleton."""

        spec = analysis_spec if isinstance(analysis_spec, dict) else {}
        skeleton = spec.get("plan_skeleton", [])
        if not isinstance(skeleton, list) or not skeleton:
            return {
                "thought_process": "No thought process provided by model.",
                "workflow": [],
                "global_constraints": [],
                "final_deliverables": [],
            }

        workflow: list[dict[str, Any]] = []
        previous_step_id: int | None = None
        for idx, entry in enumerate(skeleton, start=1):
            tool_name = ""
            objective = ""
            metadata: dict[str, Any] = {}
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                tool_name = str(entry[0]).strip()
                objective = str(entry[1]).strip()
                if len(entry) >= 3 and isinstance(entry[2], dict):
                    metadata = dict(entry[2])
            elif isinstance(entry, dict):
                tool_name = str(entry.get("tool_name", "")).strip()
                objective = str(entry.get("purpose", "") or entry.get("objective", "")).strip()
                metadata = dict(entry.get("metadata", {})) if isinstance(entry.get("metadata", {}), dict) else {}
            if not tool_name:
                continue
            parameter_hints = (
                dict(metadata.get("parameter_hints", {}))
                if isinstance(metadata.get("parameter_hints", {}), dict)
                else {}
            )
            if not parameter_hints:
                parameter_hints = {
                    str(key).strip(): value
                    for key, value in metadata.items()
                    if str(key).strip() and str(key).strip() not in {"parameter_hints", "downstream_constraints"}
                }
            downstream_constraints = (
                [str(item).strip() for item in metadata.get("downstream_constraints", []) if str(item).strip()]
                if isinstance(metadata.get("downstream_constraints", []), list)
                else []
            )
            workflow.append(
                {
                    "step_id": idx,
                    "tool_name": tool_name,
                    "objective": objective or f"Execute the {tool_name} step.",
                    "depends_on": [previous_step_id] if previous_step_id is not None else [],
                    "branch_id": "",
                    "parameter_hints": parameter_hints,
                    "downstream_constraints": downstream_constraints,
                }
            )
            previous_step_id = idx

        return {
            "thought_process": "Use the assay-level skeleton as a compact workflow seed.",
            "workflow": workflow,
            "global_constraints": [],
            "final_deliverables": [],
        }

    def _stepwise_execution_mode(self) -> bool:
        """Return whether the active harness run uses stepwise planning."""

        return str(getattr(self.cfg, "execution_mode", "batch") or "batch").strip().lower() == "stepwise"

    def _stepwise_max_turns(self) -> int:
        """Return the maximum number of stepwise planning turns."""

        raw = str(os.getenv("BIO_HARNESS_STEPWISE_MAX_TURNS", "40") or "40").strip()
        try:
            value = int(raw)
        except Exception:
            value = 40
        return max(1, min(200, value))

    def _stepwise_planner_attempts_per_turn(self) -> int:
        """Return the maximum planner attempts allowed per stepwise turn.

        Stepwise retries are independent from the batch planner's attempt
        budget (``_planner_max_attempts``): a stepwise retry is much cheaper
        and fundamentally different — it feeds a specific validator-sourced
        ``Previous rejection reason`` back into the next attempt, letting the
        LLM course-correct (e.g. pick the suggested next tool instead of
        re-proposing a completed step). Capping the stepwise ceiling at the
        batch-planner ceiling (historically 3) wasted that retry budget and
        caused turns to fail after only 3 tries on slow open-source models.

        The default is 6 attempts per turn, tunable via
        ``BIO_HARNESS_STEPWISE_ATTEMPTS_PER_TURN``.
        """

        raw = str(os.getenv("BIO_HARNESS_STEPWISE_ATTEMPTS_PER_TURN", "6") or "6").strip()
        try:
            ceiling = int(raw)
        except Exception:
            ceiling = 6
        return max(1, min(12, ceiling))

    def _stepwise_plan_steps(self) -> list[dict[str, Any]]:
        """Return the current executed-or-attempted plan steps."""

        plan = self._run_plan_dict()
        steps = plan.get("plan", [])
        return [dict(step) for step in steps] if isinstance(steps, list) else []

    def _stepwise_recent_history(self, *, limit: int = 8) -> list[dict[str, Any]]:
        """Return a compact recent execution history for prompt context."""

        steps = self._stepwise_plan_steps()
        statuses = self.run.get("step_statuses", [])
        history: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            status = (
                str(statuses[index]).strip()
                if isinstance(statuses, list) and index < len(statuses)
                else "unknown"
            )
            history.append(
                {
                    "step_id": int(step.get("step_id", index + 1) or (index + 1)),
                    "tool_name": str(step.get("tool_name", "") or ""),
                    "status": status,
                }
            )
        return history[-max(1, int(limit)) :]

    def _stepwise_recent_step_details(self, *, limit: int = 4) -> list[dict[str, Any]]:
        """Return recent executed steps with compact argument context.

        Args:
            limit: Maximum number of recent steps to include.

        Returns:
            Compact recent step records with stable scalar arguments preserved.
        """

        steps = self._stepwise_plan_steps()
        statuses = self.run.get("step_statuses", [])
        details: list[dict[str, Any]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            status = (
                str(statuses[index]).strip()
                if isinstance(statuses, list) and index < len(statuses)
                else "unknown"
            )
            raw_args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            compact_args: dict[str, Any] = {}
            for key, value in raw_args.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                if isinstance(value, (int, float, bool)) or value is None:
                    compact_args[key_text] = value
                    continue
                if isinstance(value, str):
                    rendered = value.strip()
                    if not rendered:
                        continue
                    if key_text == "command" and len(rendered) > 240:
                        compact_args[key_text] = rendered[:240] + "...[truncated]"
                    else:
                        compact_args[key_text] = rendered
                    continue
                if isinstance(value, list):
                    compact_items: list[Any] = []
                    for item in value[:6]:
                        if isinstance(item, (int, float, bool)) or item is None:
                            compact_items.append(item)
                        else:
                            compact_items.append(str(item).strip())
                    compact_args[key_text] = compact_items
            details.append(
                {
                    "step_id": int(step.get("step_id", index + 1) or (index + 1)),
                    "tool_name": str(step.get("tool_name", "") or ""),
                    "status": status,
                    "arguments": compact_args,
                }
            )
        return details[-max(1, int(limit)) :]

    def _stepwise_last_failure_context(self) -> dict[str, Any]:
        """Return the most recent failure context for the next-turn prompt."""

        if str(self.run.get("error", "") or "").strip() == "":
            return {}
        return {
            "error": str(self.run.get("error", "") or "").strip(),
            "failure_class": str(classify_failure(self.run) or "").strip(),
            "failure_signatures": list(self.run.get("failure_signatures", [])),
            "recent_history": self._stepwise_recent_history(limit=3),
        }

    def _stepwise_contract_hard_issues(
        self,
        validation: dict[str, Any],
    ) -> list[str]:
        """Return contract issues that must block a stepwise candidate."""

        hard_issues: list[str] = []
        hard_issues.extend(
            str(issue).strip()
            for issue in (validation.get("direct_wrapper_issues", []) or [])
            if str(issue).strip()
        )
        hard_issues.extend(
            str(issue).strip()
            for issue in (validation.get("artifact_role_issues", []) or [])
            if str(issue).strip()
        )
        return hard_issues

    def _stepwise_prompt(
        self,
        *,
        contract: dict[str, Any],
        contract_progress: dict[str, Any],
        turn_num: int,
        retry_reason: str = "",
        recommended_skills: list[dict[str, Any]] | None = None,
        available_skills: list[dict[str, Any]] | None = None,
        excluded_tool_names: set[str] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> str:
        """Build the planner prompt for one next-step decision."""

        available_skills = (
            [dict(skill) for skill in available_skills]
            if isinstance(available_skills, list)
            else []
        )
        if not available_skills:
            try:
                available_skills = self.orchestrator._available_skill_metadata()
            except Exception:
                available_skills = []
        recommended_skills = (
            [dict(skill) for skill in recommended_skills]
            if isinstance(recommended_skills, list)
            else []
        )
        all_tool_names = [
            str(skill.get("name", "")).strip()
            for skill in available_skills
            if isinstance(skill, dict) and str(skill.get("name", "")).strip()
        ]
        recommended_tool_names = [
            str(skill.get("name", "")).strip()
            for skill in recommended_skills
            if isinstance(skill, dict) and str(skill.get("name", "")).strip()
        ]
        other_tool_names = [
            name for name in all_tool_names
            if name not in set(recommended_tool_names)
        ]
        wrapper_examples = self._stepwise_atomic_wrapper_examples(recommended_skills or available_skills)
        instructions = [
            "You are choosing the next executable bioinformatics step.",
            "Return ONLY JSON with keys `thought_process` and `plan`.",
            "The `plan` value must contain exactly one executable step, or be an empty list only when the request is already satisfied.",
            "Do not return `plan_outline`, `workflow`, or any other substitute for `plan`.",
            "Choose only the immediate next step. Do not emit downstream speculative steps.",
            "Each step must perform exactly one logical operation.",
            "Prefer typed wrappers over `bash_run` whenever a wrapper exists.",
            "Use concrete paths. Do not emit placeholders such as <reference_fasta>, ${name}, or {{name}}.",
            "The `tool_name` must exactly match one available tool name from the allowed tool set below. Do not invent wrapper names.",
            f"Available atomic wrapper examples in this tool set include: {wrapper_examples}.",
        ]
        if retry_reason:
            instructions.append(
                "Your previous candidate was rejected. Fix the specific problem and return one valid next step only."
            )
        allowed = sorted(
            {
                str(name).strip()
                for name in (allowed_tool_names or set())
                if str(name).strip()
            }
        )
        if allowed:
            instructions.append(
                "The current unmet harness requirement restricts this turn to these tool names only: "
                + ", ".join(f"`{name}`" for name in allowed)
                + "."
            )
        return (
            "\n".join(instructions)
            + "\n\n"
            f"Recommended tool names for this turn:\n{_json_dumps_safe(recommended_tool_names, indent=2)}\n\n"
            + (
                f"Other installed tool names:\n{_json_dumps_safe(other_tool_names, indent=2)}\n\n"
                if other_tool_names
                else ""
            )
            + self._stepwise_prompt_body(
                contract=contract,
                contract_progress=contract_progress,
                turn_num=turn_num,
                retry_reason=retry_reason,
                excluded_tool_names=excluded_tool_names,
                allowed_tool_names=allowed_tool_names,
            )
        )

    def _stepwise_next_step_id(self) -> int:
        """Return the next stable step id for one appended step."""

        steps = self._stepwise_plan_steps()
        max_step_id = 0
        for index, step in enumerate(steps):
            try:
                max_step_id = max(max_step_id, int(step.get("step_id", index + 1) or (index + 1)))
            except Exception:
                max_step_id = max(max_step_id, index + 1)
        return max_step_id + 1

    def _stepwise_candidate_with_history(
        self,
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        """Append one planner-emitted next step onto the immutable history prefix."""

        current_plan = self._run_plan_dict()
        existing_steps = current_plan.get("plan", []) if isinstance(current_plan.get("plan", []), list) else []
        thought_process = str(current_plan.get("thought_process", "") or "").strip()
        candidate_steps = candidate.get("plan", []) if isinstance(candidate.get("plan", []), list) else []
        appended_step = dict(candidate_steps[0]) if candidate_steps else {}
        appended_step["step_id"] = int(self._stepwise_next_step_id())
        return {
            "thought_process": thought_process,
            "plan": [dict(step) for step in existing_steps] + [appended_step],
        }

    def _stepwise_sanitize_pending_candidate_step(
        self,
        step: Any,
    ) -> dict[str, Any] | None:
        """Return one cached tail step with stale plan-position fields removed."""

        if not isinstance(step, dict):
            return None
        sanitized = deepcopy(step)
        tool_name = str(sanitized.get("tool_name", "") or "").strip()
        if not tool_name:
            return None
        sanitized["tool_name"] = tool_name
        sanitized.pop("step_id", None)
        sanitized.pop("depends_on", None)
        return sanitized

    def _stepwise_sanitize_pending_candidate_steps(
        self,
        steps: Any,
    ) -> list[dict[str, Any]]:
        """Return sanitized cached tail steps that can be revalidated later."""

        if not isinstance(steps, list):
            return []
        sanitized_steps: list[dict[str, Any]] = []
        for step in steps:
            sanitized = self._stepwise_sanitize_pending_candidate_step(step)
            if sanitized is not None:
                sanitized_steps.append(sanitized)
        return sanitized_steps

    def _stepwise_prefix_changed(
        self,
        *,
        candidate_plan: dict[str, Any],
        existing_step_count: int,
    ) -> bool:
        """Return whether normalization mutated the already executed prefix."""

        before_prefix = self._stepwise_plan_steps()
        after_steps = candidate_plan.get("plan", []) if isinstance(candidate_plan.get("plan", []), list) else []
        if len(after_steps) != existing_step_count + 1:
            return True
        before_text = json.dumps(before_prefix[:existing_step_count], sort_keys=True)
        after_text = json.dumps(after_steps[:existing_step_count], sort_keys=True)
        return before_text != after_text

    def _stepwise_prefix_substantively_changed(
        self,
        *,
        candidate_plan: dict[str, Any],
        existing_step_count: int,
    ) -> bool:
        """Return whether normalization changed executed work identity."""

        before_prefix = self._stepwise_plan_steps()
        after_steps = candidate_plan.get("plan", []) if isinstance(candidate_plan.get("plan", []), list) else []
        if len(after_steps) != existing_step_count + 1:
            return True
        for before_step, after_step in zip(
            before_prefix[:existing_step_count],
            after_steps[:existing_step_count],
        ):
            if not isinstance(before_step, dict) or not isinstance(after_step, dict):
                return True
            if str(before_step.get("branch_id", "") or "") != str(after_step.get("branch_id", "") or ""):
                return True
            if str(before_step.get("sample_name", "") or "") != str(after_step.get("sample_name", "") or ""):
                return True
            if self._stepwise_step_signature(before_step) != self._stepwise_step_signature(after_step):
                return True
        return False

    def _stepwise_restore_executed_prefix(
        self,
        *,
        candidate_plan: dict[str, Any],
        existing_step_count: int,
    ) -> dict[str, Any]:
        """Return a candidate plan with the executed prefix restored unchanged.

        Stepwise validation builds a cumulative plan so the normalizers can
        reason about context, but the accepted prefix is immutable. Some
        deterministic binders add defaults or manifest-backed paths to earlier
        steps during that inspection. Preserve those repairs for the appended
        candidate while restoring the already executed calls verbatim.
        """

        steps = candidate_plan.get("plan", [])
        if not isinstance(steps, list) or len(steps) < existing_step_count:
            return candidate_plan
        restored_steps = list(steps)
        restored_steps[:existing_step_count] = deepcopy(
            self._stepwise_plan_steps()[:existing_step_count]
        )
        restored = dict(candidate_plan)
        restored["plan"] = restored_steps
        return restored

    @staticmethod
    def _stepwise_step_signature(step: dict[str, Any]) -> str:
        """Return a stable (tool_name, arguments) signature for one plan step.

        The signature ignores bookkeeping keys such as ``step_id`` and
        ``depends_on`` so it compares only the substantive call identity:
        tool name plus normalized arguments. This lets the stepwise validator
        detect a candidate that exactly duplicates an already-completed step,
        even when the LLM renumbered ``step_id`` or reshuffled ``depends_on``.

        Harness-managed parameters (values the plan normalizer injects, such
        as ``threads``, ``memory_gb``, or default kmer sizes) are stripped
        before signing. Without stripping, a candidate's raw LLM arguments
        would never match the accepted step's post-normalization arguments,
        and duplicate detection would silently fail every time the LLM
        re-submits a step it already completed. The skill registry is the
        authoritative source for which parameter names are harness-managed.
        """

        if not isinstance(step, dict):
            return ""
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name:
            return ""
        raw_args = step.get("arguments", {})
        args_dict = raw_args if isinstance(raw_args, dict) else {}
        managed: set[str] = set()
        try:
            from bio_harness.core.tool_registry import default_tool_registry

            registry = default_tool_registry()
            # Both harness-managed parameters AND declared parameter defaults
            # are stripped. The plan normalizer injects values for any key
            # with a default when the planner omits it, so the accepted step
            # ends up carrying keys the raw LLM candidate never named. If
            # those keys stayed in the signature, their presence on one side
            # and absence on the other would make every duplicate look
            # distinct. Identity-forming arguments (input paths, output
            # paths, sample ids) do not have parameter defaults, so they
            # remain in the signature.
            managed = {
                str(name) for name in registry.harness_managed_parameters_for(tool_name)
            }
            managed.update(str(name) for name in registry.parameter_defaults_for(tool_name).keys())
            buildable_path_keys_for = getattr(registry, "buildable_path_keys_for", None)
            if callable(buildable_path_keys_for):
                managed.update(str(name) for name in buildable_path_keys_for(tool_name))
        except Exception:
            managed = set()
        if managed:
            args_dict = {
                key: value for key, value in args_dict.items() if key not in managed
            }
        try:
            args_text = json.dumps(args_dict, sort_keys=True, default=str)
        except Exception:
            args_text = str(args_dict)
        return f"{tool_name}|{args_text}"

    @staticmethod
    def _stepwise_primary_io_signature(step: dict[str, Any]) -> str:
        """Return a looser (tool_name, primary_I/O) signature for duplicate
        detection (Fix #14).

        The strict signature from ``_stepwise_step_signature`` only matches
        when every non-managed key is identical. In practice the LLM often
        re-emits a step with a *subset* of its original arguments — e.g. the
        accepted step 3 for ``bwa_mem_align`` carried ``sample_name: "anc"``,
        but re-submissions at steps 8-16 omitted ``sample_name`` while keeping
        the identical inputs (``reads_1``, ``reads_2``, ``reference_fasta``)
        and output (``output_bam``). The strict signatures differ, so the
        duplicate guard never fires, and the same alignment runs nine times
        in a row (livelock).

        The primary-I/O signature focuses on the arguments that identify
        *what work is being done*: path-valued inputs, outputs, and
        references. Label/tuning arguments (``sample_name``, ``threads``,
        ``postprocess_mode``) are excluded because they do not change the
        identity of the work. Two steps that share the same tool and the
        same primary I/O paths are doing the same work.

        Returns ``""`` for non-dict steps or when no path-valued I/O args
        are found. An empty signature never matches, so this function is a
        strict refinement: it can only *add* duplicate detections, never
        remove them.
        """

        if not isinstance(step, dict):
            return ""
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name:
            return ""
        raw_args = step.get("arguments", {})
        if not isinstance(raw_args, dict):
            return ""
        # Identify path-valued I/O arguments by name patterns that match
        # every bioinformatics tool in the registry (input/output/reads/
        # reference prefixes, plus common file-type suffixes). We require a
        # path-looking string value to avoid capturing numeric tuning args
        # that happen to use one of these names.
        io_prefixes = ("input_", "output_", "reads_", "reference_")
        io_suffixes = (
            "_fasta", "_fa", "_bam", "_sam", "_cram", "_vcf", "_bcf",
            "_gff", "_gff3", "_gtf", "_bed", "_fastq", "_fq", "_csv",
            "_tsv", "_txt", "_json", "_dir", "_db", "_index",
        )
        io_args: dict[str, Any] = {}
        for key, value in raw_args.items():
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            is_io_key = lowered.startswith(io_prefixes) or lowered.endswith(io_suffixes)
            if not is_io_key:
                continue
            if not isinstance(value, str) or not value:
                continue
            # Path-ish: absolute path or contains a separator / extension dot.
            if "/" not in value and "." not in value:
                continue
            io_args[key] = value
        if not io_args:
            return ""
        try:
            args_text = json.dumps(io_args, sort_keys=True, default=str)
        except Exception:
            args_text = str(io_args)
        return f"{tool_name}|io|{args_text}"

    @staticmethod
    def _stepwise_input_only_signature(step: dict[str, Any]) -> str:
        """Return a (tool_name, input_only) signature for duplicate detection
        (Fix #14b).

        Fix #14 compared the full primary I/O set (inputs, outputs, and
        reference). In practice the LLM re-submits an already-completed
        alignment/variant-call with a *different output path* and/or a
        *different copy of the reference*, so the I/O signatures differ
        even though the sample being processed is identical. Example seen
        in exp26: step 3 (completed) aligned ``anc_R1.fastq.gz`` against
        ``.../assembly/scaffolds.fasta`` and wrote ``.../selected/alignment/
        anc_aligned.bam``; turn-8 candidates aligned the same ``anc_R1
        .fastq.gz`` against ``.../selected/ancestor_ref.fasta`` (a copy of
        the scaffold) and wrote ``.../selected/anc_aligned.bam``. Same
        work, identical inputs — different reference path and output dir.

        The input-only signature therefore strips *outputs and references*
        and considers two steps equivalent when the tool name is the same
        and the primary **input** arguments (``reads_1``/``reads_2``/
        ``input_bam``/``input_vcf``/``input_fasta``/etc.) are identical
        path strings. That matches the semantic identity of the work:
        "I am running ``bwa_mem_align`` on the ``anc`` sample" — no matter
        which copy of the reference is supplied or which output directory
        is chosen.

        Returns ``""`` when the step has no qualifying input args, which
        keeps this a strict refinement that can only add detections.

        Empty-string input signatures never match, so this function is
        strictly additive on top of ``_stepwise_step_signature`` and
        ``_stepwise_primary_io_signature``.
        """

        if not isinstance(step, dict):
            return ""
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name:
            return ""
        raw_args = step.get("arguments", {})
        if not isinstance(raw_args, dict):
            return ""
        input_prefixes = ("input_", "reads_")
        input_args: dict[str, Any] = {}
        for key, value in raw_args.items():
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            if not lowered.startswith(input_prefixes):
                continue
            # Skip output-looking keys that happen to share a prefix
            # ("input_dir" is still an input; "input_bam_output" is a
            # hypothetical edge case — ignore anything containing
            # "output").
            if "output" in lowered:
                continue
            if not isinstance(value, str) or not value:
                continue
            if "/" not in value and "." not in value:
                continue
            input_args[key] = value
        if not input_args:
            return ""
        try:
            args_text = json.dumps(input_args, sort_keys=True, default=str)
        except Exception:
            args_text = str(input_args)
        return f"{tool_name}|inputs|{args_text}"

    @staticmethod
    def _stepwise_resolved_output_signature(step: dict[str, Any]) -> str:
        """Return a (tool_name, resolved_output_paths) signature that maps each
        output path through a filesystem-grounded fuzzy-stem resolver before
        comparison (Fix #14c).

        Fix #14 and #14b compared path strings textually. In practice the LLM
        can rename filenames between accepted and candidate steps for the
        *same* sample — e.g. the accepted step 4 freebayes call wrote
        ``.../variants/anc_raw.vcf`` from ``anc_aligned.bam``, but turn-6
        candidates submit ``ancestor_raw.vcf`` from ``ancestor_aligned.bam``.
        The execution-time path redirector in ``bio_harness/harness/
        path_utils.py`` treats these as the same artifact (same parent
        directory, overlapping stem prefix ``anc``/``ancestor`` → same file
        on disk), but the strict/io/input signatures see different text and
        let the duplicate slip through, producing a livelock where the same
        freebayes call is re-executed turn after turn.

        This resolver mirrors the execution-time fuzzy stem match:

        1. If the candidate output path exists on disk, canonicalize with
           ``os.path.realpath`` — that gives a stable identity even across
           symlinks.
        2. Otherwise, look in the candidate's parent directory for a sibling
           file that (a) has the same file-type extension and (b) shares a
           filename-stem prefix of >=3 characters. A match means the LLM is
           asking to produce an artifact that already lives on disk under a
           different alias. Use its realpath as the identity.
        3. If neither step resolves to a real file, the signature is empty
           and the guard is a no-op — this keeps Fix #14c a strict addition
           on top of the strict/io/input signatures.

        Returns ``""`` when no resolvable output paths are found.
        """

        if not isinstance(step, dict):
            return ""
        tool_name = str(step.get("tool_name", "") or "").strip()
        if not tool_name:
            return ""
        raw_args = step.get("arguments", {})
        if not isinstance(raw_args, dict):
            return ""
        output_paths: list[str] = []
        for key, value in raw_args.items():
            if not isinstance(key, str):
                continue
            lowered = key.lower()
            # Output-style keys: output_*, *_out, *_output_dir, etc.
            is_output_key = lowered.startswith("output_") or lowered.endswith(
                ("_output", "_out", "_outdir", "_output_dir")
            )
            if not is_output_key:
                continue
            if not isinstance(value, str) or not value:
                continue
            if "/" not in value and "." not in value:
                continue
            output_paths.append(value)
        if not output_paths:
            return ""

        def _canonical(path_str: str) -> str:
            try:
                p = Path(path_str)
                if p.exists():
                    return os.path.realpath(str(p))
                parent = p.parent
                if not parent.is_dir():
                    return ""
                # Build the candidate stem prefix (everything before the first
                # "_" in the filename, without extensions). For
                # ``anc_aligned.bam`` -> ``anc``; for ``evol1_sorted.bam`` ->
                # ``evol1``; for ``ancestor_aligned.bam`` -> ``ancestor``.
                # A short/long prefix alias links intentional spelling drift
                # like ``anc``/``ancestor`` without collapsing sibling sample
                # labels such as ``evol1``/``evol2``.
                cand_first_stem = p.name.split(".", 1)[0].split("_", 1)[0]
                if len(cand_first_stem) < 3:
                    return ""
                cand_ext = "".join(p.suffixes).lower()
                best: str = ""
                best_score = 0

                def _sample_stems_alias(candidate: str, sibling: str) -> bool:
                    candidate_l = candidate.lower()
                    sibling_l = sibling.lower()
                    if candidate_l == sibling_l:
                        return True
                    shorter, longer = sorted(
                        (candidate_l, sibling_l),
                        key=lambda item: len(item),
                    )
                    if len(shorter) < 3 or not longer.startswith(shorter):
                        return False
                    return True

                for sibling in parent.iterdir():
                    if not sibling.is_file():
                        continue
                    sib_ext = "".join(sibling.suffixes).lower()
                    if sib_ext != cand_ext:
                        continue
                    sib_first_stem = sibling.name.split(".", 1)[0].split("_", 1)[0]
                    if len(sib_first_stem) < 3:
                        continue
                    if not _sample_stems_alias(cand_first_stem, sib_first_stem):
                        continue
                    # Require the full filename suffix after the first stem
                    # to match too (e.g. both ``*_aligned.bam``). This stops
                    # us from linking ``anc_aligned.bam`` to
                    # ``ancestor_raw.vcf`` just because both start with ``anc``.
                    cand_suffix = p.name[len(cand_first_stem):]
                    sib_suffix = sibling.name[len(sib_first_stem):]
                    if cand_suffix != sib_suffix:
                        continue
                    score = min(len(cand_first_stem), len(sib_first_stem))
                    if score > best_score:
                        best_score = score
                        best = os.path.realpath(str(sibling))
                return best
            except Exception:
                return ""

        resolved: list[str] = []
        for path_str in output_paths:
            canon = _canonical(path_str)
            if canon:
                resolved.append(canon)
        if not resolved:
            return ""
        resolved_sorted = sorted(set(resolved))
        try:
            args_text = json.dumps(resolved_sorted, sort_keys=True, default=str)
        except Exception:
            args_text = str(resolved_sorted)
        return f"{tool_name}|resolved_out|{args_text}"

    def _stepwise_pending_work_hint(self) -> str:
        """Return one prompt-friendly hint naming pending workflow gaps.

        The hint is assembled from signals the harness already computes:

        - ``protocol_validation.missing_required_tools`` — tools the analysis
          protocol expects somewhere in the plan but that are absent from the
          accepted prefix (e.g. ``snpeff_annotate`` for evolution runs).
        - ``protocol_validation.missing_plan_signals`` — structural signals
          (e.g. per-branch variant calls) the protocol still needs.
        - ``contract_validation.missing_capabilities`` — high-level contract
          requirements not yet satisfied.

        The hint is empty when no pending work is detected; callers append it
        to a rejection message to nudge the planner toward a non-duplicate
        candidate. This is deterministic, benchmark-agnostic, and reuses
        validation state the harness already maintains.
        """

        protocol = self.run.get("protocol_validation", {})
        protocol_dict = protocol if isinstance(protocol, dict) else {}
        contract = self.run.get("contract_validation", {})
        contract_dict = contract if isinstance(contract, dict) else {}

        def _clean_list(values: Any) -> list[str]:
            if not isinstance(values, list):
                return []
            cleaned = [str(value).strip() for value in values]
            return [value for value in cleaned if value]

        missing_tools = _clean_list(protocol_dict.get("missing_required_tools", []))
        missing_signals = _clean_list(protocol_dict.get("missing_plan_signals", []))
        missing_capabilities = _clean_list(contract_dict.get("missing_capabilities", []))

        parts: list[str] = []
        branch_stage_hint = self._stepwise_branch_stage_progress_hint()
        if branch_stage_hint:
            parts.append(branch_stage_hint)
        if missing_tools:
            parts.append(
                "Protocol still requires these tools somewhere in the plan: "
                + ", ".join(missing_tools[:6])
                + "."
            )
        if missing_signals:
            parts.append(
                "Protocol still needs these plan signals: "
                + ", ".join(missing_signals[:6])
                + "."
            )
        if missing_capabilities:
            parts.append(
                "Contract still needs these capabilities: "
                + ", ".join(missing_capabilities[:6])
                + "."
            )
        if missing_tools and not branch_stage_hint:
            parts.append(
                f"Suggested next tool: `{missing_tools[0]}` (one of the missing required tools)."
            )
        return " ".join(parts).strip()

    def _stepwise_branch_stage_progress_hint(self) -> str:
        """Return branch-stage frontier guidance for branchy stepwise runs."""

        try:
            from bio_harness.core.branch_stage_progress import (
                render_branch_stage_progress_hint,
                summarize_branch_stage_progress,
            )

            progress = summarize_branch_stage_progress(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
            )
            return render_branch_stage_progress_hint(progress)
        except Exception:  # pragma: no cover — defensive prompt enrichment
            return ""

    def _stepwise_branch_frontier_allowed_tool_names(self) -> set[str]:
        """Return the hard tool allowlist for the current branch frontier."""

        try:
            from bio_harness.core.branch_stage_progress import (
                summarize_branch_stage_progress,
            )

            progress = summarize_branch_stage_progress(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
            )
        except Exception:  # pragma: no cover — defensive prompt enrichment
            return set()
        if (
            not isinstance(progress, dict)
            or not progress
            or bool(progress.get("complete", False))
            or not bool(progress.get("started", False))
        ):
            return set()
        frontier = progress.get("frontier")
        if not isinstance(frontier, list) or not frontier:
            return set()
        return {
            str(item.get("suggested_tool", "") or "").strip()
            for item in frontier
            if isinstance(item, dict) and str(item.get("suggested_tool", "") or "").strip()
        }

    def _stepwise_annotation_prerequisite_allowed_tool_names(
        self,
        *,
        frontier_allowed_tool_names: set[str],
    ) -> set[str]:
        """Return annotation producers required before variant annotation."""

        if not self._stepwise_annotation_prerequisite_is_active(
            frontier_allowed_tool_names=frontier_allowed_tool_names,
        ):
            return set()
        try:
            available_skills = self.orchestrator._available_skill_metadata()
        except Exception:
            available_skills = []
        available_names = {
            str(skill.get("name", "") or "").strip()
            for skill in available_skills
            if isinstance(skill, dict) and str(skill.get("name", "") or "").strip()
        }
        return {
            tool_name
            for tool_name in _STEPWISE_REFERENCE_ANNOTATION_PRODUCERS
            if tool_name in available_names
        }

    def _stepwise_annotation_prerequisite_is_active(
        self,
        *,
        frontier_allowed_tool_names: set[str],
    ) -> bool:
        """Return whether branch annotation is blocked on a real GFF source."""

        if not (
            set(frontier_allowed_tool_names)
            & set(_STEPWISE_VARIANT_ANNOTATION_TOOLS)
        ):
            return False
        analysis_spec = self._run_analysis_spec_dict()
        if str(analysis_spec.get("analysis_type", "") or "") != (
            "bacterial_evolution_variant_calling"
        ):
            return False
        return not self._stepwise_reference_annotation_available()

    def _stepwise_reference_annotation_available(self) -> bool:
        """Return whether a concrete GFF/GFF3 reference annotation exists."""

        candidates = list(self._stepwise_completed_reference_annotation_paths())
        candidates.extend(self._stepwise_common_reference_annotation_paths())
        for path in candidates:
            try:
                if path.exists():
                    return True
            except OSError:
                continue
        return False

    def _stepwise_completed_reference_annotation_paths(self) -> list[Path]:
        """Return completed producer GFF paths from the accepted prefix."""

        paths: list[Path] = []
        statuses = list(self.run.get("step_statuses", []))
        for index, step in enumerate(self._stepwise_plan_steps()):
            if index >= len(statuses):
                continue
            if str(statuses[index]).strip().lower() != "completed":
                continue
            tool_name = str(step.get("tool_name", "") or "").strip()
            if tool_name not in _STEPWISE_REFERENCE_ANNOTATION_PRODUCERS:
                continue
            args = step.get("arguments", {}) if isinstance(step.get("arguments", {}), dict) else {}
            for key in ("output_gff", "output_gff3", "annotation_gff"):
                raw_path = str(args.get(key, "") or "").strip()
                if raw_path:
                    paths.append(Path(raw_path).expanduser())
            if tool_name == "prokka_annotate":
                output_dir = str(args.get("output_dir", "") or "").strip()
                sample_prefix = str(args.get("sample_prefix", "") or "").strip()
                if output_dir and sample_prefix:
                    prefix_path = Path(output_dir).expanduser() / sample_prefix
                    paths.append(prefix_path.with_suffix(".gff"))
                    paths.append(prefix_path.with_suffix(".gff3"))
        return paths

    def _stepwise_common_reference_annotation_paths(self) -> list[Path]:
        """Return common user- or harness-supplied annotation path candidates."""

        roots: list[Path] = []
        for raw_root in (
            getattr(self.cfg, "selected_dir", None),
            getattr(self.cfg, "data_root", None),
        ):
            if raw_root:
                roots.append(Path(str(raw_root)).expanduser())
        analysis_spec = self._run_analysis_spec_dict()
        requested_root = str(analysis_spec.get("requested_data_root", "") or "").strip()
        if requested_root:
            roots.append(Path(requested_root).expanduser())

        paths: list[Path] = []
        relative_names = (
            "annotation/genes.gff",
            "annotation/ancestor.gff",
            "assembly/genes.gff",
            "references/genes.gff",
            "genes.gff",
            "genes.gff3",
            "reference.gff",
            "reference.gff3",
        )
        seen: set[str] = set()
        for root in roots:
            for relative in relative_names:
                candidate = (root / relative).resolve(strict=False)
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                paths.append(candidate)
        return paths

    def _stepwise_candidate_satisfies_annotation_prerequisite(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> bool:
        """Return whether the candidate produces the active GFF prerequisite."""

        tool_name = str(candidate_step.get("tool_name", "") or "").strip()
        if tool_name not in _STEPWISE_REFERENCE_ANNOTATION_PRODUCERS:
            return False
        frontier_allowed = self._stepwise_branch_frontier_allowed_tool_names()
        return self._stepwise_annotation_prerequisite_is_active(
            frontier_allowed_tool_names=frontier_allowed,
        )

    def _stepwise_annotation_prerequisite_rejection_reason(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> str:
        """Return a rejection when variant annotation lacks a GFF producer."""

        tool_name = str(candidate_step.get("tool_name", "") or "").strip()
        if tool_name not in _STEPWISE_VARIANT_ANNOTATION_TOOLS:
            return ""
        frontier_allowed = self._stepwise_branch_frontier_allowed_tool_names()
        if not self._stepwise_annotation_prerequisite_is_active(
            frontier_allowed_tool_names=frontier_allowed,
        ):
            return ""
        producers = self._stepwise_annotation_prerequisite_allowed_tool_names(
            frontier_allowed_tool_names=frontier_allowed,
        )
        producer_text = (
            ", ".join(f"`{name}`" for name in sorted(producers))
            if producers
            else ", ".join(f"`{name}`" for name in _STEPWISE_REFERENCE_ANNOTATION_PRODUCERS)
        )
        return (
            "Candidate requires a reference gene annotation GFF before variant "
            "consequence annotation, but no completed or existing GFF is available. "
            f"Emit one of: {producer_text} to produce the annotation GFF before "
            f"`{tool_name}`."
        )

    def _stepwise_contract_allowed_tool_names(
        self,
        *,
        contract_progress: dict[str, Any],
    ) -> set[str]:
        """Return installed wrappers that satisfy currently missing contract work."""

        contract_dict = contract_progress if isinstance(contract_progress, dict) else {}
        protocol = self.run.get("protocol_validation", {})
        protocol_dict = protocol if isinstance(protocol, dict) else {}

        def _clean_list(values: Any) -> list[str]:
            if not isinstance(values, list):
                return []
            return [str(value).strip() for value in values if str(value).strip()]

        candidates: list[str] = []
        candidates.extend(_clean_list(protocol_dict.get("missing_required_tools", [])))
        candidates.extend(_clean_list(contract_dict.get("missing_required_tool_hints", [])))
        candidates.extend(_clean_list(contract_dict.get("missing_tool_hints", [])))

        capability_specs = getattr(self, "capability_specs", {}) or {}
        for capability in _clean_list(contract_dict.get("missing_capabilities", [])):
            spec = capability_specs.get(capability, {}) if isinstance(capability_specs, dict) else {}
            if not spec:
                try:
                    from bio_harness.core.contracts import DEFAULT_CAPABILITY_SPECS

                    spec = DEFAULT_CAPABILITY_SPECS.get(capability, {})
                except Exception:  # pragma: no cover — defensive metadata fallback
                    spec = {}
            if isinstance(spec, dict):
                candidates.extend(_clean_list(spec.get("plan_signals", [])))
                continue
            candidates.extend(_clean_list(getattr(spec, "plan_signals", [])))

        try:
            available_skills = self.orchestrator._available_skill_metadata()
        except Exception:
            available_skills = []
        available_names = {
            str(skill.get("name", "") or "").strip()
            for skill in available_skills
            if isinstance(skill, dict) and str(skill.get("name", "") or "").strip()
        }
        return {name for name in candidates if name in available_names}

    def _stepwise_workflow_seed_allowed_tool_names(self) -> set[str]:
        """Return hard tool choices from the next compiled workflow-seed step."""

        analysis_spec = self._runtime_binding_analysis_spec()
        if not _stepwise_analysis_spec_is_compiled_pipeline(analysis_spec):
            return set()
        expected_entry = _stepwise_next_unsatisfied_plan_skeleton_entry(
            analysis_spec=analysis_spec,
            steps=self._stepwise_plan_steps(),
            statuses=list(self.run.get("step_statuses", [])),
        )
        expected_tool = str(expected_entry.get("tool_name", "") or "").strip().lower()
        if not expected_tool:
            return set()

        allowed_tools = {expected_tool}
        allowed_tools.update(_stepwise_graph_pipeline_tools(analysis_spec))
        if self._stepwise_structural_variant_raw_read_inputs(analysis_spec):
            allowed_tools.add("minimap2_align")
        try:
            available_skills = self.orchestrator._available_skill_metadata()
        except Exception:
            available_skills = []
        available_names = {
            str(skill.get("name", "") or "").strip().lower()
            for skill in available_skills
            if isinstance(skill, dict) and str(skill.get("name", "") or "").strip()
        }
        return {name for name in allowed_tools if name in available_names}

    def _stepwise_branch_stage_rejection_reason(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> str:
        """Return a hard frontier rejection for branchy stepwise candidates."""

        prerequisite_rejection = self._stepwise_annotation_prerequisite_rejection_reason(
            candidate_step=candidate_step,
        )
        if prerequisite_rejection:
            return prerequisite_rejection
        if self._stepwise_candidate_satisfies_annotation_prerequisite(
            candidate_step=candidate_step,
        ):
            return ""

        try:
            from bio_harness.core.branch_stage_progress import (
                assess_candidate_branch_stage_frontier,
            )

            assessment = assess_candidate_branch_stage_frontier(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
                candidate_step=candidate_step,
            )
        except Exception:  # pragma: no cover — defensive guard
            return ""
        if bool(assessment.get("passed", True)):
            return ""
        reason = str(assessment.get("reason", "") or "").strip()
        if not reason:
            return ""
        hint = self._stepwise_pending_work_hint()
        return reason + (f" {hint}" if hint else "")

    def _stepwise_candidate_advances_branch_frontier(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> bool:
        """Return whether a candidate advances an active branch frontier."""

        try:
            from bio_harness.core.branch_stage_progress import (
                assess_candidate_branch_stage_frontier,
                branch_stage_cell_for_step,
            )

            if branch_stage_cell_for_step(candidate_step) is None:
                return False
            assessment = assess_candidate_branch_stage_frontier(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
                candidate_step=candidate_step,
            )
        except Exception:  # pragma: no cover - defensive guard
            return False

        progress = assessment.get("progress", {})
        return (
            isinstance(progress, dict)
            and bool(progress.get("started", False))
            and not bool(progress.get("complete", False))
            and bool(assessment.get("passed", False))
        )

    def _stepwise_planner_trace_progress_assessment(
        self,
        *,
        planner_trace_dir: str,
        latest_name: str,
    ) -> dict[str, Any]:
        """Return whether a stepwise planner trace advances usable work.

        Args:
            planner_trace_dir: Directory containing planner trace artifacts.
            latest_name: File name of the newest planner trace artifact.

        Returns:
            Mapping with ``productive`` plus diagnostic details. A trace is
            productive only when it contains a structured candidate that is not
            completed-prefix work and is compatible with the current
            branch-stage frontier.
        """

        name = str(latest_name or "").strip()
        if not name:
            return {"productive": False, "reason": "missing_trace_artifact_name"}
        if not (
            name.endswith("_structured_success.json")
            or name.endswith("_hierarchical_plan_success.json")
        ):
            return {
                "productive": False,
                "reason": "trace_artifact_not_structured_candidate",
                "details": {"progress_file": name},
            }
        trace_path = Path(planner_trace_dir) / name
        candidate_steps = self._stepwise_candidate_steps_from_trace(trace_path)
        if not candidate_steps:
            return {
                "productive": False,
                "reason": "structured_trace_has_no_candidate_steps",
                "details": {"progress_file": name},
            }
        chosen_index = self._stepwise_first_nonduplicate_candidate_index(candidate_steps)
        candidate_step = (
            dict(candidate_steps[chosen_index])
            if 0 <= chosen_index < len(candidate_steps)
            and isinstance(candidate_steps[chosen_index], dict)
            else {}
        )
        if not candidate_step:
            return {
                "productive": False,
                "reason": "structured_trace_candidate_not_mapping",
                "details": {"progress_file": name, "chosen_index": int(chosen_index)},
            }
        duplicate_prior = self._stepwise_duplicate_completed_step(
            candidate_step=candidate_step,
        )
        if duplicate_prior:
            return {
                "productive": False,
                "reason": "candidate_duplicates_completed_prefix",
                "details": {
                    "progress_file": name,
                    "chosen_index": int(chosen_index),
                    "tool_name": str(candidate_step.get("tool_name", "") or ""),
                    "duplicate_prior": duplicate_prior,
                },
            }
        branch_rejection = self._stepwise_trace_branch_stage_rejection(
            candidate_step=candidate_step,
        )
        if branch_rejection:
            return {
                "productive": False,
                "reason": "candidate_does_not_advance_branch_frontier",
                "details": {
                    "progress_file": name,
                    "chosen_index": int(chosen_index),
                    "tool_name": str(candidate_step.get("tool_name", "") or ""),
                    "branch_stage_rejection": branch_rejection,
                },
            }
        return {
            "productive": True,
            "reason": "structured_candidate_advances_stepwise_progress",
            "details": {
                "progress_file": name,
                "chosen_index": int(chosen_index),
                "tool_name": str(candidate_step.get("tool_name", "") or ""),
            },
        }

    def _stepwise_candidate_steps_from_trace(self, trace_path: Path) -> list[dict[str, Any]]:
        """Extract candidate steps from one planner trace wrapper."""

        try:
            trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_payload: Any = trace_payload
        if isinstance(trace_payload, dict):
            raw_content_file = str(trace_payload.get("raw_content_file", "") or "").strip()
            if raw_content_file:
                try:
                    raw_payload = json.loads(Path(raw_content_file).read_text(encoding="utf-8"))
                except Exception:
                    raw_payload = trace_payload.get("raw_excerpt", {})
                    if isinstance(raw_payload, str):
                        try:
                            raw_payload = json.loads(raw_payload)
                        except Exception:
                            raw_payload = {}
        return self._stepwise_candidate_steps_from_payload(raw_payload)

    @staticmethod
    def _stepwise_candidate_steps_from_payload(payload: Any) -> list[dict[str, Any]]:
        """Extract executable step mappings from a planner payload."""

        if isinstance(payload, list):
            return [dict(item) for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        if str(payload.get("tool_name", "") or "").strip():
            return [dict(payload)]
        for key in ("plan", "steps", "workflow"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        return []

    def _stepwise_first_nonduplicate_candidate_index(
        self,
        steps: list[dict[str, Any]],
        *,
        excluded_tool_names: set[str] | None = None,
    ) -> int:
        """Return the first candidate index that advances the current frontier."""

        excluded = set(excluded_tool_names or set())
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name", "") or "").strip()
            if tool_name and tool_name in excluded:
                continue
            if self._stepwise_duplicate_completed_step(candidate_step=step):
                continue
            if self._stepwise_trace_branch_stage_rejection(candidate_step=step):
                continue
            return index
        return 0

    def _stepwise_trace_branch_stage_rejection(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> str:
        """Return a branch-frontier rejection for trace progress filtering."""

        prerequisite_rejection = self._stepwise_annotation_prerequisite_rejection_reason(
            candidate_step=candidate_step,
        )
        if prerequisite_rejection:
            return prerequisite_rejection
        if self._stepwise_candidate_satisfies_annotation_prerequisite(
            candidate_step=candidate_step,
        ):
            return ""

        try:
            from bio_harness.core.branch_stage_progress import (
                assess_candidate_branch_stage_frontier,
                branch_stage_cell_for_step,
                summarize_branch_stage_progress,
            )

            progress = summarize_branch_stage_progress(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
            )
            assessment = assess_candidate_branch_stage_frontier(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
                candidate_step=candidate_step,
            )
            candidate_cell = branch_stage_cell_for_step(candidate_step)
        except Exception:
            return ""
        if bool(assessment.get("passed", True)):
            if (
                isinstance(progress, dict)
                and progress
                and not bool(progress.get("complete", False))
                and bool(progress.get("started", False))
                and candidate_cell is None
            ):
                tool_name = str(candidate_step.get("tool_name", "") or "").strip()
                frontier = progress.get("frontier", [])
                frontier_tools = {
                    str(cell.get("suggested_tool", "") or "").strip()
                    for cell in frontier
                    if isinstance(cell, dict)
                    and str(cell.get("suggested_tool", "") or "").strip()
                }
                if tool_name and tool_name in frontier_tools:
                    return ""
                return (
                    "Candidate is not a tracked branch-frontier step while "
                    "branch-local evolution work is incomplete."
                )
            return ""
        return str(assessment.get("reason", "") or "").strip()

    def _stepwise_missing_candidate_inputs(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> list[str]:
        """Return declared input paths that neither exist on disk nor are scheduled.

        Fix #22b: when the stepwise planner emits a step whose declared
        input-path arguments refer to files that (a) don't exist on disk AND
        (b) aren't scheduled as outputs of any step in the already-accepted
        plan prefix, the step cannot succeed. The classic failure mode is a
        branch-aggregator (``shared_variants_export_run``, ``bcftools_isec_run``,
        ``bcftools_merge``, etc.) being emitted before the per-branch chains
        that produce its inputs have run. Before Fix #22b the stepwise loop
        would run the plan normalizer on the candidate, which then inserts
        the missing branch prerequisites to satisfy artifact-role invariants
        — the inserted steps make the plan longer than ``existing+1`` and
        ``_stepwise_prefix_changed`` rejects the candidate with the cryptic
        "Candidate mutated the already executed plan prefix" error. The
        planner cannot act on that directive because it never mentions which
        input is missing. After Fix #22b the candidate is rejected earlier
        with a structured hint that names every missing path, so the planner
        can emit the producing step on the next turn.

        The check is tool-agnostic: it uses the tool registry's declared
        ``input_path_keys`` / ``output_argument_keys`` rather than hardcoding
        per-analysis rules, so the same guard fires for every analysis type
        whose binder exposes canonical input paths. Returns ``[]`` when every
        declared input is either present on disk or scheduled as the output
        of a prior step in the accepted prefix.

        The candidate step is strict-bound before inspection so the check
        reasons about the canonical scaffold paths (what the step will
        actually run with), not the planner-submitted raw paths (which may
        be hallucinated and would silently pass the existence check).
        """

        tool_name = str(candidate_step.get("tool_name", "") or "").strip()
        if not tool_name:
            return []

        # Bind the candidate to canonical paths so the check reasons about
        # what will actually execute, not planner-provided paths.
        bound_args: dict[str, Any] = {}
        try:
            from bio_harness.core.strict_artifact_binding import (
                bind_step_spec_for_strict_mode,
            )

            analysis_spec = dict(self._runtime_binding_analysis_spec())
            # Fix #23-stepwise: the plan-normalization path populates
            # ``selected_dir`` and ``requested_data_root`` from context before
            # calling into the binder (see
            # ``run_agent_e2e_plan_normalization_support.py``). The stepwise
            # loop bypasses that path, so the binder saw empty context and
            # could not canonicalize candidate inputs before this early
            # missing-input gate. Inject the same defaults here so the guard
            # reasons about the executable scaffold, not raw planner paths.
            if not str(analysis_spec.get("selected_dir", "") or "").strip():
                try:
                    cfg_selected_dir = str(getattr(self.cfg, "selected_dir", "") or "").strip()
                    if cfg_selected_dir:
                        analysis_spec["selected_dir"] = str(
                            Path(cfg_selected_dir).expanduser().resolve(strict=False)
                        )
                except Exception:  # pragma: no cover — defensive
                    pass
            if not str(analysis_spec.get("requested_data_root", "") or "").strip():
                try:
                    cfg_data_root = str(getattr(self.cfg, "data_root", "") or "").strip()
                    if cfg_data_root:
                        analysis_spec["requested_data_root"] = str(
                            Path(cfg_data_root).expanduser().resolve(strict=False)
                        )
                except Exception:  # pragma: no cover — defensive
                    pass
            bound = bind_step_spec_for_strict_mode(
                step_spec=dict(candidate_step),
                workflow_step=dict(candidate_step),
                analysis_spec=analysis_spec,
            )
            if isinstance(bound, dict) and isinstance(bound.get("arguments"), dict):
                bound_args = dict(bound["arguments"])  # type: ignore[arg-type]
        except Exception:  # pragma: no cover — defensive
            bound_args = {}

        # Fall back to raw candidate args if binder produced nothing useful.
        if not bound_args:
            raw_args = candidate_step.get("arguments")
            if isinstance(raw_args, dict):
                bound_args = dict(raw_args)

        if not bound_args:
            return []

        try:
            from bio_harness.core.tool_registry import default_tool_registry

            registry = default_tool_registry()
            input_keys = list(registry.input_keys_for(tool_name))
        except Exception:  # pragma: no cover — defensive
            return []

        if not input_keys:
            return []

        # Collect every output path scheduled by the already-accepted plan
        # prefix. A candidate input that matches one of these is considered
        # "scheduled" (the producing step will run before it), so the
        # missing-on-disk state is expected and not a failure condition.
        scheduled_outputs: set[str] = set()
        for step in self._stepwise_plan_steps():
            prior_tool = str(step.get("tool_name", "") or "").strip()
            if not prior_tool:
                continue
            prior_args = step.get("arguments", {}) or {}
            if not isinstance(prior_args, dict):
                continue
            try:
                prior_output_keys = registry.output_argument_keys_for(prior_tool)
            except Exception:  # pragma: no cover — defensive
                prior_output_keys = []
            for key in prior_output_keys:
                value = prior_args.get(key)
                if isinstance(value, str) and value.strip():
                    scheduled_outputs.add(value.strip())
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            scheduled_outputs.add(item.strip())

        # Collect filesystem anchors we'll resolve relative paths against
        # before concluding a file is "missing". The binder only rewrites
        # bare filenames to absolute canonical paths when it recognizes the
        # analysis_type + tool combo; if the tool isn't in the binder's
        # case list (e.g. a raw-input producer like ``spades_assemble``
        # reached the check before the binder populated ``data_root``),
        # the candidate args may still contain bare filenames like
        # ``anc_R1.fastq.gz``. Path.exists() resolves those against cwd
        # (typically the repo root), giving a false negative. Checking
        # against the known data_root + selected_dir anchors instead
        # matches what the executor will actually run with.
        anchors: list[Path] = []
        try:
            selected_dir = getattr(self.cfg, "selected_dir", None)
            if selected_dir:
                anchors.append(Path(str(selected_dir)))
        except Exception:  # pragma: no cover — defensive
            pass
        try:
            data_root = getattr(self.cfg, "data_root", None)
            if data_root:
                anchors.append(Path(str(data_root)))
        except Exception:  # pragma: no cover — defensive
            pass
        try:
            requested = str(
                self.run.get("analysis_spec", {}).get("requested_data_root", "") or ""
            ).strip() if isinstance(self.run.get("analysis_spec", {}), dict) else ""
            if requested:
                anchors.append(Path(requested))
        except Exception:  # pragma: no cover — defensive
            pass

        missing: list[str] = []
        seen: set[str] = set()
        for key in input_keys:
            value = bound_args.get(key)
            paths: list[str] = []
            if isinstance(value, str) and value.strip():
                paths.append(value.strip())
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        paths.append(item.strip())
            for path_str in paths:
                if path_str in seen:
                    continue
                seen.add(path_str)
                if path_str in scheduled_outputs:
                    continue
                # Only fire for absolute paths the executor cannot itself
                # resolve elsewhere. Relative / bare-filename paths are
                # ambiguous here — the binder normally canonicalizes them
                # on recognized analysis types, and when it can't, the
                # executor's own preflight will try data_root, selected_dir,
                # and cwd resolution. Rejecting here on an un-resolved bare
                # name yields the repo-root false positive seen in exp38.
                candidate_path = Path(path_str)
                if not candidate_path.is_absolute():
                    resolved_ok = False
                    for anchor in anchors:
                        try:
                            if (anchor / path_str).exists():
                                resolved_ok = True
                                break
                        except Exception:  # pragma: no cover — defensive
                            pass
                    if resolved_ok:
                        continue
                    # Bare/relative name that doesn't resolve under any
                    # known anchor — skip rather than flag. The later
                    # executor preflight will emit a concrete file-not-
                    # found with the full search-anchor list.
                    continue
                try:
                    if candidate_path.exists():
                        continue
                except Exception:  # pragma: no cover — defensive
                    pass
                missing.append(path_str)
        return missing

    def _stepwise_duplicate_completed_step(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the prior completed step a candidate exactly duplicates.

        A candidate is a duplicate when it shares the exact same (tool_name,
        arguments) signature as a step in the accepted prefix whose execution
        status is successful. This is the generalized livelock guard: without
        it, a slow planner can repeatedly re-propose a step it already ran
        (e.g. spades_assemble on the same inputs) and the harness will happily
        re-execute it, wasting compute and starving the workflow of forward
        progress.

        Returns ``{}`` when no completed duplicate exists.
        """

        candidate_signature = self._stepwise_step_signature(candidate_step)
        candidate_io_signature = self._stepwise_primary_io_signature(candidate_step)
        candidate_input_signature = self._stepwise_input_only_signature(candidate_step)
        candidate_resolved_out_signature = self._stepwise_resolved_output_signature(
            candidate_step
        )
        # Fix #14d: candidates that emit a bare ``{step_id, tool_name}``
        # step with NO ``arguments`` field AND no distinguishing metadata
        # cannot be told apart from a prior completed step by any
        # path-based signature — every path signature collapses to the
        # empty string, and even the strict signature degenerates to
        # ``tool_name|{}`` which won't match a prior step's real args. The
        # normalizer downstream fills the empty slot from context and runs
        # the identical command, producing a livelock (seen in exp28 where
        # freebayes_call ran 3× on ``anc_aligned.bam`` with identical full
        # paths, because the LLM emitted only ``{step_id, tool_name}`` for
        # turns 7-8).
        #
        # Fix #14d refinement (after exp29 over-match): LLMs frequently
        # submit legitimate new-sample steps with ``arguments`` omitted but
        # with ``parameter_hints`` / ``branch_id`` / ``sample_name`` /
        # ``objective`` carrying explicit per-sample intent (e.g.
        # ``{"step_id": 5, "tool_name": "bwa_mem_align", "branch_id":
        # "evol1", "parameter_hints": {"sample_name": "evol1"},
        # "objective": "Align evolved line 1 reads..."}``). Rejecting these
        # as duplicates of the anc alignment blocks forward progress to
        # downstream samples (new livelock class). A candidate is treated
        # as "truly bare" only when **none** of the sample-distinguishing
        # metadata fields is populated. That keeps Fix #14d a narrow
        # catcher for the exp28 ``{step_id, tool_name}``-only pattern
        # while letting sample-parameterized bare-arg steps through.
        def _has_content(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, (str, list, tuple, set, dict)):
                return bool(value)
            return True

        _raw_args = (
            candidate_step.get("arguments")
            if isinstance(candidate_step, dict)
            else None
        )
        _param_hints = (
            candidate_step.get("parameter_hints")
            if isinstance(candidate_step, dict)
            else None
        )
        _branch_id = (
            candidate_step.get("branch_id")
            if isinstance(candidate_step, dict)
            else None
        )
        _sample_name = (
            candidate_step.get("sample_name")
            if isinstance(candidate_step, dict)
            else None
        )
        _objective = (
            candidate_step.get("objective")
            if isinstance(candidate_step, dict)
            else None
        )
        candidate_is_bare = not (
            _has_content(_raw_args)
            or _has_content(_param_hints)
            or _has_content(_branch_id)
            or _has_content(_sample_name)
            or _has_content(_objective)
        )
        if not (
            candidate_signature
            or candidate_io_signature
            or candidate_input_signature
            or candidate_resolved_out_signature
            or candidate_is_bare
        ):
            return {}
        candidate_tool = str(candidate_step.get("tool_name", "") or "").strip() if isinstance(candidate_step, dict) else ""
        existing_steps = self._stepwise_plan_steps()
        statuses = self.run.get("step_statuses", [])
        success_markers = {"succeeded", "success", "completed", "ok", "done"}
        for index, prior_step in enumerate(existing_steps):
            status_text = ""
            if isinstance(statuses, list) and index < len(statuses):
                status_text = str(statuses[index]).strip().lower()
            if status_text not in success_markers:
                continue
            prior_signature = self._stepwise_step_signature(prior_step)
            prior_io_signature = self._stepwise_primary_io_signature(prior_step)
            prior_input_signature = self._stepwise_input_only_signature(prior_step)
            prior_resolved_out_signature = self._stepwise_resolved_output_signature(
                prior_step
            )
            prior_tool = str(prior_step.get("tool_name", "") or "").strip()
            # Fix #14/#14b/#14c/#14d: match on any of five progressively
            # looser signatures. Strict (full args) is the default.
            # Primary-I/O matches when the LLM drops a label arg but keeps
            # the same inputs/outputs. Input-only matches when the LLM
            # keeps the same inputs but redirects the output or swaps in
            # an equivalent-content reference (e.g. a cp-copy of the
            # scaffold). Resolved-output (Fix #14c) matches when the LLM
            # renames the
            # output path (e.g. ``ancestor_aligned.bam`` instead of
            # ``anc_aligned.bam``) to a filename that fuzzy-resolves to an
            # already-produced artifact on disk. The actual work — aligning
            # these reads, calling variants on this BAM, producing this
            # already-existing file — is identical in every case.
            strict_match = (
                bool(candidate_signature)
                and bool(prior_signature)
                and prior_signature == candidate_signature
            )
            io_match = (
                bool(candidate_io_signature)
                and bool(prior_io_signature)
                and prior_io_signature == candidate_io_signature
            )
            input_match = (
                bool(candidate_input_signature)
                and bool(prior_input_signature)
                and prior_input_signature == candidate_input_signature
            )
            resolved_out_match = (
                bool(candidate_resolved_out_signature)
                and bool(prior_resolved_out_signature)
                and prior_resolved_out_signature == candidate_resolved_out_signature
            )
            # Fix #14d: bare-args candidate (no path I/O) matches any
            # completed prior step with the same tool_name. See the
            # ``candidate_is_bare`` comment above for rationale.
            bare_match = (
                candidate_is_bare
                and bool(candidate_tool)
                and bool(prior_tool)
                and candidate_tool == prior_tool
            )
            if not (
                strict_match or io_match or input_match or resolved_out_match or bare_match
            ):
                continue
            return {
                "step_id": int(prior_step.get("step_id", index + 1) or (index + 1)),
                "tool_name": str(prior_step.get("tool_name", "") or ""),
                "status": status_text,
            }
        return {}

    def _stepwise_required_arg_rejection_reason(
        self,
        *,
        plan: dict[str, Any],
    ) -> str:
        """Return a rejection message if the plan has missing required arguments.

        Mirrors the executor's ``_check_required_arguments`` preflight check so
        that a missing argument triggers an in-turn retry with a specific
        ``retry_reason`` rather than letting the stepwise loop accept the step
        and learn about the failure only at executor preflight (after the
        accepted plan prefix has already grown by one pending step).

        Returns an empty string when every step has all required arguments.
        """

        try:
            from bio_harness.core.plan_validation import (
                Severity,
                _check_required_arguments,
            )
            from bio_harness.core.tool_registry import default_tool_registry
        except Exception:  # pragma: no cover - defensive import guard
            return ""

        try:
            registry = default_tool_registry()
        except Exception:  # pragma: no cover - registry build is best-effort
            return ""

        plan_dict = plan if isinstance(plan, dict) else {}
        try:
            findings = _check_required_arguments(plan_dict, registry)
        except Exception:  # pragma: no cover - registry bugs must not crash the loop
            return ""
        errors = [
            finding
            for finding in findings
            if getattr(finding, "severity", None) == Severity.ERROR
        ]
        if not errors:
            return ""
        # Only report errors on the last (candidate) step — the accepted
        # prefix is immutable so warnings on earlier steps would just
        # re-reject every attempt indefinitely.
        steps = plan_dict.get("plan", []) if isinstance(plan_dict.get("plan", []), list) else []
        last_step_id: Any = None
        if steps and isinstance(steps[-1], dict):
            last_step_id = steps[-1].get("step_id")
        relevant = [
            finding
            for finding in errors
            if last_step_id is None or finding.step_id == last_step_id
        ]
        if not relevant:
            return ""

        # Fix #13: before rejecting, try to auto-populate missing required
        # args from concrete artifacts produced by prior completed steps
        # (e.g. ``input_vcf`` populated from the most recent completed
        # step's ``output_vcf``). Without this, the stepwise loop
        # exhausts a turn's attempts on tools like ``snpeff_annotate``
        # that the LLM emits without file-path args; Fix #11 then masks
        # the tool, and the planner has no alternative tool that can
        # satisfy the remaining protocol requirement (observed in exp22
        # for prokka and exp24 for snpeff). Auto-population is safe
        # because we only fill from concrete prior-step paths, never
        # guessed values.
        if steps and isinstance(steps[-1], dict):
            missing_names: list[str] = []
            for finding in relevant:
                msg = str(getattr(finding, "message", ""))
                match = re.search(
                    r"missing required argument\(s\):\s*([^\.;]+)",
                    msg,
                    re.IGNORECASE,
                )
                if match:
                    for raw_name in match.group(1).split(","):
                        name = raw_name.strip()
                        if name:
                            missing_names.append(name)
            if missing_names:
                filled = self._try_autofill_required_args(
                    step=steps[-1],
                    missing_arg_names=missing_names,
                )
                if filled:
                    try:
                        findings2 = _check_required_arguments(plan_dict, registry)
                    except Exception:  # pragma: no cover
                        findings2 = []
                    errors2 = [
                        f for f in findings2
                        if getattr(f, "severity", None) == Severity.ERROR
                    ]
                    relevant2 = [
                        f for f in errors2
                        if last_step_id is None or f.step_id == last_step_id
                    ]
                    if not relevant2:
                        # Autofill resolved every missing required arg —
                        # accept the step as-is instead of rejecting.
                        return ""
                    # Partial fill — keep the tighter rejection message.
                    relevant = relevant2

        messages = "; ".join(str(finding.message) for finding in relevant[:4])
        return (
            "Next step is missing required arguments. "
            f"{messages} "
            "Emit the step again with every required argument populated "
            "(per the tool's declared parameters), or choose a different tool."
        )

    def _try_autofill_required_args(
        self,
        *,
        step: dict[str, Any],
        missing_arg_names: list[str],
    ) -> list[str]:
        """Fix #13: fill missing required args from concrete prior-step artifacts.

        Scans ``self.run['plan']['plan']`` for completed steps, gathers
        their path-valued arguments, and assigns the most recent matching
        path to each missing arg based on arg-name suffix conventions
        (``*_vcf``, ``*_bam``, ``*_fasta`` …). Generates default output
        paths under ``self.cfg.selected_dir`` for ``output_*`` args.

        Mutates ``step['arguments']`` in place. Returns the list of arg
        names actually filled.
        """

        if not isinstance(step, dict):
            return []
        args = step.get("arguments")
        if not isinstance(args, dict):
            args = {}
            step["arguments"] = args

        # Gather concrete file paths from prior completed steps.
        vcf_paths: list[str] = []
        bam_paths: list[str] = []
        fasta_paths: list[str] = []
        fastq_paths: list[str] = []
        plan = self.run.get("plan", {}) if isinstance(self.run, dict) else {}
        prior_steps = (
            plan.get("plan", []) if isinstance(plan, dict) else []
        )
        statuses = self.run.get("step_statuses", []) if isinstance(self.run, dict) else []
        completed_set = {"completed", "succeeded", "success", "done", "ok"}
        for idx, prev in enumerate(prior_steps):
            if not isinstance(prev, dict):
                continue
            status = statuses[idx] if idx < len(statuses) else ""
            if str(status).strip().lower() not in completed_set:
                continue
            prev_args = prev.get("arguments") if isinstance(prev, dict) else None
            if not isinstance(prev_args, dict):
                continue
            for value in prev_args.values():
                if isinstance(value, (list, tuple)):
                    candidates = [str(item) for item in value]
                else:
                    candidates = [str(value)]
                for sv in candidates:
                    low = sv.lower()
                    if low.endswith(".vcf") or low.endswith(".vcf.gz"):
                        vcf_paths.append(sv)
                    elif low.endswith(".bam"):
                        bam_paths.append(sv)
                    elif low.endswith((".fasta", ".fa", ".fna")):
                        fasta_paths.append(sv)
                    elif low.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz")):
                        fastq_paths.append(sv)

        # Fallback: scan selected_dir for artifacts produced at runtime.
        selected_dir: Path | None = None
        try:
            cfg = getattr(self, "cfg", None)
            sd = getattr(cfg, "selected_dir", None) if cfg is not None else None
            if sd:
                selected_dir = Path(sd)
        except Exception:  # pragma: no cover
            selected_dir = None
        if selected_dir is not None and selected_dir.exists():
            try:
                for pat in ("*.vcf.gz", "*.vcf"):
                    for p in selected_dir.rglob(pat):
                        sp = str(p)
                        if sp not in vcf_paths:
                            vcf_paths.append(sp)
                for p in selected_dir.rglob("*.bam"):
                    sp = str(p)
                    if sp not in bam_paths:
                        bam_paths.append(sp)
            except Exception:  # pragma: no cover
                pass

        filled: list[str] = []
        tool_name = str(step.get("tool_name", "") or "").strip() or "step"
        for raw_name in missing_arg_names:
            name = str(raw_name).strip()
            if not name:
                continue
            # Don't overwrite a value the LLM already supplied (even an empty
            # string — that counts as "supplied but bad" and the LLM should
            # see the rejection instead).
            if name in args and str(args.get(name) or "").strip():
                continue
            low = name.lower()
            value: str | None = None
            if "vcf" in low:
                if "output" in low or low.startswith("out_"):
                    if selected_dir is not None:
                        value = str(
                            selected_dir
                            / f"{tool_name}_{name}.vcf.gz"
                        )
                elif vcf_paths:
                    value = vcf_paths[-1]
            elif "bam" in low:
                if "output" in low or low.startswith("out_"):
                    if selected_dir is not None:
                        value = str(
                            selected_dir
                            / f"{tool_name}_{name}.bam"
                        )
                elif bam_paths:
                    value = bam_paths[-1]
            elif "fasta" in low or low.endswith("_fa") or "_fasta_" in low:
                if "output" in low or low.startswith("out_"):
                    if selected_dir is not None:
                        value = str(
                            selected_dir
                            / f"{tool_name}_{name}.fasta"
                        )
                elif fasta_paths:
                    value = fasta_paths[-1]
            elif low in ("sample_prefix", "sample_name", "sample_id"):
                # Derive a stable prefix from the newest fastq filename.
                if fastq_paths:
                    base = fastq_paths[-1].rsplit("/", 1)[-1]
                    prefix = re.sub(
                        r"[_\.](R?1|R?2|fastq|fq)(\.gz)?$",
                        "",
                        base,
                        flags=re.IGNORECASE,
                    )
                    if prefix:
                        value = prefix
            elif "output_dir" in low or low == "outdir":
                if selected_dir is not None:
                    value = str(selected_dir / f"{tool_name}_out")
            if value is not None:
                args[name] = value
                filled.append(name)
        return filled

    def _stepwise_protocol_pending_snapshot(
        self,
        validation: dict[str, Any],
    ) -> dict[str, set[str]]:
        """Return the benchmark-blind pending protocol snapshot for one plan.

        Args:
            validation: Protocol grounding validation payload.

        Returns:
            Mapping of unresolved protocol requirements grouped by type.
        """

        issues = validation.get("issues", []) if isinstance(validation.get("issues", []), list) else []
        soft_issues: set[str] = set()
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue_name = str(issue.get("issue", "") or "").strip()
            if issue_name not in _STEPWISE_PROTOCOL_SOFT_ISSUES:
                continue
            issue_key = issue_name
            if issue_name == "missing_benchmark_annotation_stage":
                issue_key = f"{issue_name}:{str(issue.get('expected_tool', '') or '').strip()}"
            elif issue_name == "insufficient_comparison_branches":
                issue_key = (
                    f"{issue_name}:"
                    f"{str(issue.get('chosen_method', '') or '').strip()}:"
                    f"{int(issue.get('expected_min', 0) or 0)}"
                )
            soft_issues.add(issue_key)
        return {
            "missing_required_tools": {
                str(tool).strip()
                for tool in (validation.get("missing_required_tools", []) or [])
                if str(tool).strip()
            },
            "missing_plan_signals": {
                str(signal).strip()
                for signal in (validation.get("missing_plan_signals", []) or [])
                if str(signal).strip()
            },
            "soft_issues": soft_issues,
        }

    def _stepwise_protocol_hard_issues(
        self,
        validation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return protocol issues that must still block a stepwise candidate.

        Args:
            validation: Protocol grounding validation payload.

        Returns:
            List of hard issues. Unknown issue types fail closed.
        """

        issues = validation.get("issues", []) if isinstance(validation.get("issues", []), list) else []
        hard_issues: list[dict[str, Any]] = []
        for issue in issues:
            if not isinstance(issue, dict):
                hard_issues.append({"issue": str(issue).strip() or "unknown_protocol_issue"})
                continue
            issue_name = str(issue.get("issue", "") or "").strip()
            if issue_name in _STEPWISE_PROTOCOL_SOFT_ISSUES:
                continue
            hard_issues.append(dict(issue))
        return hard_issues

    def _assess_stepwise_protocol_candidate(
        self,
        *,
        existing_plan: dict[str, Any],
        candidate_plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Assess protocol grounding for one stepwise candidate.

        Stepwise mode accepts prefix plans that still have future protocol work
        remaining. Missing downstream tools, signals, and other completeness
        gaps remain visible in the returned payload, but they do not block the
        next step unless the candidate introduces a hard contradiction or makes
        protocol progress worse than the already accepted prefix.

        Args:
            existing_plan: Immutable accepted prefix before the candidate.
            candidate_plan: Prefix plus one newly proposed step.

        Returns:
            Protocol validation payload for stepwise mode.
        """

        analysis_spec = self._run_analysis_spec_dict()
        previous_validation = assess_protocol_grounding(existing_plan, analysis_spec)
        current_validation = assess_protocol_grounding(candidate_plan, analysis_spec)
        previous_pending = self._stepwise_protocol_pending_snapshot(previous_validation)
        current_pending = self._stepwise_protocol_pending_snapshot(current_validation)
        hard_issues = self._stepwise_protocol_hard_issues(current_validation)
        regressed = any(
            not current_pending[key].issubset(previous_pending[key])
            for key in ("missing_required_tools", "missing_plan_signals", "soft_issues")
        )
        passed = (not hard_issues) and (not regressed)
        pending_missing_tools = sorted(current_pending["missing_required_tools"])
        pending_missing_signals = sorted(current_pending["missing_plan_signals"])
        pending_soft_issues = sorted(current_pending["soft_issues"])
        result = dict(current_validation)
        result.update(
            {
                "passed": passed,
                "complete": bool(current_validation.get("passed", False)),
                "validation_mode": "stepwise_prefix",
                "hard_issues": hard_issues,
                "regressed": regressed,
                "pending_required_tools": pending_missing_tools,
                "pending_plan_signals": pending_missing_signals,
                "pending_soft_issues": pending_soft_issues,
                "full_validation": current_validation,
            }
        )
        if passed and not bool(current_validation.get("passed", False)):
            result["reason"] = "stepwise_prefix_pending_protocol_work"
        elif hard_issues:
            result["reason"] = "stepwise_prefix_hard_protocol_issue"
        elif regressed:
            result["reason"] = "stepwise_prefix_protocol_regression"
        return result

    def _stepwise_rebind_appended_candidate_step(
        self,
        *,
        plan: dict[str, Any],
        existing_step_count: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Rebind only the newly accepted stepwise candidate to runtime paths.

        Stepwise normalization is allowed to inspect the cumulative plan, but the
        accepted prefix is immutable. Rebinding just the appended step keeps
        deterministic artifact scaffolding active after normalization without
        rewriting already executed work.
        """

        steps = plan.get("plan", [])
        if not isinstance(steps, list):
            return plan, {"changed": False, "why": "plan_missing"}
        if existing_step_count < 0 or existing_step_count >= len(steps):
            return plan, {"changed": False, "why": "no_appended_step"}
        candidate_step = steps[existing_step_count]
        if not isinstance(candidate_step, dict):
            return plan, {"changed": False, "why": "appended_step_not_mapping"}

        try:
            from bio_harness.core.strict_artifact_binding import (
                bind_step_spec_for_benchmark_policy,
            )

            rebound = bind_step_spec_for_benchmark_policy(
                step_spec=dict(candidate_step),
                workflow_step=dict(candidate_step),
                analysis_spec=self._runtime_binding_analysis_spec(),
            )
        except Exception as exc:  # pragma: no cover - defensive
            return dict(plan), {
                "changed": False,
                "why": "binding_failed",
                "exception_class": exc.__class__.__name__,
                "message": str(exc).strip(),
            }

        bound_step = rebound if isinstance(rebound, dict) else dict(candidate_step)
        bound_step = self._stepwise_rebind_direct_wrapper_candidate_step(bound_step)
        bound_step = self._stepwise_rebind_single_step_compiler_candidate(bound_step)

        if not isinstance(bound_step, dict) or bound_step == candidate_step:
            return plan, {"changed": False, "why": "already_bound"}

        rebound_steps = list(steps)
        rebound_steps[existing_step_count] = bound_step
        patched = dict(plan)
        patched["plan"] = rebound_steps
        return patched, {
            "changed": True,
            "why": "stepwise_appended_candidate_rebinding",
            "step_index": int(existing_step_count),
            "tool_name": str(candidate_step.get("tool_name", "") or ""),
        }

    def _stepwise_frontier_cell_for_tool(self, tool_name: str) -> dict[str, Any]:
        """Return the next branch-stage frontier cell for a tool, if any."""

        tool_token = str(tool_name or "").strip()
        if not tool_token:
            return {}
        try:
            from bio_harness.core.branch_stage_progress import (
                summarize_branch_stage_progress,
            )

            progress = summarize_branch_stage_progress(
                steps=self._stepwise_plan_steps(),
                statuses=list(self.run.get("step_statuses", [])),
                analysis_spec=self._runtime_binding_analysis_spec(),
            )
        except Exception:  # pragma: no cover - defensive gate enrichment
            return {}
        if (
            not isinstance(progress, dict)
            or bool(progress.get("complete", False))
            or not bool(progress.get("started", False))
        ):
            return {}
        next_cell = progress.get("next_cell")
        if (
            isinstance(next_cell, dict)
            and str(next_cell.get("suggested_tool", "") or "").strip() == tool_token
        ):
            return dict(next_cell)
        frontier = progress.get("frontier")
        if not isinstance(frontier, list):
            return {}
        for item in frontier:
            if (
                isinstance(item, dict)
                and str(item.get("suggested_tool", "") or "").strip() == tool_token
            ):
                return dict(item)
        return {}

    def _stepwise_rebind_candidate_step_for_gate(
        self,
        candidate_step: dict[str, Any],
    ) -> dict[str, Any]:
        """Return an early-bound candidate for duplicate/frontier gates."""

        if not isinstance(candidate_step, dict) or not candidate_step:
            return {}
        tool_name = str(candidate_step.get("tool_name", "") or "").strip()
        step = deepcopy(candidate_step)
        cell = self._stepwise_frontier_cell_for_tool(tool_name)
        if cell:
            should_rebind_to_frontier = False
            try:
                from bio_harness.core.branch_stage_progress import (
                    assess_candidate_branch_stage_frontier,
                    branch_stage_cell_for_step,
                )

                assessment = assess_candidate_branch_stage_frontier(
                    steps=self._stepwise_plan_steps(),
                    statuses=list(self.run.get("step_statuses", [])),
                    analysis_spec=self._runtime_binding_analysis_spec(),
                    candidate_step=step,
                )
                should_rebind_to_frontier = (
                    branch_stage_cell_for_step(step) is None
                    or not bool(assessment.get("passed", True))
                )
            except Exception:  # pragma: no cover - defensive gate enrichment
                should_rebind_to_frontier = not str(step.get("branch_id", "") or "").strip()
            if should_rebind_to_frontier:
                branch_id = str(cell.get("branch_id", "") or "").strip()
                stage = str(cell.get("stage", "") or "").strip()
                if branch_id:
                    step["branch_id"] = branch_id
                if stage:
                    step["objective"] = f"Complete {stage} for branch {branch_id}."

        analysis_spec = self._runtime_binding_analysis_spec()
        skeleton_entry = _stepwise_next_unsatisfied_plan_skeleton_entry(
            analysis_spec=analysis_spec,
            steps=self._stepwise_plan_steps(),
            statuses=list(self.run.get("step_statuses", [])),
        )
        if (
            str(skeleton_entry.get("tool_name", "") or "").strip().lower()
            == tool_name.strip().lower()
        ):
            if not str(step.get("objective", "") or "").strip():
                step["objective"] = str(skeleton_entry.get("objective", "") or "").strip()
            if not isinstance(step.get("parameter_hints"), dict):
                hints = skeleton_entry.get("parameter_hints", {})
                if isinstance(hints, dict) and hints:
                    step["parameter_hints"] = dict(hints)

        try:
            from bio_harness.core.strict_artifact_binding import (
                bind_step_spec_for_benchmark_policy,
            )

            rebound = bind_step_spec_for_benchmark_policy(
                step_spec=dict(step),
                workflow_step=dict(step),
                analysis_spec=analysis_spec,
            )
        except Exception:  # pragma: no cover - defensive gate enrichment
            return step
        bound_step = rebound if isinstance(rebound, dict) else step
        bound_step = self._stepwise_rebind_direct_wrapper_candidate_step(bound_step)
        return self._stepwise_rebind_single_step_compiler_candidate(bound_step)

    def _stepwise_rebind_single_step_compiler_candidate(
        self,
        candidate_step: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill one direct-wrapper candidate from a same-tool compiler output."""

        if not isinstance(candidate_step, dict) or not candidate_step:
            return {}
        tool_name = str(candidate_step.get("tool_name", "") or "").strip().lower()
        if not tool_name:
            return dict(candidate_step)
        analysis_spec = self._runtime_binding_analysis_spec()
        if not self._stepwise_candidate_tool_is_analysis_scoped(
            tool_name=tool_name,
            analysis_spec=analysis_spec,
        ):
            return dict(candidate_step)

        arguments = candidate_step.get("arguments", {})
        candidate_args = dict(arguments) if isinstance(arguments, dict) else {}
        try:
            from bio_harness.core.direct_wrapper_argument_utils import _argument_missing
            from bio_harness.core.protocol_grounding import deterministic_protocol_repair
            from bio_harness.core.tool_registry import default_tool_registry

            registry = default_tool_registry()
            required_names = [
                str(name).strip()
                for name in registry.required_parameters_for(tool_name)
                if str(name).strip()
            ]
        except Exception:  # pragma: no cover - defensive gate enrichment
            return dict(candidate_step)

        missing_required = [
            name for name in required_names if _argument_missing(candidate_args.get(name))
        ]
        if candidate_args and not missing_required:
            return dict(candidate_step)

        try:
            repaired_plan, repair_meta = deterministic_protocol_repair(
                {"thought_process": "", "plan": [dict(candidate_step)]},
                analysis_spec=analysis_spec,
                selected_dir=Path(self.cfg.selected_dir),
                data_root=Path(self.cfg.data_root),
            )
        except Exception:  # pragma: no cover - defensive gate enrichment
            return dict(candidate_step)
        if not bool(repair_meta.get("changed", False)):
            return dict(candidate_step)
        repaired_steps = (
            repaired_plan.get("plan", [])
            if isinstance(repaired_plan.get("plan", []), list)
            else []
        )
        if len(repaired_steps) != 1 or not isinstance(repaired_steps[0], dict):
            return dict(candidate_step)
        repaired_step = dict(repaired_steps[0])
        repaired_tool = str(repaired_step.get("tool_name", "") or "").strip().lower()
        if repaired_tool != tool_name:
            return dict(candidate_step)
        repaired_args_raw = repaired_step.get("arguments", {})
        if not isinstance(repaired_args_raw, dict) or not repaired_args_raw:
            return dict(candidate_step)
        repaired_args = dict(repaired_args_raw)
        if missing_required and not any(
            not _argument_missing(repaired_args.get(name)) for name in missing_required
        ):
            return dict(candidate_step)

        merged_args = dict(repaired_args)
        for key, value in candidate_args.items():
            if not _argument_missing(value):
                merged_args[key] = value
        rebound = dict(repaired_step)
        rebound.update({key: value for key, value in candidate_step.items() if key != "arguments"})
        rebound["arguments"] = merged_args
        return rebound

    def _stepwise_rebind_direct_wrapper_candidate_step(
        self,
        candidate_step: dict[str, Any],
    ) -> dict[str, Any]:
        """Return a candidate step with deterministic direct-wrapper bindings."""

        if not isinstance(candidate_step, dict) or not candidate_step:
            return {}
        tool_name = str(candidate_step.get("tool_name", "") or "").strip().lower()
        if not tool_name:
            return dict(candidate_step)
        try:
            from bio_harness.core.direct_wrapper_completeness import (
                repair_direct_wrapper_plan_bindings,
            )

            analysis_spec = self._stepwise_analysis_spec_for_candidate_tool(tool_name)
            repaired_plan, meta = repair_direct_wrapper_plan_bindings(
                {"thought_process": "", "plan": [dict(candidate_step)]},
                analysis_spec=analysis_spec,
                contract=self.run.get("plan_contract", {})
                if isinstance(self.run.get("plan_contract", {}), dict)
                else {},
                request_text=str(
                    self.run.get("user_request", "")
                    or getattr(self.cfg, "prompt", "")
                    or ""
                ),
                selected_dir=str(self.cfg.selected_dir),
                data_root=str(self.cfg.data_root),
            )
        except Exception:  # pragma: no cover - defensive gate enrichment
            return dict(candidate_step)
        if not bool(meta.get("changed", False)):
            return dict(candidate_step)
        repaired_steps = (
            repaired_plan.get("plan", [])
            if isinstance(repaired_plan.get("plan", []), list)
            else []
        )
        if repaired_steps and isinstance(repaired_steps[0], dict):
            return dict(repaired_steps[0])
        return dict(candidate_step)

    def _stepwise_analysis_spec_for_candidate_tool(
        self,
        tool_name: str,
    ) -> dict[str, Any]:
        """Return runtime analysis spec with the scoped candidate tool enabled."""

        analysis_spec = dict(self._runtime_binding_analysis_spec())
        tool_token = str(tool_name or "").strip().lower()
        if not tool_token or tool_token == "bash_run":
            return analysis_spec
        if not self._stepwise_candidate_tool_is_analysis_scoped(
            tool_name=tool_token,
            analysis_spec=analysis_spec,
        ):
            return analysis_spec
        try:
            from bio_harness.core.wrapper_contracts import wrapper_has_contract
        except Exception:  # pragma: no cover - defensive import guard
            wrapper_has_contract = lambda _name: False  # type: ignore[assignment]
        if not wrapper_has_contract(tool_token):
            return analysis_spec
        execution_contract = (
            dict(analysis_spec.get("execution_contract", {}))
            if isinstance(analysis_spec.get("execution_contract", {}), dict)
            else {}
        )
        compatible = {
            str(item).strip().lower()
            for item in (execution_contract.get("compatible_tools", []) or [])
            if str(item).strip()
        }
        compatible.add(tool_token)
        execution_contract["execution_mode"] = "direct_wrapper"
        execution_contract["compatible_tools"] = sorted(compatible)
        analysis_spec["execution_contract"] = execution_contract
        return analysis_spec

    def _stepwise_candidate_tool_is_analysis_scoped(
        self,
        *,
        tool_name: str,
        analysis_spec: dict[str, Any],
    ) -> bool:
        """Return whether a direct-wrapper candidate belongs to this analysis."""

        tool_token = str(tool_name or "").strip().lower()
        if not tool_token:
            return False
        scoped_tools: set[str] = set()
        for key in ("preferred_tools", "candidate_methods"):
            scoped_tools.update(
                str(item).strip().lower()
                for item in (analysis_spec.get(key, []) or [])
                if str(item).strip()
            )
        for mapping_key in ("execution_contract", "explicit_execution_intent", "protocol_grounding"):
            mapping = analysis_spec.get(mapping_key, {})
            if not isinstance(mapping, dict):
                continue
            for key in ("compatible_tools", "required_tools", "locked_tools", "requested_tools"):
                scoped_tools.update(
                    str(item).strip().lower()
                    for item in (mapping.get(key, []) or [])
                    if str(item).strip()
                )
        for entry in analysis_spec.get("plan_skeleton", []) or []:
            if isinstance(entry, (list, tuple)) and entry:
                scoped_tools.add(str(entry[0]).strip().lower())
        for entry in analysis_spec.get("graph_pipeline_skeleton", []) or []:
            if isinstance(entry, dict):
                scoped_tools.add(str(entry.get("tool_name", "") or "").strip().lower())
        if self._stepwise_structural_variant_raw_read_inputs(analysis_spec):
            scoped_tools.add("minimap2_align")
        return tool_token in scoped_tools

    def _stepwise_workflow_seed_tool_rejection_reason(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> str:
        """Return a rejection when a compiled-pipeline candidate ignores the next seed step."""

        if not isinstance(candidate_step, dict):
            return ""
        tool_name = str(candidate_step.get("tool_name", "") or "").strip().lower()
        if not tool_name:
            return ""
        analysis_spec = self._runtime_binding_analysis_spec()
        if not _stepwise_analysis_spec_is_compiled_pipeline(analysis_spec):
            return ""
        if self._stepwise_candidate_advances_branch_frontier(
            candidate_step=candidate_step,
        ):
            return ""
        expected_entry = _stepwise_next_unsatisfied_plan_skeleton_entry(
            analysis_spec=analysis_spec,
            steps=self._stepwise_plan_steps(),
            statuses=list(self.run.get("step_statuses", [])),
        )
        expected_tool = str(expected_entry.get("tool_name", "") or "").strip().lower()
        if not expected_tool:
            return ""
        allowed_tools = {expected_tool}
        allowed_tools.update(_stepwise_graph_pipeline_tools(analysis_spec))
        if self._stepwise_structural_variant_raw_read_inputs(analysis_spec):
            allowed_tools.add("minimap2_align")
        if tool_name in allowed_tools:
            return ""
        allowed = ", ".join(f"`{item}`" for item in sorted(allowed_tools))
        return (
            "Candidate is outside the workflow seed for this compiled pipeline. "
            f"Expected next workflow seed tool is `{expected_tool}`. "
            f"Emit one of the allowed next tools first: {allowed}."
        )

    def _stepwise_structural_variant_raw_read_inputs(
        self,
        analysis_spec: dict[str, Any],
    ) -> bool:
        """Return whether a structural-variant case has raw reads to align."""

        analysis_tokens = {
            str(analysis_spec.get("analysis_type", "") or "").strip().lower(),
            str(analysis_spec.get("analysis_family", "") or "").strip().lower(),
        }
        protocol = (
            analysis_spec.get("protocol_grounding", {})
            if isinstance(analysis_spec.get("protocol_grounding", {}), dict)
            else {}
        )
        analysis_tokens.add(str(protocol.get("analysis_type", "") or "").strip().lower())
        analysis_tokens.add(str(protocol.get("analysis_family", "") or "").strip().lower())
        if "structural_variant_calling" not in analysis_tokens:
            return False
        paths: list[str] = []
        for entry in analysis_spec.get("discovered_data_files", []) or []:
            if isinstance(entry, dict):
                paths.append(str(entry.get("path", "") or ""))
                paths.append(str(entry.get("name", "") or ""))
        manifest = (
            analysis_spec.get("file_manifest", {})
            if isinstance(analysis_spec.get("file_manifest", {}), dict)
            else {}
        )
        for entry in manifest.get("entries", []) or []:
            if isinstance(entry, dict):
                paths.append(str(entry.get("resolved_path", "") or ""))
                paths.append(str(entry.get("file_type", "") or ""))
                paths.append(str(entry.get("role", "") or ""))
        rendered = "\n".join(paths).lower()
        has_reads = any(token in rendered for token in (".fastq", ".fq", "fastq"))
        has_reference = any(token in rendered for token in (".fasta", ".fna", ".fa", "fasta"))
        return has_reads and has_reference

    def _stepwise_direct_wrapper_input_role_rejection_reason(
        self,
        *,
        candidate_step: dict[str, Any],
    ) -> str:
        """Return a rejection when a direct-wrapper input has the wrong role."""

        if not isinstance(candidate_step, dict):
            return ""
        tool_name = str(candidate_step.get("tool_name", "") or "").strip().lower()
        if not tool_name:
            return ""
        try:
            from bio_harness.core.direct_wrapper_argument_utils import _argument_missing
            from bio_harness.core.direct_wrapper_input_bindings import (
                _DIRECT_WRAPPER_INPUT_HINTS,
            )
            from bio_harness.core.wrapper_contracts import wrapper_has_contract
        except Exception:  # pragma: no cover - defensive guard enrichment
            return ""
        if not wrapper_has_contract(tool_name):
            return ""
        if not self._stepwise_candidate_tool_is_analysis_scoped(
            tool_name=tool_name,
            analysis_spec=self._runtime_binding_analysis_spec(),
        ):
            return ""
        expected_suffixes = _DIRECT_WRAPPER_INPUT_HINTS.get(tool_name, {})
        if not expected_suffixes:
            return ""
        args = candidate_step.get("arguments", {})
        if not isinstance(args, dict):
            return ""
        issues: list[str] = []
        for param_name, suffixes in sorted(expected_suffixes.items()):
            value = args.get(param_name)
            if _argument_missing(value):
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                path_text = str(item or "").strip()
                if not path_text:
                    continue
                if _value_matches_any_suffix(path_text, suffixes):
                    continue
                issues.append(
                    f"{tool_name}.{param_name}={path_text} does not match expected "
                    f"input role suffixes {', '.join(suffixes)}"
                )
        if not issues:
            return ""
        hint = ""
        if tool_name == "sniffles_sv_call" and any("input_bam" in issue for issue in issues):
            hint = (
                " Raw FASTQ inputs must be aligned first, for example with "
                "minimap2_align, before Sniffles."
            )
        return (
            "Candidate direct-wrapper inputs have incompatible file roles: "
            + "; ".join(issues[:3])
            + hint
        )

    def _evaluate_stepwise_candidate(
        self,
        *,
        contract: dict[str, Any],
        candidate: dict[str, Any],
    ) -> tuple[bool, dict[str, Any], str]:
        """Validate one next-step candidate against the current executed prefix.

        The stepwise validator is intentionally stricter than the batch
        preexecution path. It may normalize and inspect the cumulative plan, but
        it does not run deterministic plan-repair passes that would rewrite the
        already executed prefix.

        Args:
            contract: Request contract for the current run.
            candidate: One-step planner response.

        Returns:
            Tuple of ``(accepted, accepted_payload, rejection_reason)``.
        """

        existing_steps = self._stepwise_plan_steps()
        existing_plan = deepcopy(self._run_plan_dict())
        original_run = deepcopy(self.run)
        original_append_event = self._append_event
        accepted_payload: dict[str, Any] = {}
        rejection_reason = ""

        # Generalized livelock guard: a slow LLM planner sometimes re-proposes a
        # step that was already executed successfully with identical arguments.
        # Re-running it wastes compute and blocks forward progress across the
        # remaining workflow branches. The accepted prefix is immutable, so the
        # duplicate check runs against the current candidate step alone, before
        # the expensive normalization / protocol / semantic validation passes.
        candidate_steps_for_dupe_check = (
            candidate.get("plan", []) if isinstance(candidate.get("plan", []), list) else []
        )
        candidate_step_for_dupe_check = (
            dict(candidate_steps_for_dupe_check[0])
            if candidate_steps_for_dupe_check and isinstance(candidate_steps_for_dupe_check[0], dict)
            else {}
        )
        candidate_step_for_dupe_check = self._stepwise_rebind_candidate_step_for_gate(
            candidate_step_for_dupe_check
        )
        if candidate_step_for_dupe_check:
            candidate = dict(candidate)
            candidate["plan"] = [candidate_step_for_dupe_check]
        cumulative = self._stepwise_candidate_with_history(candidate)
        duplicate_prior = self._stepwise_duplicate_completed_step(
            candidate_step=candidate_step_for_dupe_check,
        )
        if duplicate_prior:
            hint = self._stepwise_pending_work_hint()
            rejection_reason = (
                f"Candidate duplicates completed step_id={duplicate_prior.get('step_id')} "
                f"(tool_name={duplicate_prior.get('tool_name')}, status={duplicate_prior.get('status')}). "
                "That step already finished successfully with identical arguments. "
                "Emit a different next step that advances the workflow (e.g. the next "
                "branch, sample, or downstream stage) instead of re-running work that "
                "is already complete."
                + (f" {hint}" if hint else "")
            )
            return False, {}, rejection_reason

        seed_tool_rejection = self._stepwise_workflow_seed_tool_rejection_reason(
            candidate_step=candidate_step_for_dupe_check,
        )
        if seed_tool_rejection:
            return False, {}, seed_tool_rejection

        branch_stage_rejection = self._stepwise_branch_stage_rejection_reason(
            candidate_step=candidate_step_for_dupe_check,
        )
        if branch_stage_rejection:
            return False, {}, branch_stage_rejection

        input_role_rejection = self._stepwise_direct_wrapper_input_role_rejection_reason(
            candidate_step=candidate_step_for_dupe_check,
        )
        if input_role_rejection:
            return False, {}, input_role_rejection

        # Fix #22b-pre: before the missing-inputs (disk-existence) guard,
        # run the schema-level required-argument check on the candidate.
        # If the candidate is missing a required argument key entirely, the
        # tighter "missing required arguments" rejection is more actionable
        # for the planner than the generic "input file not on disk" message
        # that the missing-inputs guard would otherwise emit. Running the
        # required-args check first also gives autofill a chance to
        # populate missing keys from prior completed outputs, so the
        # missing-inputs check then reasons about the autofilled paths.
        early_arg_rejection = self._stepwise_required_arg_rejection_reason(
            plan=cumulative,
        )
        if early_arg_rejection:
            return False, {}, early_arg_rejection

        # Fix #22b: generalized branch-completeness guard. When the candidate
        # declares input paths that neither exist on disk nor are scheduled
        # as outputs of the accepted prefix, reject with a directive that
        # names the missing files. Running this BEFORE normalization
        # prevents the "prefix mutation" rejection path that the normalizer
        # triggers when it auto-inserts the missing producers (observed in
        # exp37 where ``shared_variants_export_run`` was emitted before the
        # evol2 BWA → freebayes → filter chain had run). The check is tool-
        # agnostic: it reads input/output key declarations from the tool
        # registry and applies to every analysis type whose binder produces
        # canonical input paths.
        missing_inputs = self._stepwise_missing_candidate_inputs(
            candidate_step=candidate_step_for_dupe_check,
        )
        if missing_inputs:
            sample = ", ".join(missing_inputs[:3])
            if len(missing_inputs) > 3:
                sample += f", ... (+{len(missing_inputs) - 3} more)"
            hint = self._stepwise_pending_work_hint()
            rejection_reason = (
                "Candidate step references inputs that are not available yet — "
                "they do not exist on disk and are not scheduled as outputs of any "
                f"prior step. Missing: {sample}. Emit the step(s) that produce "
                "these files first (e.g. the per-sample alignment / variant-call "
                "chain for the missing branch) before running this downstream step."
                + (f" {hint}" if hint else "")
            )
            return False, {}, rejection_reason

        try:
            self._append_event = lambda **_kwargs: None  # type: ignore[assignment]
            previous_freeze_completed_prefix = bool(
                getattr(self, "_stepwise_freeze_completed_prefix", False)
            )
            self._stepwise_freeze_completed_prefix = True
            try:
                normalized_plan, canonical_meta, fc_meta = self._normalize_plan_for_execution(
                    cumulative
                )
            finally:
                self._stepwise_freeze_completed_prefix = previous_freeze_completed_prefix
            if self._stepwise_prefix_substantively_changed(
                candidate_plan=normalized_plan,
                existing_step_count=len(existing_steps),
            ):
                rejection_reason = (
                    "Candidate mutated the already executed plan prefix. "
                    "Emit only the next step without rewriting prior steps."
                )
                return False, {}, rejection_reason
            normalized_plan = self._stepwise_restore_executed_prefix(
                candidate_plan=normalized_plan,
                existing_step_count=len(existing_steps),
            )
            if self._stepwise_prefix_changed(
                candidate_plan=normalized_plan,
                existing_step_count=len(existing_steps),
            ):
                rejection_reason = (
                    "Candidate mutated the already executed plan prefix. "
                    "Emit only the next step without rewriting prior steps."
                )
                return False, {}, rejection_reason

            normalized_plan, candidate_binding_meta = self._stepwise_rebind_appended_candidate_step(
                plan=normalized_plan,
                existing_step_count=len(existing_steps),
            )
            if candidate_binding_meta.get("changed", False):
                canonical_meta = dict(canonical_meta or {})
                canonical_meta["stepwise_appended_candidate_binding"] = candidate_binding_meta

            self.run["plan"] = normalized_plan
            self.run["protocol_validation"] = self._assess_stepwise_protocol_candidate(
                existing_plan=existing_plan,
                candidate_plan=self._run_plan_dict(),
            )
            if not bool(self.run["protocol_validation"].get("passed", False)):
                rejection_reason = (
                    "Next step failed protocol grounding: "
                    + _json_dumps_safe(self.run["protocol_validation"], indent=2)
                )
                return False, {}, rejection_reason

            resolved_plan, semantic_validation, placeholder_sidecar = (
                assess_plan_semantic_guards_with_bash_placeholders(
                    plan=self._run_plan_dict(),
                    assess_semantic_guards=lambda cumulative_plan: _assess_plan_semantic_guards(
                        cumulative_plan,
                        analysis_spec=self._run_analysis_spec_dict(),
                        cwd=self.cfg.selected_dir,
                    ),
                    path_graph=self.path_graph,
                    selected_dir=str(self.cfg.selected_dir),
                )
            )
            self.run["plan"] = resolved_plan
            self.run["semantic_validation"] = semantic_validation
            self.run["bash_placeholder_resolutions"] = placeholder_sidecar
            if not bool(semantic_validation.get("passed", False)):
                rejection_reason = (
                    "Next step failed semantic validation: "
                    + _json_dumps_safe(semantic_validation, indent=2)
                )
                return False, {}, rejection_reason

            contract_progress = self._assess_contract_for_plan(self._run_plan_dict(), contract)
            self.run["contract_validation"] = contract_progress
            hard_issues = self._stepwise_contract_hard_issues(contract_progress)
            if hard_issues:
                rejection_reason = (
                    "Next step introduced non-progress contract issues: "
                    + _json_dumps_safe(hard_issues, indent=2)
                )
                return False, {}, rejection_reason

            missing_tools = _missing_exec_tools_for_plan(self._run_plan_dict())
            if missing_tools:
                rejection_reason = (
                    "Next step referenced unavailable executable tools: "
                    + ", ".join(missing_tools[:8])
                )
                return False, {}, rejection_reason

            missing_inputs = self._filter_missing_plan_inputs(
                _missing_input_paths_for_plan(
                    self._run_plan_dict(),
                    self.cfg.selected_dir,
                    self.cfg.data_root,
                )
            )
            if missing_inputs:
                rejection_reason = (
                    "Next step referenced missing inputs: "
                    + ", ".join(missing_inputs[:8])
                )
                return False, {}, rejection_reason

            missing_scripts = _missing_local_scripts_for_plan(
                self._run_plan_dict(),
                self.cfg.selected_dir,
            )
            if missing_scripts:
                rejection_reason = (
                    "Next step referenced missing local scripts: "
                    + ", ".join(missing_scripts[:8])
                )
                return False, {}, rejection_reason

            # Mirror the executor's pre-execution required-argument check here
            # so the attempt loop can feed the specific missing argument back
            # into the next attempt's `retry_reason`. Without this, a
            # missing-required-argument error was only caught at executor
            # preflight after the turn had already been accepted, so the
            # planner advanced to a new turn only to re-propose the same
            # broken call (observed in exp15: prokka_annotate without
            # ``sample_prefix`` emitted in four consecutive turns).
            arg_rejection = self._stepwise_required_arg_rejection_reason(
                plan=self._run_plan_dict(),
            )
            if arg_rejection:
                rejection_reason = arg_rejection
                return False, {}, rejection_reason

            accepted_payload = {
                "plan": deepcopy(self._run_plan_dict()),
                "contract_validation": deepcopy(self.run["contract_validation"]),
                "protocol_validation": deepcopy(self.run["protocol_validation"]),
                "semantic_validation": deepcopy(self.run["semantic_validation"]),
                "bash_placeholder_resolutions": deepcopy(self.run["bash_placeholder_resolutions"]),
                "canonicalization": canonical_meta,
                "featurecounts_normalization": fc_meta,
            }
            return True, accepted_payload, ""
        finally:
            self.run = original_run
            self._append_event = original_append_event  # type: ignore[assignment]

    def _adopt_stepwise_candidate(
        self,
        *,
        accepted_payload: dict[str, Any],
    ) -> None:
        """Install one accepted next step while preserving history."""

        existing_statuses = list(self.run.get("step_statuses", []))
        self.run["plan"] = deepcopy(accepted_payload.get("plan", {}))
        self.run["contract_validation"] = deepcopy(accepted_payload.get("contract_validation", {}))
        self.run["protocol_validation"] = deepcopy(accepted_payload.get("protocol_validation", {}))
        self.run["semantic_validation"] = deepcopy(accepted_payload.get("semantic_validation", {}))
        self.run["bash_placeholder_resolutions"] = deepcopy(
            accepted_payload.get("bash_placeholder_resolutions", [])
        )
        self.run["step_statuses"] = existing_statuses + ["pending"]
        self.run["next_step_idx"] = len(existing_statuses)
        self.run["status"] = "planned"
        self.run["error"] = ""

    def _try_stepwise_pending_candidate_steps(
        self,
        *,
        contract: dict[str, Any],
        contract_progress: dict[str, Any],
        turn_num: int,
        attempt_rows: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        """Validate cached tail steps before asking the planner for new work."""

        pending_steps = self._stepwise_sanitize_pending_candidate_steps(
            self.run.get("stepwise_pending_candidate_steps", [])
        )
        if not pending_steps:
            self.run["stepwise_pending_candidate_steps"] = []
            return None, ""

        retry_reason = ""
        for pending_index, pending_step in enumerate(pending_steps, start=1):
            remaining_steps = pending_steps[pending_index:]
            candidate_plan = {
                "thought_process": (
                    "Continue a previously accepted stepwise multi-step candidate."
                ),
                "plan": [pending_step],
            }
            accepted, accepted_payload, rejection_reason = self._evaluate_stepwise_candidate(
                contract=contract,
                candidate=candidate_plan,
            )
            strategy = f"stepwise_turn_{turn_num}_pending_tail_{pending_index}"
            attempt_row = {
                "attempt": 0,
                "pending_index": int(pending_index),
                "strategy": strategy,
                "status": "accepted" if accepted else "pending_tail_rejected",
                "source": "pending_candidate_tail",
                "elapsed_seconds": 0.0,
                "tool_name": str(pending_step.get("tool_name", "") or ""),
                "reason": rejection_reason,
            }
            if not accepted:
                attempt_row["rejection_record"] = self._record_stepwise_candidate_rejection(
                    candidate_plan=candidate_plan,
                    rejection_reason=rejection_reason,
                    turn_num=turn_num,
                    attempt_num=0,
                    strategy=strategy,
                    source="pending_candidate_tail",
                )
            attempt_rows.append(attempt_row)
            if accepted:
                return (
                    {
                        "status": "step",
                        "attempts": attempt_rows,
                        "contract_progress": contract_progress,
                        "accepted_payload": accepted_payload,
                        "candidate_plan": candidate_plan,
                        "pending_candidate_steps": remaining_steps,
                    },
                    "",
                )
            retry_reason = rejection_reason

        self.run["stepwise_pending_candidate_steps"] = []
        return None, retry_reason

    def _plan_next_step_turn(
        self,
        *,
        contract: dict[str, Any],
        turn_num: int,
    ) -> dict[str, Any]:
        """Plan and validate one next-step decision for the current turn."""

        contract_progress = self._assess_contract_for_plan(self._run_plan_dict(), contract)
        attempt_rows: list[dict[str, Any]] = []
        pending_decision, retry_reason = self._try_stepwise_pending_candidate_steps(
            contract=contract,
            contract_progress=contract_progress,
            turn_num=turn_num,
            attempt_rows=attempt_rows,
        )
        if pending_decision is not None:
            return pending_decision
        # Progressive tool masking: when the LLM duplicate-rejects a completed
        # tool, drop that tool from both the recommended and available skill
        # lists on subsequent attempts and surface a hard "Forbidden" directive
        # in the prompt. Reuses the existing duplicate detector as the signal;
        # no per-skill argument synthesis required. General across analyses.
        excluded_tool_names: set[str] = set()

        for attempt_num in range(1, self._stepwise_planner_attempts_per_turn() + 1):
            allowed_tool_names = self._stepwise_current_allowed_tool_names(
                contract_progress=contract_progress,
            )
            prompt_body = self._stepwise_prompt_body(
                contract=contract,
                contract_progress=contract_progress,
                turn_num=turn_num,
                retry_reason=retry_reason,
                excluded_tool_names=excluded_tool_names,
                allowed_tool_names=allowed_tool_names,
            )
            selected_skills, selection_meta, available_skills = self._stepwise_selected_planner_skills(
                selection_query=prompt_body,
                excluded_tool_names=excluded_tool_names,
                allowed_tool_names=allowed_tool_names,
            )
            prompt = self._stepwise_prompt(
                contract=contract,
                contract_progress=contract_progress,
                turn_num=turn_num,
                retry_reason=retry_reason,
                recommended_skills=selected_skills,
                available_skills=available_skills,
                excluded_tool_names=excluded_tool_names,
                allowed_tool_names=allowed_tool_names,
            )
            strategy = f"stepwise_turn_{turn_num}_attempt_{attempt_num}"
            try:
                candidate_plan, elapsed = self._planner_attempt_with_heartbeat(
                    prompt=prompt,
                    strategy=strategy,
                    attempt_num=attempt_num,
                    planner_mode="auto",
                    seed_plan=self._run_plan_dict(),
                    available_skills_metadata_override=selected_skills,
                )
            except Exception as exc:
                retry_reason = str(exc)
                attempt_rows.append(
                    {
                        "attempt": int(attempt_num),
                        "strategy": strategy,
                        "status": "failed",
                        "error": retry_reason,
                        "recommended_tool_names": list(selection_meta.get("selected_skill_names", [])),
                    }
                )
                continue

            steps = candidate_plan.get("plan", []) if isinstance(candidate_plan.get("plan", []), list) else []
            if len(steps) == 0:
                unsupported_top_level = [
                    key
                    for key in ("plan_outline", "workflow", "final_deliverables")
                    if candidate_plan.get(key)
                ]
                retry_reason = (
                    "Received an empty `plan`. Return exactly one executable next step. "
                    "Use an empty plan only when both contract progress and protocol grounding are already passed."
                )
                if unsupported_top_level:
                    retry_reason += (
                        " Unsupported top-level keys were present: "
                        + ", ".join(unsupported_top_level)
                        + "."
                    )
                attempt_rows.append(
                    {
                        "attempt": int(attempt_num),
                        "strategy": strategy,
                        "status": "invalid_shape",
                        "elapsed_seconds": round(float(elapsed), 3),
                        "reason": retry_reason,
                        "recommended_tool_names": list(selection_meta.get("selected_skill_names", [])),
                    }
                )
                continue
            pending_candidate_steps: list[dict[str, Any]] = []
            if len(steps) > 1:
                # The LLM sometimes emits a multi-step plan despite the
                # stepwise prompt asking for exactly one next step. Rather
                # than reject the whole plan and risk a livelock, truncate
                # to a single step. Previously we always took ``steps[0]``,
                # but the LLM will re-emit its original full plan (e.g. 13
                # steps starting from assembly) on every turn, so ``steps[0]``
                # is almost always the same completed step — duplicate
                # detection then rejects it, the tool gets masked, and the
                # LLM still returns the same first step, making the turn
                # fail with no forward progress.
                #
                # Fix: walk the returned steps and skip any whose signature
                # matches a step in the already-completed prefix. The first
                # step whose signature is NOT a completed duplicate is the
                # LLM's intended "next" action. Fall back to ``steps[0]``
                # only when every returned step duplicates completed work,
                # so duplicate detection can still surface the problem.
                completed_signatures: set[str] = set()
                completed_io_signatures: set[str] = set()
                completed_input_signatures: set[str] = set()
                completed_resolved_out_signatures: set[str] = set()
                # Fix #14d: also collect the set of completed tool names so
                # bare-args candidates (``{step_id, tool_name}`` without any
                # arguments field) can be rejected against any completed
                # prior call of the same tool.
                completed_tool_names: set[str] = set()
                for prior_index, prior_step in enumerate(self._stepwise_plan_steps()):
                    statuses = self.run.get("step_statuses", [])
                    if not isinstance(statuses, list) or prior_index >= len(statuses):
                        continue
                    if str(statuses[prior_index]).strip().lower() not in (
                        "succeeded",
                        "success",
                        "completed",
                        "ok",
                        "done",
                    ):
                        continue
                    sig = self._stepwise_step_signature(prior_step)
                    if sig:
                        completed_signatures.add(sig)
                    # Fix #14: also track primary-I/O signatures so the
                    # truncation selector skips a candidate whose I/O paths
                    # already match a completed step, even when its
                    # argument set differs in label/tuning keys like
                    # ``sample_name``.
                    io_sig = self._stepwise_primary_io_signature(prior_step)
                    if io_sig:
                        completed_io_signatures.add(io_sig)
                    # Fix #14b: also track input-only signatures so the
                    # truncation selector skips candidates that re-do the
                    # same work with a different reference copy or output
                    # path. The input path IS the sample identity.
                    input_sig = self._stepwise_input_only_signature(prior_step)
                    if input_sig:
                        completed_input_signatures.add(input_sig)
                    # Fix #14c: also track resolved-output signatures so
                    # the truncation selector skips candidates that would
                    # write to an already-produced artifact under an
                    # aliased filename (e.g. ``ancestor_aligned.bam`` vs
                    # ``anc_aligned.bam`` in the same parent dir).
                    resolved_out_sig = self._stepwise_resolved_output_signature(prior_step)
                    if resolved_out_sig:
                        completed_resolved_out_signatures.add(resolved_out_sig)
                    prior_tool_name_str = str(prior_step.get("tool_name", "") or "").strip()
                    if prior_tool_name_str:
                        completed_tool_names.add(prior_tool_name_str)
                chosen_index = 0
                if (
                    completed_signatures
                    or completed_io_signatures
                    or completed_input_signatures
                    or completed_resolved_out_signatures
                    or completed_tool_names
                    or excluded_tool_names
                ):
                    # Walk the LLM's plan and pick the first step that is
                    # neither (a) a duplicate of a completed step nor (b)
                    # a use of a tool that was masked earlier in this turn
                    # (e.g. bash_run after a ``duplicate_equivalent_step``
                    # rejection). Without the ``excluded_tool_names``
                    # check, Fix #8 would still select the first masked
                    # tool from the LLM's multi-step response and the
                    # semantic validator would reject it on the same
                    # grounds that masked it — livelock.
                    for candidate_index, candidate_step_item in enumerate(steps):
                        if not isinstance(candidate_step_item, dict):
                            continue
                        candidate_tool = str(
                            candidate_step_item.get("tool_name", "") or ""
                        ).strip()
                        if candidate_tool and candidate_tool in excluded_tool_names:
                            continue
                        candidate_sig = self._stepwise_step_signature(candidate_step_item)
                        if candidate_sig and candidate_sig in completed_signatures:
                            continue
                        # Fix #14: also skip when the primary-I/O signature
                        # matches a completed step. This catches LLM
                        # re-submissions that drop label args like
                        # ``sample_name`` between turns but align the same
                        # reads to the same reference.
                        candidate_io_sig = self._stepwise_primary_io_signature(candidate_step_item)
                        if candidate_io_sig and candidate_io_sig in completed_io_signatures:
                            continue
                        # Fix #14b: skip when the input-only signature
                        # matches a completed step. Same tool on the same
                        # input file is the same work.
                        candidate_input_sig = self._stepwise_input_only_signature(candidate_step_item)
                        if candidate_input_sig and candidate_input_sig in completed_input_signatures:
                            continue
                        # Fix #14c: skip when the candidate's output path
                        # fuzzy-resolves on disk to a file already produced
                        # by a completed step. Filename-rename livelock
                        # (e.g. ``ancestor_aligned.bam`` aliasing to
                        # ``anc_aligned.bam`` on disk) is suppressed here.
                        candidate_resolved_out_sig = self._stepwise_resolved_output_signature(
                            candidate_step_item
                        )
                        if (
                            candidate_resolved_out_sig
                            and candidate_resolved_out_sig in completed_resolved_out_signatures
                        ):
                            continue
                        # Fix #14d: skip candidates that are *truly* bare
                        # (no arguments, parameter_hints, branch_id,
                        # sample_name, or objective) when the same tool
                        # has already been run to completion. The harness
                        # would otherwise fill the empty arguments from
                        # context and re-execute the identical command.
                        # Candidates with distinguishing metadata (e.g.
                        # ``branch_id: evol1``) describe legitimate
                        # per-sample work and must be allowed through even
                        # if ``arguments`` is empty.
                        def _has_content_trunc(v: Any) -> bool:
                            if v is None:
                                return False
                            if isinstance(v, (str, list, tuple, set, dict)):
                                return bool(v)
                            return True

                        candidate_is_bare_truncation = not (
                            _has_content_trunc(candidate_step_item.get("arguments"))
                            or _has_content_trunc(candidate_step_item.get("parameter_hints"))
                            or _has_content_trunc(candidate_step_item.get("branch_id"))
                            or _has_content_trunc(candidate_step_item.get("sample_name"))
                            or _has_content_trunc(candidate_step_item.get("objective"))
                        )
                        if (
                            candidate_is_bare_truncation
                            and candidate_tool
                            and candidate_tool in completed_tool_names
                        ):
                            continue
                        if self._stepwise_branch_stage_rejection_reason(
                            candidate_step=candidate_step_item,
                        ):
                            continue
                        chosen_index = candidate_index
                        break
                original_step_count = len(steps)
                pending_candidate_steps = self._stepwise_sanitize_pending_candidate_steps(
                    steps[chosen_index + 1 :]
                )
                truncated_candidate = dict(candidate_plan)
                truncated_candidate["plan"] = [steps[chosen_index]]
                candidate_plan = truncated_candidate
                steps = [steps[chosen_index]]
                attempt_rows.append(
                    {
                        "attempt": int(attempt_num),
                        "strategy": strategy,
                        "status": "truncated_to_first_step",
                        "elapsed_seconds": round(float(elapsed), 3),
                        "reason": (
                            f"Received {original_step_count} step(s); selected index "
                            f"{chosen_index} (first past the completed prefix and branch frontier) as the "
                            "next executable step."
                        ),
                        "recommended_tool_names": list(selection_meta.get("selected_skill_names", [])),
                    }
                )

            accepted, accepted_payload, rejection_reason = self._evaluate_stepwise_candidate(
                contract=contract,
                candidate=candidate_plan,
            )
            attempt_row = {
                "attempt": int(attempt_num),
                "strategy": strategy,
                "status": "accepted" if accepted else "rejected",
                "elapsed_seconds": round(float(elapsed), 3),
                "tool_name": str(steps[0].get("tool_name", "") or ""),
                "reason": rejection_reason,
                "recommended_tool_names": list(selection_meta.get("selected_skill_names", [])),
            }
            if not accepted:
                attempt_row["rejection_record"] = self._record_stepwise_candidate_rejection(
                    candidate_plan=candidate_plan,
                    rejection_reason=rejection_reason,
                    turn_num=turn_num,
                    attempt_num=attempt_num,
                    strategy=strategy,
                    source="planner_candidate",
                )
            attempt_rows.append(attempt_row)
            if accepted:
                return {
                    "status": "step",
                    "attempts": attempt_rows,
                    "contract_progress": contract_progress,
                    "accepted_payload": accepted_payload,
                    "candidate_plan": candidate_plan,
                    "pending_candidate_steps": pending_candidate_steps,
                }
            retry_reason = rejection_reason
            # Progressive tool masking: if this rejection was a duplicate of a
            # completed step, add the proposed tool to the exclusion set so
            # later attempts' skill subset and prompt both suppress it.
            rejected_step = steps[0] if isinstance(steps, list) and steps else {}
            if isinstance(rejected_step, dict):
                duplicate_prior = self._stepwise_duplicate_completed_step(
                    candidate_step=rejected_step,
                )
                if duplicate_prior:
                    duplicate_tool = str(rejected_step.get("tool_name", "") or "").strip()
                    if duplicate_tool and duplicate_tool not in allowed_tool_names:
                        excluded_tool_names.add(duplicate_tool)
                # Also mask the tool when the rejection was
                # ``duplicate_equivalent_step`` from stage-DAG semantic
                # validation. That issue fires when the LLM re-emits an
                # identical bash_run command after the prior one failed —
                # without masking, the LLM keeps re-proposing the same
                # command every attempt (it ignores the text rejection
                # reason), exhausts the turn, and the case fails. Masking
                # bash_run for the remaining attempts forces the planner
                # to pick a proper protocol tool (e.g. ``bwa_mem_align``
                # for the next branch) instead of looping on bash fixups.
                if "duplicate_equivalent_step" in rejection_reason:
                    rejected_tool = str(
                        rejected_step.get("tool_name", "") or ""
                    ).strip()
                    if rejected_tool:
                        excluded_tool_names.add(rejected_tool)
                # Mask the tool when the rejection was missing-required-arg.
                # The LLM will often re-emit the exact same incomplete step
                # across attempts (it has no way of knowing which arg the
                # registry requires), which exhausts the turn. Masking
                # pushes the planner to a different tool that either
                # doesn't need those args or ones it can populate.
                if "missing required argument" in rejection_reason:
                    rejected_tool = str(
                        rejected_step.get("tool_name", "") or ""
                    ).strip()
                    frontier_match = re.search(
                        r"Expected branch-stage tool:\s*`([^`]+)`",
                        rejection_reason,
                    )
                    expected_tool = (
                        str(frontier_match.group(1) or "").strip()
                        if frontier_match
                        else ""
                    )
                    if (
                        rejected_tool
                        and rejected_tool not in allowed_tool_names
                        and (not expected_tool or rejected_tool != expected_tool)
                    ):
                        excluded_tool_names.add(rejected_tool)
                # Branch-stage frontier rejections are hard stage-order
                # constraints. If the rejected tool is different from the
                # expected frontier tool, mask it for the rest of this turn
                # so the planner cannot keep racing downstream annotation or
                # export ahead of branch-local work. When the tool matches
                # (e.g. bcftools_isec_run for the wrong branch), keep it
                # available so the retry can use the same wrapper with the
                # correct branch.
                frontier_match = re.search(
                    r"Expected branch-stage tool:\s*`([^`]+)`",
                    rejection_reason,
                )
                if frontier_match:
                    expected_tool = str(frontier_match.group(1) or "").strip()
                    rejected_tool = str(
                        rejected_step.get("tool_name", "") or ""
                    ).strip()
                    if rejected_tool and expected_tool and rejected_tool != expected_tool:
                        excluded_tool_names.add(rejected_tool)

        raise ValueError(
            "Stepwise planner did not produce a usable next step. "
            + (retry_reason or "No valid candidate was returned.")
        )

    def _prepare_stepwise_context(self) -> dict[str, Any]:
        """Prepare contract, analysis spec, and bookkeeping for stepwise mode."""

        contract = _infer_request_contract(self.cfg.prompt, self.catalog)
        self.run["plan_contract"] = contract
        self._prepare_analysis_spec(contract)
        self._refresh_environment_snapshot()
        initialize_plan_preparation_state(
            self.run,
            catalog_summary=ranked_fallback_catalog_metadata(),
        )
        self.run["planner_strategy_used"] = "stepwise_next_step"
        self.run["plan"] = {"thought_process": "", "plan": []}
        self.run["step_statuses"] = []
        self.run["stepwise_pending_candidate_steps"] = []
        self.run["next_step_idx"] = 0
        self.run["contract_validation"] = self._assess_contract_for_plan(self._run_plan_dict(), contract)
        self.run["protocol_validation"] = assess_protocol_grounding(
            self._run_plan_dict(),
            self._run_analysis_spec_dict(),
        )
        self.run["status"] = "planned"
        return contract

    def _run_end_to_end_stepwise(self) -> dict[str, Any]:
        """Run the harness in one-step-at-a-time planning mode."""

        self._init_run()
        self._persist_state()

        _emit(f"Run ID: {self.run['run_uid']}", quiet=self.cfg.quiet)
        _emit("Preparing stepwise execution...", quiet=self.cfg.quiet)
        contract = self._prepare_stepwise_context()
        self._persist_state()

        max_turns = self._stepwise_max_turns()
        while len(self.run.get("stepwise_turns", [])) < max_turns:
            turn_num = len(self.run.get("stepwise_turns", [])) + 1
            current_progress = self._assess_contract_for_plan(self._run_plan_dict(), contract)
            current_protocol = assess_protocol_grounding(
                self._run_plan_dict(),
                self._run_analysis_spec_dict(),
            )
            self.run["contract_validation"] = current_progress
            self.run["protocol_validation"] = current_protocol
            if bool(current_progress.get("passed", False)) and bool(current_protocol.get("passed", False)):
                self.run["status"] = "completed"
                self._finalize_completed_run()
                break

            try:
                decision = self._plan_next_step_turn(contract=contract, turn_num=turn_num)
            except Exception as exc:
                self.run["stepwise_turns"].append(
                    {
                        "turn": int(turn_num),
                        "attempts": [],
                        "status": "failed",
                        "error": str(exc).strip() or exc.__class__.__name__,
                    }
                )
                self.run["status"] = "failed"
                self.run["error"] = str(exc).strip() or exc.__class__.__name__
                break
            turn_record = {
                "turn": int(turn_num),
                "attempts": list(decision.get("attempts", [])),
                "status": str(decision.get("status", "") or ""),
            }

            if decision["status"] == "done":
                if bool(current_progress.get("passed", False)) and bool(current_protocol.get("passed", False)):
                    self.run["stepwise_turns"].append(turn_record)
                    self.run["status"] = "completed"
                    self._finalize_completed_run()
                    break
                self.run["stepwise_turns"].append(turn_record)
                self.run["status"] = "failed"
                self.run["error"] = (
                    "Stepwise planner reported DONE before the request contract and protocol grounding were satisfied."
                )
                break

            accepted_payload = decision.get("accepted_payload", {})
            self._adopt_stepwise_candidate(
                accepted_payload=accepted_payload if isinstance(accepted_payload, dict) else {},
            )
            pending_candidate_steps = self._stepwise_sanitize_pending_candidate_steps(
                decision.get("pending_candidate_steps", [])
            )
            self.run["stepwise_pending_candidate_steps"] = pending_candidate_steps
            latest_steps = self._run_plan_dict().get("plan", []) if isinstance(self._run_plan_dict().get("plan", []), list) else []
            latest_step = latest_steps[-1] if latest_steps else {}
            turn_record["accepted_step"] = {
                "step_id": int(latest_step.get("step_id", len(latest_steps)) or len(latest_steps)),
                "tool_name": str(latest_step.get("tool_name", "") or ""),
            }
            if pending_candidate_steps:
                turn_record["pending_candidate_step_count"] = len(pending_candidate_steps)
                turn_record["pending_candidate_steps"] = deepcopy(pending_candidate_steps)
            self.run["stepwise_turns"].append(turn_record)
            self._persist_state()

            _emit(
                f"Starting stepwise turn {turn_num} execution for step {turn_record['accepted_step']['step_id']}",
                quiet=self.cfg.quiet,
            )
            preserved_pending_candidate_steps = deepcopy(pending_candidate_steps)
            self._execute_once(finalize_run=False)
            if bool(self.run.get("stepwise_last_step_failed", False)):
                self.run["stepwise_pending_candidate_steps"] = []
                self.run["auto_repair_last_class"] = classify_failure(self.run)
                self.run["status"] = "planned"
                self._persist_state()
                continue
            self.run["stepwise_pending_candidate_steps"] = preserved_pending_candidate_steps

            post_step_progress = self._assess_contract_for_plan(self._run_plan_dict(), contract)
            post_step_protocol = assess_protocol_grounding(
                self._run_plan_dict(),
                self._run_analysis_spec_dict(),
            )
            self.run["contract_validation"] = post_step_progress
            self.run["protocol_validation"] = post_step_protocol
            if bool(post_step_progress.get("passed", False)) and bool(post_step_protocol.get("passed", False)):
                self.run["status"] = "completed"
                self._finalize_completed_run()
                break

            self.run["status"] = "planned"
            self._persist_state()
        else:
            self.run["status"] = "failed"
            self.run["error"] = (
                f"Stepwise execution reached the maximum turn limit ({max_turns}) before satisfying the request."
            )

        if self.run.get("status") == "planned":
            self.run["status"] = "failed"
            if not str(self.run.get("error", "")).strip():
                self.run["error"] = "Stepwise execution ended without completion."

        self.run["finished_at"] = _now_utc_iso()
        self._record_graph_outcome()
        self._persist_state()
        self._write_exit()
        return self._result_payload()
