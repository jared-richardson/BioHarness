"""Prompt-construction and output-normalization helpers for ``BioLLM``."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import ollama

from bio_harness.core.analysis_spec import build_analysis_brief
from bio_harness.core.hierarchical_planning import (
    normalize_step_execution_spec,
    normalize_workflow_spec,
    workflow_spec_from_plan,
)


class LLMPromptMixin:
    """Provides planner prompt builders and output normalizers."""

    _WORKFLOW_REPAIR_HEADINGS = (
        "Original user request:",
        "User request:",
        "Required contract:",
        "Current plan contract gaps:",
        "Current protocol gaps:",
        "Semantic validation issues:",
        "Latest contract gaps:",
        "Focused repair context:",
        "Current plan summary:",
        "Prior plan (if any):",
    )

    def _format_skills_for_prompt(self, available_skills: list[dict[str, Any]]) -> str:
        if self.planner_prompt_style == "full":
            return self._format_skills_for_prompt_full(available_skills)
        return self._format_skills_for_prompt_compact(available_skills)

    def _planner_skill_prompt_profile(self) -> dict[str, Any]:
        """Return model-conditioned prompt-compaction settings for skill rows."""

        model_name = str(getattr(self, "model_name", "") or "").strip().lower()
        compact_model = any(
            hint in model_name
            for hint in ("gemma", "qwen3-coder-next", "qwen3next", "codellama", "starcoder", "deepseek-coder")
        )
        return {
            "compact_model": compact_model,
            "max_desc_chars": min(int(self.max_desc_chars), 100) if compact_model else int(self.max_desc_chars),
            "max_optional_args": 5 if compact_model else 10,
            "max_outputs": 4 if compact_model else 6,
            "include_capabilities": compact_model,
            "include_categories": compact_model,
            "include_outputs": compact_model,
        }

    def _format_skills_for_prompt_full(self, available_skills: list[dict[str, Any]]) -> str:
        profile = self._planner_skill_prompt_profile()
        formatted_skills: list[str] = []
        for skill in available_skills:
            name = skill.get("name", "unknown_tool")
            description = skill.get("description", "No description provided.")
            parameters = skill.get("parameters", {})

            param_lines: list[str] = []
            for param_name, param_details in parameters.items():
                p_type = param_details.get("type", "string")
                p_desc = param_details.get("description", "No description.")
                p_required = "(required)" if param_details.get("required", False) else "(optional)"
                param_lines.append(f"- {param_name} ({p_type}): {p_desc} {p_required}")

            metadata_lines: list[str] = []
            if profile["include_capabilities"]:
                capabilities = [str(value).strip() for value in (skill.get("capabilities") or []) if str(value).strip()]
                if capabilities:
                    metadata_lines.append(f"Capabilities: {', '.join(capabilities[:6])}")
            if profile["include_categories"]:
                categories = [str(value).strip() for value in (skill.get("analysis_categories") or []) if str(value).strip()]
                if categories:
                    metadata_lines.append(f"Analysis categories: {', '.join(categories[:4])}")
            if profile["include_outputs"]:
                outputs = self._planner_skill_output_hints(skill)
                if outputs:
                    metadata_lines.append(f"Canonical outputs: {', '.join(outputs[: profile['max_outputs']])}")

            metadata_block = f"\n{chr(10).join(metadata_lines)}" if metadata_lines else ""
            formatted_skills.append(
                f"### {name}\nDescription: {description}\nParameters:\n"
                f"{chr(10).join(param_lines) if param_lines else '- None'}{metadata_block}"
            )
        return "\n\n".join(formatted_skills)

    def _format_skills_for_prompt_compact(self, available_skills: list[dict[str, Any]]) -> str:
        profile = self._planner_skill_prompt_profile()
        rows: list[str] = []
        for skill in available_skills:
            name = str(skill.get("name", "unknown_tool")).strip() or "unknown_tool"
            desc_parts: list[str] = []
            description = str(skill.get("description", "")).strip()
            if len(description) > profile["max_desc_chars"]:
                description = description[: profile["max_desc_chars"]].rstrip() + "..."
            desc_parts.append(description)
            when_not_to_use = str(skill.get("when_not_to_use", "")).strip()
            if when_not_to_use:
                desc_parts.append(when_not_to_use)
            combined_desc = ". ".join(part for part in desc_parts if part)
            input_types = skill.get("input_types", [])
            output_types = skill.get("output_types", [])
            io_text = ""
            if input_types or output_types:
                in_str = ", ".join(input_types) if input_types else "any"
                out_str = ", ".join(output_types) if output_types else "any"
                io_text = f" | io: {in_str} -> {out_str}"
            parameters = skill.get("parameters", {}) if isinstance(skill.get("parameters", {}), dict) else {}
            required: list[str] = []
            optional: list[str] = []
            for param_name, param_details in parameters.items():
                if not str(param_name).strip():
                    continue
                if isinstance(param_details, dict) and bool(param_details.get("required", False)):
                    required.append(str(param_name).strip())
                else:
                    optional.append(str(param_name).strip())
            req_text = ", ".join(sorted(set(required))[:10]) if required else "-"
            opt_text = ", ".join(sorted(set(optional))[: profile["max_optional_args"]]) if optional else "-"
            extras: list[str] = []
            if profile["include_capabilities"]:
                caps = [str(value).strip() for value in (skill.get("capabilities") or []) if str(value).strip()]
                if caps:
                    extras.append(f"caps=[{', '.join(caps[:4])}]")
            if profile["include_categories"]:
                cats = [str(value).strip() for value in (skill.get("analysis_categories") or []) if str(value).strip()]
                if cats:
                    extras.append(f"categories=[{', '.join(cats[:3])}]")
            if profile["include_outputs"]:
                outputs = self._planner_skill_output_hints(skill)
                if outputs:
                    extras.append(f"outputs=[{', '.join(outputs[: profile['max_outputs']])}]")
            extras_text = f" | {' | '.join(extras)}" if extras else ""
            rows.append(
                f"- {name}: {combined_desc}{io_text}{extras_text}"
                f" | required_args=[{req_text}] | optional_args=[{opt_text}]"
            )
        return "\n".join(rows)

    def _planner_skill_output_hints(self, skill: dict[str, Any]) -> list[str]:
        """Return stable output hints for planner prompt rows."""

        hints: list[str] = []
        canonical = skill.get("canonical_output_filenames", {})
        if isinstance(canonical, dict):
            for value in canonical.values():
                text = str(value).strip()
                if text:
                    hints.append(text)
        output_types = skill.get("output_types", [])
        if isinstance(output_types, list):
            for value in output_types:
                text = str(value).strip()
                if text:
                    hints.append(text)
        seen: set[str] = set()
        deduped: list[str] = []
        for hint in hints:
            if hint in seen:
                continue
            seen.add(hint)
            deduped.append(hint)
        return deduped

    def _analysis_brief_block(self, analysis_spec: dict[str, Any] | None) -> str:
        brief = build_analysis_brief(analysis_spec)
        if not brief:
            return ""
        return f"\nAnalysis brief:\n{brief}\n"

    def _atomic_wrapper_examples(self, available_skills: list[dict[str, Any]]) -> str:
        """Return compact wrapper examples for atomic-step planning prompts."""

        visible = {
            str(skill.get("name", "")).strip()
            for skill in available_skills
            if str(skill.get("name", "")).strip()
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

    def _plan_skeleton_branching_guidance(self, analysis_spec: dict[str, Any] | None) -> str:
        """Return a note clarifying that branch stages may expand into multiple steps."""

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

    def _workflow_seed_from_analysis_spec(self, analysis_spec: dict[str, Any] | None) -> dict[str, Any]:
        """Build a compact workflow seed from the assay-level plan skeleton."""

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

    def _compact_text_for_workflow(self, text: str) -> str:
        """Shorten workflow-stage text so prompts stay path-light and compact."""

        raw_text = str(text or "")

        def _replace_path(match: re.Match[str]) -> str:
            path = match.group(0)
            base = os.path.basename(path.rstrip("/"))
            return f"[PATH:{base or 'artifact'}]"

        compact = re.sub(r"/[A-Za-z0-9._~/-]+", _replace_path, raw_text)
        compact = re.sub(r"\s+", " ", compact).strip()
        return compact

    def _extract_workflow_repair_section(self, prompt: str, heading: str) -> str:
        """Extract one headed section from a planner repair prompt."""

        text = str(prompt or "")
        marker = f"{heading}\n"
        start = text.find(marker)
        if start < 0:
            return ""
        start += len(marker)
        next_markers = [
            idx
            for candidate in self._WORKFLOW_REPAIR_HEADINGS
            if candidate != heading
            for idx in [text.find(f"\n\n{candidate}\n", start)]
            if idx >= 0
        ]
        end = min(next_markers) if next_markers else len(text)
        return text[start:end].strip()

    def _workflow_repair_issue_lines(self, prompt: str) -> list[str]:
        """Summarize repair-stage issues for workflow planning."""

        heading = ""
        for candidate in (
            "Current plan contract gaps:",
            "Current protocol gaps:",
            "Semantic validation issues:",
            "Latest contract gaps:",
        ):
            if candidate in str(prompt or ""):
                heading = candidate
                break
        if not heading:
            return []

        raw_section = self._extract_workflow_repair_section(prompt, heading)
        if not raw_section:
            return []

        try:
            payload = json.loads(raw_section)
        except Exception:
            compact = self._compact_text_for_workflow(raw_section)
            return [compact[:160]] if compact else []

        lines: list[str] = []
        key_order = (
            "artifact_role_issues",
            "direct_wrapper_issues",
            "issues",
            "missing_required_tool_hints",
            "missing_tool_hints",
            "missing_capabilities",
        )
        for key in key_order:
            values = payload.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values[:4]:
                text = self._compact_text_for_workflow(str(value))
                if not text:
                    continue
                lines.append(f"{key}: {text[:180]}")
        return lines[:8]

    def _workflow_prompt_request_and_repair_focus(self, user_query: str) -> tuple[str, str]:
        """Return a workflow-safe request string plus compact repair guidance."""

        prompt = str(user_query or "")
        issue_lines = self._workflow_repair_issue_lines(prompt)
        if not issue_lines:
            return self._compact_text_for_workflow(prompt), ""

        request_section = self._extract_workflow_repair_section(prompt, "Original user request:")
        if not request_section:
            request_section = self._extract_workflow_repair_section(prompt, "User request:")
        compact_request = self._compact_text_for_workflow(request_section or prompt)
        repair_lines = [
            "Workflow repair focus:",
            "- Preserve the valid seed workflow structure and branch layout when possible.",
            "- Repair only the steps implicated by these unresolved issues:",
        ]
        repair_lines.extend(f"- {line}" for line in issue_lines)
        return compact_request, "\n".join(repair_lines)

    def _normalize_outline_output(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {"thought_process": "No thought process provided by model.", "plan_outline": []}
        out: dict[str, Any] = dict(raw)
        if "thought_process" not in out:
            out["thought_process"] = "No thought process provided by model."
        raw_outline = out.get("plan_outline", [])
        if not isinstance(raw_outline, list):
            raw_outline = []
        normalized: list[dict[str, Any]] = []
        for idx, step in enumerate(raw_outline, start=1):
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name", "")).strip()
            objective = str(step.get("objective", "")).strip() or "Execute the required analysis step."
            try:
                step_id = int(step.get("step_id", idx))
            except Exception:
                step_id = idx
            if not tool_name:
                continue
            normalized.append({"tool_name": tool_name, "objective": objective, "step_id": step_id})
        out["plan_outline"] = normalized
        return out

    def _normalize_plan_output(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {
                "thought_process": "No thought process provided by model.",
                "plan": [],
                "final_deliverables": [],
            }
        out: dict[str, Any] = dict(raw)
        if "thought_process" not in out:
            out["thought_process"] = "No thought process provided by model."
        raw_deliverables = out.get("final_deliverables", [])
        if not isinstance(raw_deliverables, list):
            raw_deliverables = []
        out["final_deliverables"] = [str(item).strip() for item in raw_deliverables if str(item).strip()]
        raw_plan = out.get("plan", [])
        if not isinstance(raw_plan, list):
            raw_plan = []
        if not raw_plan:
            raw_outline = out.get("plan_outline", [])
            if isinstance(raw_outline, list):
                raw_plan = list(raw_outline)
        normalized_plan: list[dict[str, Any]] = []
        for idx, step in enumerate(raw_plan, start=1):
            if not isinstance(step, dict):
                continue
            step_dict = dict(step)
            args = step_dict.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            sid = step_dict.get("step_id")
            if sid is None and "step_id" in args:
                sid = args.pop("step_id")
            try:
                sid_int = int(sid)
            except Exception:
                sid_int = idx
            tool_name = str(step_dict.get("tool_name", "")).strip()
            if not tool_name:
                tool_name = "bash_run" if "command" in args else "fastqc_run"
            normalized_plan.append(
                {
                    "tool_name": tool_name,
                    "arguments": args,
                    "step_id": sid_int,
                    "deliverables": [
                        str(item).strip()
                        for item in (step_dict.get("deliverables", []) or [])
                        if str(item).strip()
                    ]
                    if isinstance(step_dict.get("deliverables", []), list)
                    else [],
                    "expected_files": [
                        str(item).strip()
                        for item in (step_dict.get("expected_files", []) or [])
                        if str(item).strip()
                    ]
                    if isinstance(step_dict.get("expected_files", []), list)
                    else [],
                    "validation_method": str(step_dict.get("validation_method", "") or "").strip(),
                }
            )
        out["plan"] = normalized_plan
        return out

    def _normalize_workflow_output(self, raw: dict[str, Any]) -> dict[str, Any]:
        return normalize_workflow_spec(raw if isinstance(raw, dict) else {})

    def _normalize_step_output(self, raw: dict[str, Any], *, step_id: int, tool_name: str) -> dict[str, Any]:
        return normalize_step_execution_spec(
            raw if isinstance(raw, dict) else {},
            expected_step_id=int(step_id),
            expected_tool_name=str(tool_name).strip(),
        )

    def _build_outline_messages(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
    ) -> list[ollama.Message]:
        skill_names = ", ".join([skill.get("name", "unknown") for skill in available_skills])
        formatted_skills_details = self._format_skills_for_prompt(available_skills)
        analysis_block = self._analysis_brief_block(analysis_spec)
        system_prompt = f"""You are a Bioinformatics OS planner producing a compact outline.
