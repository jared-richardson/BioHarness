"""Public planning entrypoints for ``BioLLM``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from bio_harness.core.benchmark_policy import is_blind_bioagentbench_policy
from bio_harness.core.llm_backends import backend_host_env_var
from bio_harness.core.llm_types import BioHarnessError, LLMOutputSchema

logger = logging.getLogger(__name__)


def _load_registered_skill_names() -> frozenset[str]:
    """Return the full set of registered skill names at module import time.

    The stepwise planner ships only a retrieval-ranked subset of skills to the
    LLM to keep prompts small. The LLM, however, reasonably plans for skills
    that exist in the harness's registry but didn't make the subset budget
    (e.g. ``bash_run`` for a concat step, ``prokka_annotate`` for a putative
    annotation step). If we reject those plans on the grounds that the
    subset didn't include them, the planner re-proposes the same (valid)
    tool and we livelock.

    The rejection is only correct for names the harness cannot execute at
    all — i.e. names not in the registry. Reading the skill index once at
    import time gives us a cheap universe of valid names to fall back on.
    A missing or malformed index yields an empty set (the validator then
    behaves exactly as before).
    """

    index_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "definitions"
        / "index.json"
    )
    try:
        with index_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return frozenset()
    names: set[str] = set()
    for entry in payload.get("skills", []) if isinstance(payload, dict) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if name:
            names.add(name)
    return frozenset(names)


# Skills that are always part of the harness's registered catalog and should
# never be flagged as "unavailable" even when the stepwise retrieval subset
# omits them. Populated from ``bio_harness/skills/definitions/index.json`` at
# import time. ``bash_run`` is retained as an unconditional fallback in case
# the index is missing or truncated, because it is the universal shell
# executor and rejecting plans that reference it is the most common planner
# livelock mode.
_ALWAYS_AVAILABLE_SKILL_NAMES: frozenset[str] = (
    _load_registered_skill_names() | frozenset({"bash_run"})
)


class LLMEntrypointsMixin:
    """Provides the public planning and lightweight text-generation methods."""

    def _unknown_plan_tools(
        self,
        *,
        plan: dict[str, Any],
        available_skills: list[dict[str, Any]],
    ) -> list[str]:
        """Return tool names that are not present in the visible skill catalog."""

        allowed = {
            str(skill.get("name", "")).strip()
            for skill in (available_skills or [])
            if isinstance(skill, dict) and str(skill.get("name", "")).strip()
        }
        allowed.update(_ALWAYS_AVAILABLE_SKILL_NAMES)
        unknown: list[str] = []
        for step in plan.get("plan", []) if isinstance(plan, dict) else []:
            if not isinstance(step, dict):
                continue
            tool_name = str(step.get("tool_name", "")).strip()
            if not tool_name or tool_name in allowed or tool_name in unknown:
                continue
            unknown.append(tool_name)
        return unknown

    def think(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
        *,
        planner_mode: str = "auto",
        seed_plan: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        """Produce an executable plan for one user request."""

        max_retries = 3
        validation_error_message = ""
        two_stage_fallback_attempted = False
        planning_model = model_override or self.heavy_model_name

        skill_names = ", ".join([skill.get("name", "unknown") for skill in available_skills])
        formatted_skills_details = self._format_skills_for_prompt(available_skills)
        analysis_block = self._analysis_brief_block(analysis_spec)

        skeleton_anchor = ""
        if isinstance(analysis_spec, dict) and analysis_spec.get("plan_skeleton"):
            skeleton = analysis_spec["plan_skeleton"]
            skeleton_lines = []
            for idx, entry in enumerate(skeleton, 1):
                if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                    skeleton_lines.append(f"  {idx}. {entry[0]} ({entry[1]})")
                elif isinstance(entry, dict):
                    skeleton_lines.append(
                        f"  {idx}. {entry.get('tool_name', 'bash_run')} ({entry.get('purpose', '')})"
                    )
            if skeleton_lines:
                skeleton_anchor = (
                    "\nYOUR PLAN MUST include these tools in this logical stage order:\n"
                    + "\n".join(skeleton_lines)
                    + "\n"
                    + self._plan_skeleton_branching_guidance(analysis_spec)
                )

        system_prompt = f"""You are a Bioinformatics OS. Your goal is to construct executable pipelines.
