"""Deterministic policy for planner-time literature assistance.

This module decides when planner-time literature assistance is allowed and what
query class should be used. The policy is intentionally narrow and benchmark
safe: assistance is advisory-only, disabled for blind benchmark modes, and only
triggered for prompts that explicitly ask for literature, published methods, or
best-practice style guidance.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from bio_harness.core.benchmark_policy import is_blind_bioagentbench_policy

_PARAMETER_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("preset", ("preset", "mode")),
    ("resolution", ("resolution", "cluster resolution")),
    ("minimum_support", ("minimum support", "min support", "support threshold")),
    ("normalization_method", ("normalization", "normalisation")),
    ("missing_value_policy", ("missing value", "imputation")),
    ("alpha", ("alpha", "fdr", "false discovery rate")),
)
_LITERATURE_TRIGGER_TERMS = (
    "published methods",
    "best practice",
    "best practices",
    "recommended",
    "recommendation",
    "literature",
    "best workflow",
    "recommended workflow",
    "best protocol",
    "recommended protocol",
    "standard steps",
)
_PARAMETER_REQUEST_TERMS = (
    "recommended",
    "recommendation",
    "what should",
    "what is the recommended",
    "which",
    "best",
    "default",
    "suggested",
    "published methods",
    "best practice",
    "best practices",
)
_PATHLIKE_PATTERN = re.compile(
    r"(?:https?://\S+)|(?:file://\S+)|(?:/[^\s]+(?:/[^\s]+)*)|(?:[A-Za-z]:\\[^\s]+(?:\\[^\s]+)*)"
)


@dataclass(frozen=True)
class LiteraturePlanningDecision:
    """Immutable planner-time literature decision.

    Attributes:
        allowed: Whether assistance should run.
        query_class: Deterministic query class label.
        trigger_reason: Stable reason for the decision.
        advisory_only: Whether the result is advisory-only.
        tool_name: Tool name to anchor parameter assistance, when known.
        parameter_name: Parameter name to anchor parameter assistance, when known.
    """

    allowed: bool
    query_class: str = ""
    trigger_reason: str = ""
    advisory_only: bool = True
    tool_name: str = ""
    parameter_name: str = ""


def decide_literature_planning_support(
    user_query: str,
    analysis_spec: dict[str, Any] | None,
    *,
    benchmark_policy: str,
) -> LiteraturePlanningDecision:
    """Return whether planner-time literature assistance is allowed.

    Args:
        user_query: Raw planner request.
        analysis_spec: Current analysis-spec payload.
        benchmark_policy: Active benchmark policy string.

    Returns:
        Deterministic planner-time literature decision.
    """

    prompt = str(user_query or "").strip()
    if not prompt:
        return LiteraturePlanningDecision(allowed=False, trigger_reason="empty_prompt")
    if is_blind_bioagentbench_policy(benchmark_policy):
        return LiteraturePlanningDecision(allowed=False, trigger_reason="blind_benchmark_policy")

    prompt_l = prompt.lower()
    if prompt_l.startswith("research:"):
        return LiteraturePlanningDecision(allowed=False, trigger_reason="explicit_research_mode")
    policy_text = _policy_text(prompt_l)

    tools = _tool_candidates(analysis_spec)
    parameter_name = _parameter_name_from_prompt(policy_text)
    if parameter_name:
        tool_name = tools[0] if tools else ""
        return LiteraturePlanningDecision(
            allowed=True,
            query_class="parameter_recommendation",
            trigger_reason=f"parameter_question:{parameter_name}",
            advisory_only=True,
            tool_name=tool_name,
            parameter_name=parameter_name,
        )

    if any(term in policy_text for term in _LITERATURE_TRIGGER_TERMS):
        return LiteraturePlanningDecision(
            allowed=True,
            query_class="protocol_choice",
            trigger_reason="explicit_best_practice_request",
            advisory_only=True,
            tool_name=tools[0] if tools else "",
            parameter_name="",
        )

    return LiteraturePlanningDecision(allowed=False, trigger_reason="no_literature_trigger")


def tool_candidates_from_analysis_spec(analysis_spec: dict[str, Any] | None) -> tuple[str, ...]:
    """Return deterministic tool candidates from an analysis spec.

    Args:
        analysis_spec: Current analysis-spec payload.

    Returns:
        Ordered tuple of distinct tool-like names.
    """

    return _tool_candidates(analysis_spec)


def _tool_candidates(analysis_spec: dict[str, Any] | None) -> tuple[str, ...]:
    spec = analysis_spec if isinstance(analysis_spec, dict) else {}
    preferred = [
        str(item).strip()
        for item in (spec.get("preferred_tools", []) or [])
        if str(item).strip()
    ]
    chosen_method = str(spec.get("chosen_method", "") or "").strip()
    chosen_tokens = [
        token.strip()
        for token in re.split(r"\s*\+\s*|\s*,\s*", chosen_method)
        if token.strip() and re.search(r"[A-Za-z0-9]", token)
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for tool in preferred + chosen_tokens:
        normalized = str(tool).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return tuple(ordered)


def _parameter_name_from_prompt(prompt_l: str) -> str:
    if not any(term in prompt_l for term in _PARAMETER_REQUEST_TERMS):
        return ""
    for parameter_name, markers in _PARAMETER_HINTS:
        if any(_contains_marker(prompt_l, marker) for marker in markers):
            return parameter_name
    return ""


def _policy_text(prompt_l: str) -> str:
    sanitized = _PATHLIKE_PATTERN.sub(" ", prompt_l)
    collapsed = re.sub(r"[^a-z0-9]+", " ", sanitized)
    return re.sub(r"\s+", " ", collapsed).strip()


def _contains_marker(prompt_l: str, marker: str) -> bool:
    """Return whether a sanitized prompt contains a marker as a word/phrase."""

    token = re.escape(str(marker or "").strip().lower())
    if not token:
        return False
    pattern = rf"(?<![a-z0-9]){token}(?![a-z0-9])"
    return re.search(pattern, prompt_l) is not None


__all__ = [
    "LiteraturePlanningDecision",
    "decide_literature_planning_support",
    "tool_candidates_from_analysis_spec",
]