Available tools: {skill_names}.
Tool details:
{formatted_skills_details}
{analysis_block}

RULES:
1. Output only valid JSON.
2. Choose only tools from the available list.
3. Honor explicit user tool requests.
4. If the analysis brief specifies a chosen method or preferred tools, follow that guidance unless the tool is unavailable.
5. If alternative splicing is requested, include a dedicated splicing tool such as `rmats_run`, `dexseq_run`, or `majiq_run`.
6. Each `bash_run` step must perform exactly one logical operation. If the workflow needs multiple operations, emit multiple steps.
7. Prefer typed wrappers over `bash_run` whenever a wrapper exists. Available wrapper examples in this tool set: {self._atomic_wrapper_examples(available_skills)}.
8. Keep the outline compact and ordered and prefer 8 steps or fewer.
9. Do not include runtime install/fetch commands.
10. `plan_outline` contains only tool_name, objective, and step_id.
11. If the analysis brief includes protocol-required tools, signals, or rules from a task-local recipe, satisfy them explicitly.
"""
        return [
            ollama.Message(role="system", content=system_prompt),
            ollama.Message(role="user", content=user_query),
        ]

    def _build_expansion_messages(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        outline: dict[str, Any],
        analysis_spec: dict[str, Any] | None = None,
    ) -> list[ollama.Message]:
        skill_names = ", ".join([skill.get("name", "unknown") for skill in available_skills])
        formatted_skills_details = self._format_skills_for_prompt(available_skills)
        analysis_block = self._analysis_brief_block(analysis_spec)
        system_prompt = f"""You are a Bioinformatics OS planner producing an executable plan.
