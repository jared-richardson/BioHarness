"""Retrieval-backed planner skill scoring for the orchestrator.

This module keeps semantic retrieval separate from the main orchestrator so the
planner can layer compact, model-conditioned retrieval boosts onto its
existing deterministic scoring logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from bio_harness.core.skill_retrieval import build_skill_retrieval_record, search_skill_records
from bio_harness.core.tool_cards import read_tool_card

_SMALL_MODEL_HINTS = (
    "gemma",
    "qwen3-coder-next",
    "qwen3next",
    "codellama",
    "starcoder",
    "deepseek-coder",
)
_MODEL_SIZE_RE = re.compile(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])")


@dataclass(frozen=True)
class PlannerSkillRetrievalProfile:
    """Retrieval profile for planner-skill selection."""

    name: str
    limit: int
    boost_weight: int
    protected_top_k: int


def planner_skill_retrieval_profile(model_name: str | None, *, budget: int) -> PlannerSkillRetrievalProfile:
    """Return a retrieval profile tuned to the planner model family.

    Args:
        model_name: Planner model identifier.
        budget: Current planner-skill budget.

    Returns:
        Retrieval profile controlling boost strength and shortlist size.
    """

    normalized = str(model_name or "").strip().lower()
    parameter_size = _parse_parameter_size_from_name(normalized)
    if any(hint in normalized for hint in _SMALL_MODEL_HINTS) or (parameter_size and parameter_size <= 32.0):
        return PlannerSkillRetrievalProfile(
            name="compact_model",
            limit=max(4, min(max(budget, 4), 8)),
            boost_weight=24,
            protected_top_k=3,
        )
    if parameter_size and parameter_size >= 70.0:
        return PlannerSkillRetrievalProfile(
            name="large_model",
            limit=max(3, min(max(budget, 3), 5)),
            boost_weight=10,
            protected_top_k=1,
        )
    return PlannerSkillRetrievalProfile(
        name="balanced_model",
        limit=max(4, min(max(budget, 4), 6)),
        boost_weight=16,
        protected_top_k=2,
    )


def planner_skill_retrieval_boosts(
    user_query: str,
    available_skills_metadata: Sequence[Mapping[str, Any]],
    *,
    analysis_spec: Mapping[str, Any] | None = None,
    model_name: str | None = None,
    budget: int,
    tool_cards_dir: str | Path | None = None,
) -> tuple[dict[str, int], set[str], dict[str, Any]]:
    """Build retrieval-based score boosts and protected names for planning.

    Args:
        user_query: User request text.
        available_skills_metadata: Available planner-skill metadata.
        analysis_spec: Optional deterministic analysis spec.
        model_name: Planner model name used for profile selection.
        budget: Current planner skill budget.
        tool_cards_dir: Optional tool-card directory used to enrich retrieval.

    Returns:
        Tuple of ``(boosts, protected_names, metadata)``.
    """

    skills = [dict(skill) for skill in available_skills_metadata if isinstance(skill, Mapping)]
    profile = planner_skill_retrieval_profile(model_name, budget=budget)
    query = _build_retrieval_query(user_query, analysis_spec=analysis_spec)
    if not query.strip() or not skills:
        return {}, set(), _render_empty_meta(profile)

    tool_cards_root = _normalize_tool_cards_dir(tool_cards_dir)
    records = [
        build_skill_retrieval_record(
            skill,
            tool_card=_load_tool_card(str(skill.get("name", "")), tool_cards_dir=tool_cards_root),
        )
        for skill in skills
    ]
    matches = search_skill_records(query, records, limit=min(profile.limit, len(records)))
    boosts = {
        str(match.name).strip().lower(): max(1, round(match.score * profile.boost_weight))
        for match in matches
        if match.score > 0
    }
    protected = {
        str(match.name).strip().lower()
        for match in matches[: profile.protected_top_k]
        if match.score > 0
    }
    metadata = {
        "retrieval_enabled": True,
        "retrieval_profile": profile.name,
        "retrieval_limit": profile.limit,
        "tool_cards_dir": str(tool_cards_root) if tool_cards_root is not None else "",
        "retrieval_selected_skill_names": [str(match.name).strip() for match in matches],
        "retrieval_protected_skill_names": sorted(protected),
        "retrieval_matches": [
            {
                "name": str(match.name).strip(),
                "score": float(match.score),
                "semantic_score": float(match.semantic_score),
                "lexical_score": float(match.lexical_score),
                "matched_terms": list(match.matched_terms),
            }
            for match in matches
        ],
    }
    return boosts, protected, metadata


def _build_retrieval_query(user_query: str, *, analysis_spec: Mapping[str, Any] | None = None) -> str:
    """Build a compact retrieval query from request and deterministic hints.

    Args:
        user_query: User request text.
        analysis_spec: Optional deterministic analysis spec.

    Returns:
        Compact retrieval query string.
    """

    parts = [str(user_query or "").strip()]
    if isinstance(analysis_spec, Mapping):
        for key in ("analysis_type", "chosen_method"):
            value = str(analysis_spec.get(key, "")).strip()
            if value:
                parts.append(value)
        for key in ("preferred_tools", "discouraged_tools"):
            values = analysis_spec.get(key, [])
            if isinstance(values, list):
                parts.extend(str(value).strip() for value in values if str(value).strip())
        grounding = analysis_spec.get("protocol_grounding", {})
        if isinstance(grounding, Mapping):
            for key in ("required_tools", "required_plan_signals"):
                values = grounding.get(key, [])
                if isinstance(values, list):
                    parts.extend(str(value).strip() for value in values if str(value).strip())
    return " ".join(part for part in parts if part)


def _parse_parameter_size_from_name(model_name: str) -> float:
    """Extract a rough parameter-count hint from a model name.

    Args:
        model_name: Raw model identifier.

    Returns:
        Approximate parameter-count hint in billions.
    """

    match = _MODEL_SIZE_RE.search(str(model_name or "").lower())
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def _render_empty_meta(profile: PlannerSkillRetrievalProfile) -> dict[str, Any]:
    """Return empty retrieval metadata when ranking is unavailable.

    Args:
        profile: Retrieval profile used for the current query.

    Returns:
        Empty retrieval metadata payload.
    """

    return {
        "retrieval_enabled": False,
        "retrieval_profile": profile.name,
        "retrieval_limit": profile.limit,
        "tool_cards_dir": "",
        "retrieval_selected_skill_names": [],
        "retrieval_protected_skill_names": [],
        "retrieval_matches": [],
    }


def _normalize_tool_cards_dir(tool_cards_dir: str | Path | None) -> Path | None:
    """Return a normalized tool-card directory when configured.

    Args:
        tool_cards_dir: Optional directory path.

    Returns:
        Resolved directory path when it exists, otherwise ``None``.
    """

    if tool_cards_dir is None:
        return None
    candidate = Path(tool_cards_dir).expanduser().resolve(strict=False)
    if not candidate.is_dir():
        return None
    return candidate


def _load_tool_card(name: str, *, tool_cards_dir: Path | None) -> object | None:
    """Load one persisted tool card when available.

    Args:
        name: Skill name.
        tool_cards_dir: Optional tool-card directory.

    Returns:
        Loaded tool card or ``None``.
    """

    if tool_cards_dir is None:
        return None
    normalized = str(name or "").strip()
    if not normalized:
        return None
    candidate = tool_cards_dir / f"{normalized}.json"
    if not candidate.is_file():
        return None
    try:
        return read_tool_card(candidate)
    except Exception:
        return None
