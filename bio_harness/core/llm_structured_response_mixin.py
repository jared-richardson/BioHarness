"""Structured-response and two-stage planning helpers for ``BioLLM``."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from bio_harness.core.benchmark_policy import is_bioagentbench_planning_strict_policy
from bio_harness.core.llm_types import AbstractPlanSchema, LLMOutputSchema

logger = logging.getLogger(__name__)

_FENCED_BASH_BLOCK_RE = re.compile(r"```(?:bash|sh|shell)?\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def _deterministic_json_repair(raw_text: str) -> dict[str, Any] | None:
    """Best-effort deterministic JSON repair for truncated/mildly-malformed LLM output.

    Tries three cheap strategies in order before giving up and falling back to
    an LLM-driven repair round:

      1. Direct ``json.loads`` of the trimmed text.
      2. ``JSONDecoder.raw_decode`` to extract the longest valid JSON prefix;
         useful when the model appended commentary or a duplicate closing
         brace after a valid object.
      3. Bracket/brace balancing with trailing-comma removal — handles the
         common ``"...,\n}`` pattern and the case where the response is cut
         off mid-array.

    Returns a parsed dict on success, or ``None`` when no strategy recovers a
    JSON object. Never raises for malformed input.
    """

    if not isinstance(raw_text, str):
        return None
    text = raw_text.strip()
    if not text:
        return None
    # Strategy 1: direct parse.
    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    # Strategy 2: longest valid prefix via raw_decode.
    start = text.find("{")
    if start >= 0:
        decoder = json.JSONDecoder()
        try:
            prefix_parsed, _ = decoder.raw_decode(text[start:])
        except Exception:
            prefix_parsed = None
        if isinstance(prefix_parsed, dict):
            return prefix_parsed

    # Strategy 3: bracket balance + trailing-comma cleanup. We scan forward
    # from the first ``{`` tracking string state so we do not mis-count braces
    # inside strings. We keep the substring up to the last position where the
    # structure could be validly closed, then strip trailing commas and
    # append the balancing close brackets.
    if start < 0:
        return None
    stack: list[str] = []
    in_string = False
    string_quote = ""
    escape = False
    last_valid_idx = -1
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == string_quote:
                in_string = False
            continue
        if ch in ('"', "'"):
            in_string = True
            string_quote = ch
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
            continue
        if ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    last_valid_idx = idx
                    # Reached a balanced end; try to parse what we have so far.
                    try:
                        candidate = json.loads(text[start : idx + 1])
                    except Exception:
                        candidate = None
                    if isinstance(candidate, dict):
                        return candidate
            else:
                # Mismatched closing bracket — stop scanning and try to repair
                # what came before.
                break
    # No balanced close found — try to balance what we have. Drop any
    # trailing comma just before an implicit close.
    if stack:
        # Find the last position where content was potentially complete: last
        # quote close, bracket close, or end of a primitive value.
        # As a simple heuristic, use the last position that was not inside a
        # string, then strip trailing whitespace/comma, then append the
        # remaining closers.
        scan_end = len(text)
        # Back off from the end while we are inside a string.
        truncated = text[start:scan_end]
        # Remove any trailing comma followed by whitespace.
        stripped = re.sub(r",\s*$", "", truncated)
        # Also strip a dangling ``"key":`` that has no value (unclosed pair).
        stripped = re.sub(r'(?:,\s*)?"[^"]*"\s*:\s*$', "", stripped)
        candidate_text = stripped + "".join(reversed(stack))
        try:
            candidate = json.loads(candidate_text)
        except Exception:
            candidate = None
        if isinstance(candidate, dict):
            return candidate
    return None


class LLMStructuredResponseMixin:
    """Provides JSON schema request, repair, and two-stage planner helpers."""

    def _extract_fenced_bash_script(self, raw_content: str) -> str:
        """Return the first fenced shell block from a raw model reply."""

        match = _FENCED_BASH_BLOCK_RE.search(str(raw_content or ""))
        if not match:
            return ""
        return str(match.group(1) or "").strip()

    def _salvage_step_expansion_candidate(
        self,
        *,
        raw_content: str,
        schema_model: type[BaseModel],
        normalizer: Any = None,
    ) -> dict[str, Any] | None:
        """Salvage step-expansion replies that contain fenced bash instead of JSON."""

        if not callable(normalizer):
            return None
        command = self._extract_fenced_bash_script(raw_content)
        if not command:
            return None
        candidate = {"command": command}
        try:
            normalized = normalizer(candidate)
            schema_model(**normalized)
        except Exception:
            return None
        self._planner_trace(
            "PARSE_SALVAGE",
            {"stage": "step_expansion", "salvage_kind": "fenced_bash"},
            raw_content=command,
        )
        return candidate

    def _should_use_two_stage(self, user_query: str, available_skills: list[dict[str, Any]]) -> bool:
        if self.two_stage_mode == "off":
            return False
        if self.two_stage_mode == "always":
            return True
        query_len = len(str(user_query or ""))
        skill_count = len(available_skills or [])
        return query_len >= 280 or skill_count >= 7

    def _direct_plan_predict_budget(
        self,
        *,
        attempt_idx: int,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
    ) -> int:
        """Return a generation budget for direct structured planning."""

        spec = analysis_spec if isinstance(analysis_spec, dict) else {}
        skeleton_len = len(spec.get("plan_skeleton", []) or [])
        analysis_type = str(spec.get("analysis_type", "") or "").strip()
        strict_policy = is_bioagentbench_planning_strict_policy(spec.get("benchmark_policy"))
        query_len = len(str(user_query or ""))
        skill_count = len(available_skills or [])

        base_predict = self.default_num_predict
        if skill_count >= 10 or (analysis_type not in ("generic_analysis", "") and skeleton_len >= 4):
            base_predict = max(base_predict, 3200)
        if strict_policy and (query_len >= 900 or skeleton_len >= 6):
            base_predict = max(base_predict, 4600)
        if strict_policy and skeleton_len >= 8:
            base_predict = max(base_predict, 5200)

        cap = 6400 if strict_policy else 4096
        return min(cap, int(base_predict + (attempt_idx * 600)))

    def _repair_predict_budget(
        self,
        *,
        stage: str,
        raw_content: str,
        failure_message: str,
    ) -> int:
        """Return a generation budget for JSON repair attempts."""

        budget = max(512, min(1800, int(self.default_num_predict)))
        text = f"{failure_message}\n{raw_content[:2048]}".lower()
        if (
            len(raw_content) >= 8000
            or "invalid json" in text
            or "expecting ',' delimiter" in text
            or "unterminated" in text
        ):
            budget = max(budget, 3200)
        if stage == "direct_plan":
            budget = max(budget, 3600)
        return min(4800, budget)

    def _plan_expansion_predict_budget(
        self,
        *,
        outline: dict[str, Any],
        analysis_spec: dict[str, Any] | None = None,
        user_query: str = "",
    ) -> int:
        """Return a generation budget for two-stage plan expansion.

        Args:
            outline: Structured abstract outline emitted by the first stage.
            analysis_spec: Optional analysis brief.
            user_query: Original user prompt text.

        Returns:
            Token budget for the expansion stage.
        """

        budget = max(900, min(2600, int(self.default_num_predict)))
        spec = analysis_spec if isinstance(analysis_spec, dict) else {}
        outline_steps = len(outline.get("plan_outline", []) or []) if isinstance(outline, dict) else 0
        query_len = len(str(user_query or ""))
        protocol_grounding = (
            spec.get("protocol_grounding", {})
            if isinstance(spec.get("protocol_grounding", {}), dict)
            else {}
        )
        strict_policy = is_bioagentbench_planning_strict_policy(spec.get("benchmark_policy"))

        if outline_steps >= 6:
            budget = max(budget, min(3600, 1200 + (outline_steps * 260)))
        if query_len >= 1200:
            budget = max(budget, min(4000, budget + 400))
        if bool(protocol_grounding.get("grounded", False)):
            budget = max(budget, min(4200, 1600 + (outline_steps * 280)))
        if strict_policy and outline_steps >= 8:
            budget = max(budget, 4200)
        return min(4800, int(budget))

    def _request_structured_response(
        self,
        *,
        stage: str,
        schema_model: type[BaseModel],
        messages: list[Any],
        num_predict: int,
        normalizer: Any = None,
        repair_allowed: bool = True,
        repair_reason: str = "",
        model_override: str | None = None,
    ) -> dict[str, Any]:
        response_meta = self._chat_json_raw(
            messages,
            num_predict=num_predict,
            format_spec=schema_model.model_json_schema(),
            model_override=model_override,
        )
        raw_content = str(response_meta.get("raw_content", "") or "")
        transport = str(response_meta.get("transport", self.transport_name) or self.transport_name)
        self._planner_trace(
            "RAW_RESPONSE",
            {
                "stage": stage,
                "transport": transport,
                "num_predict": int(num_predict),
                "message_count": len(messages),
                "repair_allowed": bool(repair_allowed),
                "repair_reason": repair_reason,
            },
            raw_content=raw_content,
        )

        parsed: dict[str, Any] | None = None
        parse_error = ""
        candidate_text = self._extract_json_candidate(raw_content)
        try:
            parsed = json.loads(candidate_text)
        except Exception as exc:
            parse_error = str(exc)
            if stage == "step_expansion":
                parsed = self._salvage_step_expansion_candidate(
                    raw_content=raw_content,
                    schema_model=schema_model,
                    normalizer=normalizer,
                )

        # Deterministic repair before incurring an LLM round-trip. This handles
        # common truncation/trailing-garbage failures (planner livelock where the
        # model repeatedly emits the same malformed JSON and the LLM-driven
        # repair also fails). Cheap and bounded — safe to always try.
        if parsed is None:
            deterministic = _deterministic_json_repair(raw_content)
            if deterministic is None and candidate_text and candidate_text != raw_content:
                deterministic = _deterministic_json_repair(candidate_text)
            if deterministic is not None:
                self._planner_trace(
                    "DETERMINISTIC_REPAIR",
                    {"stage": stage, "failure_message": parse_error},
                    raw_content=json.dumps(deterministic, ensure_ascii=True, indent=2),
                )
                parsed = deterministic

        if parsed is None and repair_allowed:
            repaired = self._repair_structured_response(
                stage=stage,
                schema_model=schema_model,
                raw_content=raw_content,
                failure_message=parse_error or repair_reason or "Invalid JSON response.",
            )
            if repaired is not None:
                parsed = repaired

        if parsed is None:
            self._planner_trace(
                "PARSE_FAILURE",
                {
                    "stage": stage,
                    "transport": transport,
                    "error": parse_error or repair_reason or "Unable to parse response as JSON.",
                },
                raw_content=raw_content,
            )
            raise json.JSONDecodeError(parse_error or "Invalid JSON response.", candidate_text, 0)

        payload = normalizer(parsed) if callable(normalizer) else parsed
        try:
            validated = schema_model(**payload)
        except ValidationError as exc:
            if repair_allowed:
                repaired = self._repair_structured_response(
                    stage=stage,
                    schema_model=schema_model,
                    raw_content=json.dumps(parsed, ensure_ascii=True, indent=2),
                    failure_message=f"Schema validation failed: {exc}",
                )
                if repaired is not None:
                    repaired_payload = normalizer(repaired) if callable(normalizer) else repaired
                    validated = schema_model(**repaired_payload)
                else:
                    self._planner_trace(
                        "VALIDATION_FAILURE",
                        {"stage": stage, "error": str(exc)},
                        raw_content=json.dumps(payload, ensure_ascii=True, indent=2),
                    )
                    raise
            else:
                self._planner_trace(
                    "VALIDATION_FAILURE",
                    {"stage": stage, "error": str(exc)},
                    raw_content=json.dumps(payload, ensure_ascii=True, indent=2),
                )
                raise

        model_dump = validated.model_dump()
        self._planner_trace(
            "STRUCTURED_SUCCESS",
            {
                "stage": stage,
                "item_count": len(model_dump.get("plan", model_dump.get("plan_outline", []))),
            },
            raw_content=json.dumps(model_dump, ensure_ascii=True, indent=2),
        )
        return model_dump

    def _repair_structured_response(
        self,
        *,
        stage: str,
        schema_model: type[BaseModel],
        raw_content: str,
        failure_message: str,
    ) -> dict[str, Any] | None:
        repair_messages = [
            {
                "role": "system",
                "content": (
                    "Repair the assistant output into valid JSON that matches the provided schema. "
                    "Return JSON only. Preserve tool choices and arguments when possible."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Schema:\n{json.dumps(schema_model.model_json_schema(), ensure_ascii=True)}\n\n"
                    f"Failure:\n{failure_message}\n\n"
                    f"Broken output:\n{raw_content[:32000]}"
                ),
            },
        ]
        try:
            repaired = self._chat_json_raw(
                repair_messages,
                num_predict=self._repair_predict_budget(
                    stage=stage,
                    raw_content=raw_content,
                    failure_message=failure_message,
                ),
                format_spec=schema_model.model_json_schema(),
            )
        except Exception as exc:
            if self._is_supervisor_timeout_error(exc):
                raise
            self._planner_trace("REPAIR_FAILURE", {"stage": stage, "error": str(exc)}, raw_content=raw_content)
            return None
        repaired_text = str(repaired.get("raw_content", "") or "")
        self._planner_trace(
            "REPAIR_RESPONSE",
            {
                "stage": stage,
                "failure_message": failure_message,
                "transport": str(repaired.get("transport", self.transport_name) or self.transport_name),
            },
            raw_content=repaired_text,
        )
        candidate_text = self._extract_json_candidate(repaired_text)
        try:
            return json.loads(candidate_text)
        except Exception as exc:
            self._planner_trace("REPAIR_FAILURE", {"stage": stage, "error": str(exc)}, raw_content=repaired_text)
            return None

    def _think_two_stage(
        self,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None = None,
        model_override: str | None = None,
    ) -> dict[str, Any]:
        outline = self._request_structured_response(
            stage="abstract_outline",
            schema_model=AbstractPlanSchema,
            messages=self._build_outline_messages(user_query, available_skills, analysis_spec),
            num_predict=max(500, min(1200, int(self.default_num_predict * 0.55))),
            normalizer=self._normalize_outline_output,
            model_override=model_override,
        )
        return self._request_structured_response(
            stage="plan_expansion",
            schema_model=LLMOutputSchema,
            messages=self._build_expansion_messages(user_query, available_skills, outline, analysis_spec),
            num_predict=self._plan_expansion_predict_budget(
                outline=outline,
                analysis_spec=analysis_spec,
                user_query=user_query,
            ),
            normalizer=self._normalize_plan_output,
            model_override=model_override,
        )

    def _two_stage_fallback_from_direct_failure(
        self,
        *,
        use_two_stage: bool,
        already_attempted: bool,
        attempt_idx: int,
        reason: str,
        user_query: str,
        available_skills: list[dict[str, Any]],
        analysis_spec: dict[str, Any] | None,
        planning_model: str | None,
    ) -> tuple[dict[str, Any] | None, bool, str]:
        """Try a two-stage fallback after direct-plan structured-output failure."""

        if not use_two_stage or already_attempted:
            return None, already_attempted, reason
        self._planner_trace(
            "TWO_STAGE_FALLBACK",
            {"attempt": int(attempt_idx + 1), "reason": reason or "direct_plan_structured_failure"},
        )
        try:
            plan = self._think_two_stage(
                user_query,
                available_skills,
                analysis_spec,
                model_override=planning_model,
            )
            return plan, True, reason
        except Exception as exc:
            if self._is_supervisor_timeout_error(exc):
                raise
            fallback_error = f"Two-stage planning failed: {exc}"
            logger.warning(fallback_error)
            return None, True, fallback_error