Available tools: {skill_names}.
Tool details:
{formatted_skills_details}
{analysis_block}

RULES:
1. Output only valid JSON matching the executable plan schema.
2. Honor the outline order unless a later step depends on an earlier prerequisite.
3. Honor explicit user tool requests.
4. If the analysis brief specifies a chosen method, use that method unless it is unavailable or impossible for the request.
5. Prefer tools listed in the analysis brief and avoid discouraged tools unless there is no viable alternative.
6. If alternative splicing is requested, include at least one dedicated splicing tool step such as `rmats_run`, `dexseq_run`, or `majiq_run`.
7. Each `bash_run` step must perform exactly one logical operation. If the workflow needs multiple operations, emit multiple steps.
8. Prefer typed wrappers over `bash_run` whenever a wrapper exists. Available wrapper examples in this tool set: {self._atomic_wrapper_examples(available_skills)}.
9. Keep `thought_process` to one short sentence.
10. Prefer concise executable plans, avoid redundant steps, and prefer about 10 steps or fewer.
11. If the analysis brief includes protocol-required tools, signals, or rules from a task-local recipe, satisfy them explicitly in the executable plan.
12. If the request names final published outputs that are not documented tool parameters, record them in `final_deliverables` instead of inventing undocumented arguments like `output_file` or `final_csv`.
"""
        user_prompt = (
            f"User request:\n{user_query}\n\n"
            f"Planning outline:\n{json.dumps(outline, ensure_ascii=True, indent=2)}"
        )
        return [
            ollama.Message(role="system", content=system_prompt),
            ollama.Message(role="user", content=user_prompt),
        ]

    def _build_workflow_messages(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
        seed_plan: dict[str, Any] | None = None,
    ) -> list[ollama.Message]:
        skill_names = ", ".join([str(skill.get("name", "unknown")).strip() for skill in available_skills if str(skill.get("name", "")).strip()])
        formatted_skills_details = self._format_skills_for_prompt(available_skills)
        analysis_block = self._analysis_brief_block(analysis_spec)
        seed_workflow = workflow_spec_from_plan(seed_plan or {}) if isinstance(seed_plan, dict) and seed_plan else {}
        if not seed_workflow.get("workflow"):
            assay_seed = self._workflow_seed_from_analysis_spec(analysis_spec)
            if assay_seed.get("workflow"):
                seed_workflow = assay_seed
        compact_query, repair_focus = self._workflow_prompt_request_and_repair_focus(user_query)
        system_prompt = f"""You are a bioinformatics workflow planner producing a typed workflow skeleton.
