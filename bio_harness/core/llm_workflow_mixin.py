"""Workflow-skeleton and hierarchical-expansion helpers for ``BioLLM``."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from bio_harness.core.analysis_spec import AnalysisSpecSchema, normalize_analysis_spec
from bio_harness.core.benchmark_policy import is_bioagentbench_planning_strict_policy
from bio_harness.core.hierarchical_planning import (
    StepExecutionSpecSchema,
    WorkflowSpecSchema,
    assemble_executable_plan,
    should_use_hierarchical_planning,
)
from bio_harness.core.strict_artifact_binding import bind_step_spec_for_benchmark_policy
from bio_harness.core.llm_types import BioHarnessError, LLMOutputSchema
from bio_harness.core.tool_registry import default_tool_registry

_WORKFLOW_TOOL_REGISTRY = default_tool_registry()
_EVOLUTION_BRANCH_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])evol\d+(?![A-Za-z0-9])", re.IGNORECASE)
_BRANCH_LOCAL_ARGUMENTS: dict[str, frozenset[str]] = {
    "bwa_mem_align": frozenset({"reads_1", "reads_2", "output_bam", "sample_name"}),
    "freebayes_call": frozenset({"input_bam", "output_vcf"}),
    "snpeff_annotate": frozenset({"input_vcf", "output_vcf"}),
}


def _workflow_depended_steps(
    workflow_spec: dict[str, Any],
    workflow_step: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the upstream workflow steps referenced by one step dependency list."""

    depends_on = {
        int(dep)
        for dep in workflow_step.get("depends_on", [])
        if isinstance(dep, int) or str(dep).isdigit()
    }
    if not depends_on:
        return []
    branch_id = str(workflow_step.get("branch_id", "") or "").strip()
    upstream_steps: list[dict[str, Any]] = []
    for step in workflow_spec.get("workflow", []):
        if not isinstance(step, dict):
            continue
        if int(step.get("step_id", -1)) not in depends_on:
            continue
        upstream_branch = str(step.get("branch_id", "") or "").strip()
        if branch_id and upstream_branch and upstream_branch != branch_id:
            continue
        upstream_steps.append(step)
    return upstream_steps


def _workflow_tool_supports_argument(tool_name: str, argument_name: str) -> bool:
    """Return whether one registered workflow tool accepts an argument."""

    tool = str(tool_name or "").strip()
    argument = str(argument_name or "").strip()
    if not tool or not argument:
        return False
    return argument in _WORKFLOW_TOOL_REGISTRY.parameter_schema_for(tool)


def _upstream_output_bams_for_step(
    workflow_spec: dict[str, Any],
    workflow_step: dict[str, Any],
) -> list[str]:
    """Collect deterministic upstream BAM outputs for one workflow step."""

    ordered: list[str] = []
    seen: set[str] = set()
    for upstream in _workflow_depended_steps(workflow_spec, workflow_step):
        upstream_hints = upstream.get("parameter_hints", {})
        if not isinstance(upstream_hints, dict):
            continue
        output_bam = str(upstream_hints.get("output_bam", "") or "").strip()
        if not output_bam or output_bam in seen:
            continue
        seen.add(output_bam)
        ordered.append(output_bam)
    return ordered


def _inherit_upstream_bam_bindings(
    *,
    tool_name: str,
    args: dict[str, Any],
    workflow_spec: dict[str, Any],
    workflow_step: dict[str, Any],
) -> dict[str, Any]:
    """Rebind BAM-consuming step arguments to depended upstream BAM producers."""

    upstream_bams = _upstream_output_bams_for_step(workflow_spec, workflow_step)
    if not upstream_bams:
        return args

    updated = dict(args)
    if "input_bam" in updated or _workflow_tool_supports_argument(tool_name, "input_bam"):
        updated["input_bam"] = upstream_bams[-1]
    if "input_bams" in updated or _workflow_tool_supports_argument(tool_name, "input_bams"):
        updated["input_bams"] = list(upstream_bams)
    return updated