You have access to these tools: {skill_names}.
Here are the detailed specifications for each tool:
{formatted_skills_details}

RULES:
1. Do NOT chat. Do NOT explain.
2. Output ONLY a valid JSON object matching the schema below.
3. Do NOT use runtime install/fetch commands (git/curl/wget/pip/conda/apt/brew).
4. Assume required bioinformatics tools are already provisioned in the environment.
5. If the user explicitly asks for a tool or aligner, honor that request in the plan.
6. If the analysis brief specifies a chosen method, use that method unless it is unavailable or impossible for the request.
7. Prefer tools listed in the analysis brief and avoid discouraged tools unless there is no viable alternative.
8. If alternative splicing is requested, include at least one dedicated splicing tool step such as `rmats_run`, `dexseq_run`, or `majiq_run`.
9. Each `bash_run` step must perform exactly one logical operation. If the workflow needs multiple operations, emit multiple steps.
10. Prefer typed wrappers over `bash_run` whenever a wrapper exists. Available wrapper examples in this tool set: {self._atomic_wrapper_examples(available_skills)}.
11. Keep `thought_process` to one short sentence and keep the plan compact (prefer 10 or fewer steps).
12. If the analysis brief includes protocol-required tools, signals, or rules from a task-local recipe, satisfy them explicitly in the executable plan.
13. If the request names final published outputs that are not documented tool parameters, record them in `final_deliverables` instead of inventing undocumented arguments like `output_file` or `final_csv`.

DO NOT:
- DO NOT invent tool names not listed above.
- DO NOT use parameters not documented for each tool.
- DO NOT turn requested final deliverable filenames into undocumented tool arguments.
- DO NOT skip required tools from the analysis brief.
- DO NOT output anything other than a JSON object.