Available tools: {skill_names}.
Tool details:
{formatted_skills_details}
{analysis_block}

RULES:
1. Output only valid JSON matching the workflow schema.
2. The workflow must be executable after later step-specific expansion.
3. Prefer explicit dependencies and preserve branch structure for shared comparisons.
4. Keep tool choices aligned with protocol grounding and analysis constraints.
5. Keep the workflow compact, but do not collapse branches that are biologically distinct.
6. If a seed workflow is provided, preserve valid structure where possible and change only what is necessary.
7. Keep `parameter_hints` compact. Include only high-value semantic flags or short hints like ploidy, careful mode, branch role, or expected deliverable name.
8. Do NOT embed long shell commands, absolute paths, or path-bearing inputs/outputs like `reads_1`, `reads_2`, `reference_fasta`, `input_bam`, `output_bam`, `output_vcf`, or `output_dir` in the workflow skeleton. Concrete paths belong in later step expansion.
9. Every workflow step must have a unique `step_id`.
10. When a branch is split into concrete comparands (for example ancestor, evol1, evol2), emit only the concrete branch steps. Do not emit an umbrella placeholder step and its children with the same step_id.
11. If a downstream operation must happen separately for each branch before comparison (for example variant calling or annotation on evol1 and evol2), emit one concrete workflow step per branch instead of a single generic shared step.
12. Use `final_deliverables` for requested published outputs instead of stuffing them into `parameter_hints`.
13. If repair guidance is supplied, use it only to identify what must change; do not copy path-heavy diagnostics or full concrete tool arguments into the workflow skeleton.
14. Keep `thought_process` to one short sentence.
15. Do not narrate rule conflicts, step-count tradeoffs, or alternative drafts in `thought_process`. Decide internally and emit the best workflow directly.
16. If compact seed guidance conflicts with branch-specific execution requirements, prefer the concrete branch-safe workflow and do not explain the discrepancy in `thought_process`.
"""
        prompt_sections = [f"User request:\n{compact_query}"]
        if repair_focus:
            prompt_sections.append(repair_focus)
        prompt_sections.append(f"Seed workflow:\n{json.dumps(seed_workflow, ensure_ascii=True, indent=2)}")
        user_prompt = "\n\n".join(prompt_sections)
        return [
            ollama.Message(role="system", content=system_prompt),
            ollama.Message(role="user", content=user_prompt),
        ]

    def _compact_user_query_for_workflow(self, user_query: str) -> str:
        """Shorten the workflow-stage query so the model stops echoing full paths."""

        return self._compact_text_for_workflow(user_query)

    def _build_step_messages(
        self,
        *,
        user_query: str,
        workflow_spec: dict[str, Any],
        workflow_step: dict[str, Any],
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
        seed_step: dict[str, Any] | None = None,
    ) -> list[ollama.Message]:
        tool_name = str(workflow_step.get("tool_name", "")).strip()
        skill_meta = {}
        for skill in available_skills:
            if str(skill.get("name", "")).strip() == tool_name:
                skill_meta = skill
                break
        analysis_block = self._analysis_brief_block(analysis_spec)
        upstream = [step for step in workflow_spec.get("workflow", []) if int(step.get("step_id", -1)) in set(workflow_step.get("depends_on", []))]
        downstream = [step for step in workflow_spec.get("workflow", []) if int(workflow_step.get("step_id", -1)) in set(step.get("depends_on", []))]
        tool_specific_rules = self._step_prompt_tool_specific_rules(tool_name, available_skills=available_skills)
        system_prompt = f"""You are a bioinformatics step planner.