def _rewrite_value_to_target_evolution_branch(value: Any, *, target_branch: str) -> tuple[Any, bool]:
    """Rewrite one value to the target evolution branch when the source is unique.

    Args:
        value: Arbitrary planner-authored argument value.
        target_branch: Workflow-validated branch ID such as ``evol2``.

    Returns:
        Tuple of ``(updated_value, changed)``. The helper only rewrites values
        when exactly one non-target evolution branch token is present, which
        keeps the repair low-ambiguity.
    """

    if value is None:
        return value, False
    if isinstance(value, list):
        changed = False
        updated: list[Any] = []
        for item in value:
            repaired, item_changed = _rewrite_value_to_target_evolution_branch(
                item,
                target_branch=target_branch,
            )
            updated.append(repaired)
            changed = changed or item_changed
        return updated, changed
    if isinstance(value, tuple):
        updated, changed = _rewrite_value_to_target_evolution_branch(
            list(value),
            target_branch=target_branch,
        )
        return tuple(updated), changed
    if isinstance(value, set):
        updated, changed = _rewrite_value_to_target_evolution_branch(
            list(value),
            target_branch=target_branch,
        )
        return set(updated), changed
    if not isinstance(value, str):
        return value, False
    text = str(value or "")
    matches = sorted({match.group(0).lower() for match in _EVOLUTION_BRANCH_TOKEN_RE.finditer(text)})
    if not matches:
        return value, False
    non_target = [token for token in matches if token != target_branch]
    if len(non_target) != 1:
        return value, False
    source_branch = non_target[0]
    updated = re.sub(
        rf"(?<![A-Za-z0-9]){re.escape(source_branch)}(?![A-Za-z0-9])",
        target_branch,
        text,
        flags=re.IGNORECASE,
    )
    return updated, updated != text


def _rebind_branch_local_step_arguments(
    *,
    tool_name: str,
    args: dict[str, Any],
    workflow_step: dict[str, Any],
) -> dict[str, Any]:
    """Rebind branch-local arguments to the workflow step's concrete branch.

    Args:
        tool_name: Planned tool name for the expanded step.
        args: Candidate argument mapping.
        workflow_step: Normalized workflow skeleton row for the step.

    Returns:
        Updated argument mapping whose branch-local values match the workflow
        step's ``branch_id`` when a unique source branch token was present.
    """

    target_branch = str(workflow_step.get("branch_id", "") or "").strip().lower()
    if not target_branch.startswith("evol"):
        return args
    branch_local_args = _BRANCH_LOCAL_ARGUMENTS.get(str(tool_name or "").strip().lower())
    if not branch_local_args:
        return args

    updated = dict(args)
    for argument_name in sorted(branch_local_args):
        if argument_name == "sample_name":
            if argument_name in updated or _workflow_tool_supports_argument(tool_name, argument_name):
                updated[argument_name] = target_branch
            continue
        repaired, changed = _rewrite_value_to_target_evolution_branch(
            updated.get(argument_name),
            target_branch=target_branch,
        )
        if changed:
            updated[argument_name] = repaired
    return updated