JSON SCHEMA AND EXAMPLE:
{{
  "thought_process": "One short sentence.",
  "final_deliverables": ["/path/to/final/result.csv"],
  "plan": [
    {{"tool_name": "fastqc_run", "arguments": {{"input_dir": "/path/to/reads", "output_dir": "/path/to/output"}}, "step_id": 1}},
    {{"tool_name": "bash_run", "arguments": {{"command": "samtools sort -o sorted.bam input.bam"}}, "step_id": 2}}
  ]
}}
{skeleton_anchor}
{analysis_block}
"""

        use_two_stage = self._should_use_two_stage(user_query, available_skills)
        use_hierarchical = self._hierarchical_mode_enabled(
            planner_mode=planner_mode,
            user_query=user_query,
            analysis_spec=analysis_spec,
        )
        if planning_model != self.model_name:
            logger.info("Dual-model: planning with %s (fast=%s)", planning_model, self.model_name)
        self._planner_trace(
            "PLANNER_START",
            {
                "use_two_stage": bool(use_two_stage),
                "use_hierarchical": bool(use_hierarchical),
                "skill_count": len(available_skills),
                "user_query_chars": len(str(user_query or "")),
                "planner_prompt_style": self.planner_prompt_style,
                "planning_model": planning_model,
                "fast_model": self.model_name,
            },
        )

        strict_two_stage_primary = bool(
            use_two_stage and is_blind_bioagentbench_policy((analysis_spec or {}).get("benchmark_policy"))
        )
        direct_plan_repair_allowed = not use_two_stage

        if use_two_stage and (self.two_stage_mode == "always" or strict_two_stage_primary):
            primary_reason = "planner_two_stage_mode_always" if self.two_stage_mode == "always" else "planning_strict_auto"
            self._planner_trace("TWO_STAGE_PRIMARY", {"reason": primary_reason})
            try:
                plan = self._think_two_stage(user_query, available_skills, analysis_spec, model_override=planning_model)
                unknown_tools = self._unknown_plan_tools(plan=plan, available_skills=available_skills)
                if unknown_tools:
                    raise ValueError(f"Plan used unavailable tools: {', '.join(unknown_tools)}")
                return plan
            except Exception as exc:
                if self._is_supervisor_timeout_error(exc):
                    raise
                validation_error_message = f"Two-stage planning failed: {exc}"
                logger.warning(validation_error_message)

        if use_hierarchical:
            try:
                plan = self._think_hierarchical(
                    user_query,
                    available_skills,
                    analysis_spec=analysis_spec,
                    seed_plan=seed_plan,
                    model_override=planning_model,
                )
                unknown_tools = self._unknown_plan_tools(plan=plan, available_skills=available_skills)
                if unknown_tools:
                    raise ValueError(f"Plan used unavailable tools: {', '.join(unknown_tools)}")
                return plan
            except Exception as exc:
                if self._is_supervisor_timeout_error(exc):
                    raise
                validation_error_message = f"Hierarchical planning failed: {exc}"
                logger.warning(validation_error_message)

        for attempt_idx in range(max_retries):
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ]
            if validation_error_message:
                messages.append({"role": "assistant", "content": "JSON error feedback:"})
                messages.append({"role": "user", "content": validation_error_message})

            try:
                predict_budget = self._direct_plan_predict_budget(
                    attempt_idx=attempt_idx,
                    user_query=user_query,
                    available_skills=available_skills,
                    analysis_spec=analysis_spec,
                )
                llm_output_data = self._request_structured_response(
                    stage="direct_plan",
                    schema_model=LLMOutputSchema,
                    messages=messages,
                    num_predict=predict_budget,
                    normalizer=self._normalize_plan_output,
                    repair_allowed=direct_plan_repair_allowed,
                    repair_reason=validation_error_message,
                    model_override=planning_model,
                )
                validated_output = LLMOutputSchema(**llm_output_data)
                unknown_tools = self._unknown_plan_tools(
                    plan=validated_output.model_dump(),
                    available_skills=available_skills,
                )
                if unknown_tools:
                    validation_error_message = f"Plan used unavailable tools: {', '.join(unknown_tools)}"
                    logger.warning(validation_error_message)
                    fallback_plan, two_stage_fallback_attempted, validation_error_message = self._two_stage_fallback_from_direct_failure(
                        use_two_stage=use_two_stage,
                        already_attempted=two_stage_fallback_attempted,
                        attempt_idx=attempt_idx,
                        reason=validation_error_message,
                        user_query=user_query,
                        available_skills=available_skills,
                        analysis_spec=analysis_spec,
                        planning_model=planning_model,
                    )
                    if isinstance(fallback_plan, dict):
                        fallback_unknown_tools = self._unknown_plan_tools(
                            plan=fallback_plan,
                            available_skills=available_skills,
                        )
                        if not fallback_unknown_tools:
                            return fallback_plan
                        validation_error_message = (
                            f"Plan used unavailable tools: {', '.join(fallback_unknown_tools)}"
                        )
                    continue
                return validated_output.model_dump()
            except json.JSONDecodeError as exc:  # type: ignore[name-defined]
                validation_error_message = f"Invalid JSON received: {exc}"
                logger.warning(validation_error_message)
                fallback_plan, two_stage_fallback_attempted, validation_error_message = self._two_stage_fallback_from_direct_failure(
                    use_two_stage=use_two_stage,
                    already_attempted=two_stage_fallback_attempted,
                    attempt_idx=attempt_idx,
                    reason=validation_error_message,
                    user_query=user_query,
                    available_skills=available_skills,
                    analysis_spec=analysis_spec,
                    planning_model=planning_model,
                )
                if isinstance(fallback_plan, dict):
                    fallback_unknown_tools = self._unknown_plan_tools(
                        plan=fallback_plan,
                        available_skills=available_skills,
                    )
                    if not fallback_unknown_tools:
                        return fallback_plan
                    validation_error_message = f"Plan used unavailable tools: {', '.join(fallback_unknown_tools)}"
                continue
            except ValidationError as exc:  # type: ignore[name-defined]
                validation_error_message = f"Schema validation failed: {exc}"
                logger.warning(validation_error_message)
                fallback_plan, two_stage_fallback_attempted, validation_error_message = self._two_stage_fallback_from_direct_failure(
                    use_two_stage=use_two_stage,
                    already_attempted=two_stage_fallback_attempted,
                    attempt_idx=attempt_idx,
                    reason=validation_error_message,
                    user_query=user_query,
                    available_skills=available_skills,
                    analysis_spec=analysis_spec,
                    planning_model=planning_model,
                )
                if isinstance(fallback_plan, dict):
                    fallback_unknown_tools = self._unknown_plan_tools(
                        plan=fallback_plan,
                        available_skills=available_skills,
                    )
                    if not fallback_unknown_tools:
                        return fallback_plan
                    validation_error_message = f"Plan used unavailable tools: {', '.join(fallback_unknown_tools)}"
                continue
            except Exception as exc:
                error_message = str(exc)
                if self._is_supervisor_timeout_error(exc):
                    raise
                if use_two_stage and (
                    isinstance(exc, httpx.ReadTimeout)
                    or "timed out" in error_message.lower()
                    or "Invalid JSON received" in validation_error_message
                    or "Schema validation failed" in validation_error_message
                ):
                    self._planner_trace(
                        "TWO_STAGE_FALLBACK",
                        {
                            "attempt": int(attempt_idx + 1),
                            "reason": error_message or validation_error_message or "direct_plan_failure",
                        },
                    )
                    try:
                        return self._think_two_stage(user_query, available_skills, analysis_spec, model_override=planning_model)
                    except Exception as outline_exc:
                        validation_error_message = f"Two-stage planning failed: {outline_exc}"
                        logger.warning(validation_error_message)
                        continue
                if self._backend.is_connectivity_error(exc):
                    if self._is_loopback_blocked_error(exc):
                        raise BioHarnessError(
                            f"Local loopback access to {self.backend_label} at {self.host} is blocked by the current runtime. "
                            "Grant localhost network permission or run the harness outside the sandbox."
                        ) from exc
                    if self.backend_name == "ollama":
                        raise BioHarnessError(
                            "Is Ollama running? Try 'ollama serve'. "
                            f"Connection error: {exc}"
                        ) from exc
                    raise BioHarnessError(
                        f"Is the {self.backend_label} reachable? "
                        f"Check {backend_host_env_var(self.backend_name)} ({self.host}). "
                        f"Connection error: {exc}"
                    ) from exc
                if isinstance(exc, httpx.ReadTimeout) or "timed out" in error_message.lower():
                    raise BioHarnessError(
                        "Planner request timed out while waiting for model output "
                        f"({int(self.request_timeout_seconds)}s timeout). "
                        "Try again, simplify the request, or increase BIO_HARNESS_LLM_TIMEOUT_SECONDS."
                    ) from exc
                if f"model '{self.model_name}' not found" in error_message or "not found" in error_message:
                    if self.backend_name == "ollama":
                        raise BioHarnessError(
                            f"Model '{self.model_name}' not found. "
                            f"Please pull it first: ollama pull {self.model_name}."
                        ) from exc
                    raise BioHarnessError(
                        f"Model '{self.model_name}' not found on {self.backend_label} at {self.host}."
                    ) from exc
                raise

        raise BioHarnessError(
            "LLM failed to produce valid JSON output after retries. "
            f"Last validation error: {validation_error_message}"
        )

    def summarize_text(self, text: str, instruction: str) -> str:
        """Run a general-purpose summarization prompt against the configured model."""

        prompt = (
            "You are a bioinformatics assistant. "
            "Provide a concise, technically correct response in markdown.\n\n"
            f"Task: {instruction}\n\n"
            f"Input:\n{text[:120000]}"
        )
        return self._backend.chat(
            model_name=self.model_name,
            messages=[
                {"role": "system", "content": "Return direct, factual output."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            num_ctx=8192,
            num_predict=900,
        )

    def generate_text(self, system_prompt: str, user_prompt: str, num_ctx: int = 8192) -> str:
        """Run a general chat completion for interactive orchestrator conversations."""

        return self._backend.chat(
            model_name=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            num_ctx=num_ctx,
            num_predict=1200,
        )