You must produce a concrete execution spec for exactly one workflow step using only tool `{tool_name}`.
{analysis_block}

RULES:
1. Output only valid JSON matching the step execution schema.
2. Keep `tool_name` fixed to `{tool_name}` and keep `step_id` fixed.
3. Use concrete runnable arguments.
4. Preserve seed arguments and paths when they are already valid and compatible.
5. Respect downstream constraints and global workflow constraints.
6. Do not invent runtime installation or fetch steps.
7. Preserve the branch identity of the workflow step. If the workflow step is for `evol1`, do not substitute `evol2` or `anc` paths, filenames, or sample inputs.
8. When the workflow step has branch-specific `parameter_hints`, treat them as authoritative for that branch's output paths and branch role.
9. Resolve local inconsistencies silently and emit the best executable step JSON directly. Do not critique the workflow, narrate contradictions, or propose alternate plans.
10. Do not wrap the answer in markdown fences, repeated drafts, or example JSON blocks.
{tool_specific_rules}
"""
        user_prompt = (
            f"Original request:\n{user_query}\n\n"
            f"Global workflow constraints:\n{json.dumps(workflow_spec.get('global_constraints', []), ensure_ascii=True, indent=2)}\n\n"
            f"Workflow step:\n{json.dumps(workflow_step, ensure_ascii=True, indent=2)}\n\n"
            f"Upstream context:\n{json.dumps(upstream, ensure_ascii=True, indent=2)}\n\n"
            f"Downstream context:\n{json.dumps(downstream, ensure_ascii=True, indent=2)}\n\n"
            f"Tool metadata:\n{json.dumps(skill_meta, ensure_ascii=True, indent=2)}\n\n"
            f"Seed step:\n{json.dumps(seed_step or {}, ensure_ascii=True, indent=2)}"
        )
        return [
            ollama.Message(role="system", content=system_prompt),
            ollama.Message(role="user", content=user_prompt),
        ]

    def _step_prompt_tool_specific_rules(
        self,
        tool_name: str,
        *,
        available_skills: list[dict[str, Any]] | None = None,
    ) -> str:
        """Return additional tool-specific rules for step expansion prompts."""

        if tool_name != "bash_run":
            return ""
        wrapper_examples = self._atomic_wrapper_examples(available_skills or [])
        return (
            "TOOL-SPECIFIC RULES FOR `bash_run`:\n"
            "- Emit exactly one logical operation in the command.\n"
            "- Do not use pipes, `&&`, `;`, `||`, loops, or side-effecting conditionals.\n"
            f"- If a typed wrapper exists for the objective (for example {wrapper_examples}), the workflow should prefer that wrapper instead of `bash_run`.\n"
            "- Prefer checked-in helper scripts or single CLI invocations over inline Python, R, awk, or heredoc programs.\n"
            "- Do not embed long comment blocks, copied field catalogs, or explanatory prose inside the command.\n"
            "- If a repo-local helper script matches the objective, invoke it with concrete flags instead of reimplementing its logic inline.\n"
            "- Keep the command to one direct invocation with concrete arguments.\n"
        )