class LLMWorkflowMixin:
    """Provides workflow-skeleton expansion and analysis-review helpers."""

    def _workflow_skeleton_predict_budget(self, *, user_query: str) -> int:
        """Return a generation budget for one workflow-skeleton request."""

        budget = max(550, min(1400, int(self.default_num_predict * 0.65)))
        prompt = str(user_query or "")
        if any(
            marker in prompt
            for marker in (
                "Current plan contract gaps:",
                "Current protocol gaps:",
                "Semantic validation issues:",
                "Latest contract gaps:",
            )
        ):
            budget = max(budget, min(2200, int(max(self.default_num_predict, budget) * 0.95)))
        return budget

    def _hierarchical_mode_enabled(
        self,
        *,
        planner_mode: str,
        user_query: str,
        analysis_spec: dict[str, Any] | None,
    ) -> bool:
        benchmark_policy = ""
        if isinstance(analysis_spec, dict):
            benchmark_policy = str(analysis_spec.get("benchmark_policy", "") or "").strip()
        mode = str(planner_mode or "auto").strip().lower()
        if mode == "hierarchical":
            return True
        if mode in {"direct", "off"}:
            return False
        if is_bioagentbench_planning_strict_policy(benchmark_policy):
            normalized_mode = (
                "hierarchical"
                if self.hierarchical_mode == "always"
                else ("off" if self.hierarchical_mode == "off" else "auto")
            )
            return normalized_mode == "hierarchical"
        normalized_mode = (
            "hierarchical"
            if self.hierarchical_mode == "always"
            else ("off" if self.hierarchical_mode == "off" else "auto")
        )
        return should_use_hierarchical_planning(
            planner_mode=normalized_mode,
            user_query=user_query,
            analysis_spec=analysis_spec,
        )

    def _constrain_step_spec_to_workflow_context(
        self,
        *,
        step_spec: dict[str, Any],
        workflow_spec: dict[str, Any],
        workflow_step: dict[str, Any],
        analysis_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind branch-specific step arguments back to the workflow context."""

        constrained = dict(step_spec if isinstance(step_spec, dict) else {})
        args = constrained.get("arguments", {})
        if not isinstance(args, dict):
            args = {}
        args = dict(args)
        tool_name = str(constrained.get("tool_name", "")).strip()

        hints = workflow_step.get("parameter_hints", {})
        if not isinstance(hints, dict):
            hints = {}
        for key, value in hints.items():
            hint_key = str(key or "").strip()
            if not hint_key or value in (None, "", [], {}):
                continue
            if hint_key in args or _workflow_tool_supports_argument(tool_name, hint_key):
                args[hint_key] = value

        args = _inherit_upstream_bam_bindings(
            tool_name=tool_name,
            args=args,
            workflow_spec=workflow_spec,
            workflow_step=workflow_step,
        )
        args = _rebind_branch_local_step_arguments(
            tool_name=tool_name,
            args=args,
            workflow_step=workflow_step,
        )

        analysis_type = ""
        if isinstance(analysis_spec, dict):
            analysis_type = str(analysis_spec.get("analysis_type", "") or "").strip().lower()
        if analysis_type:
            constrained = bind_step_spec_for_benchmark_policy(
                step_spec=constrained,
                workflow_step=workflow_step,
                analysis_spec=analysis_spec,
            )
            args = constrained.get("arguments", {}) if isinstance(constrained.get("arguments", {}), dict) else {}

        constrained["arguments"] = args
        return constrained

    def _expand_workflow_step(
        self,
        *,
        user_query: str,
        workflow_spec: dict[str, Any],
        workflow_step: dict[str, Any],
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
        seed_step: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        step_id = int(workflow_step.get("step_id", 0))
        tool_name = str(workflow_step.get("tool_name", "")).strip()
        outline_like = {
            "plan_outline": workflow_spec.get("workflow", [])
            if isinstance(workflow_spec.get("workflow", []), list)
            else []
        }
        step_spec = self._request_structured_response(
            stage="step_expansion",
            schema_model=StepExecutionSpecSchema,
            messages=self._build_step_messages(
                user_query=user_query,
                workflow_spec=workflow_spec,
                workflow_step=workflow_step,
                available_skills=available_skills,
                analysis_spec=analysis_spec,
                seed_step=seed_step,
            ),
            num_predict=self._plan_expansion_predict_budget(
                outline=outline_like,
                analysis_spec=analysis_spec,
                user_query=user_query,
            ),
            normalizer=lambda raw: self._normalize_step_output(raw, step_id=step_id, tool_name=tool_name),
            model_override=model_override,
        )
        return self._constrain_step_spec_to_workflow_context(
            step_spec=step_spec,
            workflow_spec=workflow_spec,
            workflow_step=workflow_step,
            analysis_spec=analysis_spec,
        )

    def _think_hierarchical(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
        seed_plan: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        workflow_spec = self._request_structured_response(
            stage="workflow_skeleton",
            schema_model=WorkflowSpecSchema,
            messages=self._build_workflow_messages(user_query, available_skills, analysis_spec, seed_plan),
            num_predict=self._workflow_skeleton_predict_budget(user_query=user_query),
            normalizer=self._normalize_workflow_output,
            model_override=model_override,
        )
        workflow_steps = workflow_spec.get("workflow", []) if isinstance(workflow_spec.get("workflow", []), list) else []
        if not workflow_steps:
            raise BioHarnessError("Hierarchical planner returned an empty workflow.")

        seed_by_id: dict[int, dict[str, Any]] = {}
        if isinstance(seed_plan, dict):
            for step in seed_plan.get("plan", []) if isinstance(seed_plan.get("plan", []), list) else []:
                if not isinstance(step, dict):
                    continue
                try:
                    sid = int(step.get("step_id"))
                except Exception:
                    continue
                seed_by_id[sid] = step

        strict_policy = False
        if isinstance(analysis_spec, dict):
            strict_policy = is_bioagentbench_planning_strict_policy(analysis_spec.get("benchmark_policy"))
        max_workers = 1 if strict_policy else min(self.hierarchical_max_workers, max(1, len(workflow_steps)))
        step_specs: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._expand_workflow_step,
                    user_query=user_query,
                    workflow_spec=workflow_spec,
                    workflow_step=workflow_step,
                    available_skills=available_skills,
                    analysis_spec=analysis_spec,
                    seed_step=seed_by_id.get(int(workflow_step.get("step_id", 0)), {}),
                    model_override=model_override,
                ): workflow_step
                for workflow_step in workflow_steps
            }
            for future in as_completed(futures):
                step_specs.append(future.result())

        executable = assemble_executable_plan(
            workflow_spec,
            step_specs,
            analysis_spec=analysis_spec,
            seed_plan=seed_plan,
        )
        normalized = self._normalize_plan_output(executable)
        validated = LLMOutputSchema(**normalized)
        self._planner_trace(
            "HIERARCHICAL_PLAN_SUCCESS",
            {
                "workflow_steps": len(workflow_steps),
                "expanded_steps": len(validated.plan),
                "max_workers": int(max_workers),
            },
            raw_content=json.dumps(validated.model_dump(), ensure_ascii=True, indent=2),
        )
        return validated.model_dump()

    def design_analysis(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        contract: dict[str, Any] | None = None,
        fallback_spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Review and normalize the analysis brief before executable planning."""

        available_names = [str(skill.get("name", "")).strip() for skill in available_skills if isinstance(skill, dict)]
        fallback = normalize_analysis_spec(
            fallback_spec if isinstance(fallback_spec, dict) else {},
            user_query=user_query,
            contract=contract,
            available_skill_names=available_names,
        )
        skill_names = ", ".join([name for name in available_names if name])
        system_prompt = f"""You are a bioinformatics analysis reviewer producing a compact analysis brief before execution planning.
Available tools: {skill_names}.

RULES:
1. Output only valid JSON matching the schema.
2. Keep the brief compact and concrete.
3. Choose a method family and parameter profile that fit the biological objective.
4. Prefer benchmark recipes and official manuals over generic defaults.
5. Only reference tools from the available list.
6. If the seed brief contains protocol grounding, treat protocol-required tools, signals, and rules as binding unless impossible with the available tools.
7. If `plan_skeleton` is present, keep it compact and abstract: tool names plus short purposes only.
8. Do NOT include concrete file paths, long shell commands, or per-file inventories inside `plan_skeleton`.
"""
        user_prompt = (
            f"User request:\n{user_query}\n\n"
            f"Request contract:\n{json.dumps(contract or {}, ensure_ascii=True, indent=2)}\n\n"
            f"Seed analysis brief:\n{json.dumps(fallback, ensure_ascii=True, indent=2)}"
        )
        try:
            raw = self._request_structured_response(
                stage="analysis_review",
                schema_model=AnalysisSpecSchema,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                num_predict=max(700, min(1800, int(self.default_num_predict * 0.75))),
                normalizer=lambda payload: normalize_analysis_spec(
                    payload if isinstance(payload, dict) else {},
                    user_query=user_query,
                    contract=contract,
                    available_skill_names=available_names,
                ),
                model_override=self.heavy_model_name,
                repair_allowed=False,
                repair_reason="Invalid analysis review response.",
            )
            return AnalysisSpecSchema(**raw).model_dump()
        except Exception as exc:
            if self._is_supervisor_timeout_error(exc):
                raise
            self._planner_trace(
                "ANALYSIS_REVIEW_FALLBACK",
                {"error": str(exc)},
                raw_content=json.dumps(fallback, ensure_ascii=True, indent=2),
            )
            return fallback
