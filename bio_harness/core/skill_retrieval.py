"""Deterministic hybrid retrieval for skill metadata and tool cards.

This module provides a local-first retrieval layer for Bio-Harness skills. It
combines weighted token overlap with cosine-style similarity over normalized
metadata terms so the registry can rank skills even when the query does not
exactly match existing keywords.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    from bio_harness.core.tool_cards import ToolCard

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FIELD_WEIGHTS = {
    "name": 5.0,
    "tools_required": 4.0,
    "capabilities": 4.0,
    "analysis_categories": 3.5,
    "description": 3.0,
    "when_to_use": 2.5,
    "when_not_to_use": 1.5,
    "input_types": 2.0,
    "output_types": 2.0,
    "canonical_output_filenames": 2.5,
    "parameters": 1.0,
    "tool_card_when_to_use": 2.5,
    "tool_card_outputs": 2.5,
    "tool_card_safe_example": 1.5,
    "tool_card_errors": 1.0,
}


@dataclass(frozen=True)
class SkillRetrievalRecord:
    """Weighted retrieval document for one skill."""

    name: str
    file_path: str
    term_weights: dict[str, float]
    lexical_terms: tuple[str, ...]
    source_fields: tuple[str, ...]


@dataclass(frozen=True)
class SkillSearchMatch:
    """One ranked skill-search result."""

    name: str
    file_path: str
    score: float
    lexical_score: float
    semantic_score: float
    matched_terms: tuple[str, ...]


def build_skill_retrieval_record(
    skill: Mapping[str, Any],
    *,
    tool_card: "ToolCard | None" = None,
) -> SkillRetrievalRecord:
    """Build one weighted retrieval record from skill metadata.

    Args:
        skill: Skill metadata mapping.
        tool_card: Optional tool-card enrichment.

    Returns:
        Weighted retrieval record.
    """

    name = str(skill.get("name", "")).strip()
    file_path = str(skill.get("file_path", "")).strip()
    term_weights: dict[str, float] = {}
    source_fields: list[str] = []

    def _add(field: str, values: Any) -> None:
        weight = float(_FIELD_WEIGHTS.get(field, 1.0))
        tokens = _field_tokens(values)
        if not tokens:
            return
        source_fields.append(field)
        for token in tokens:
            term_weights[token] = term_weights.get(token, 0.0) + weight

    _add("name", name)
    _add("tools_required", skill.get("tools_required", []))
    _add("capabilities", skill.get("capabilities", []))
    _add("analysis_categories", skill.get("analysis_categories", []))
    _add("description", skill.get("description", ""))
    _add("when_to_use", skill.get("when_to_use", ""))
    _add("when_not_to_use", skill.get("when_not_to_use", ""))
    _add("input_types", skill.get("input_types", []))
    _add("output_types", skill.get("output_types", []))
    _add("canonical_output_filenames", skill.get("canonical_output_filenames", {}))
    _add("parameters", list(_parameter_names(skill.get("parameters", {}))))

    if tool_card is not None:
        _add("tool_card_when_to_use", tool_card.when_to_use)
        _add("tool_card_outputs", tool_card.canonical_outputs)
        _add("tool_card_safe_example", tool_card.safe_example)
        _add(
            "tool_card_errors",
            [entry.get("pattern", "") for entry in tool_card.common_errors],
        )

    lexical_terms = tuple(sorted(term_weights))
    return SkillRetrievalRecord(
        name=name,
        file_path=file_path,
        term_weights=term_weights,
        lexical_terms=lexical_terms,
        source_fields=tuple(dict.fromkeys(source_fields)),
    )


def search_skill_records(
    query: str,
    records: Sequence[SkillRetrievalRecord],
    *,
    limit: int = 5,
) -> tuple[SkillSearchMatch, ...]:
    """Search weighted skill records with a hybrid lexical/semantic score.

    Args:
        query: Natural-language query.
        records: Retrieval records to score.
        limit: Maximum number of matches to return.

    Returns:
        Ranked search results.
    """

    query_terms = _query_weights(query)
    if not query_terms:
        return ()
    query_tokens = set(query_terms)

    matches: list[SkillSearchMatch] = []
    for record in records:
        matched_terms = tuple(sorted(query_tokens & set(record.lexical_terms)))
        lexical_score = _lexical_overlap(query_tokens, record.lexical_terms)
        semantic_score = _cosine_similarity(query_terms, record.term_weights)
        score = (semantic_score * 0.7) + (lexical_score * 0.3)
        if score <= 0:
            continue
        matches.append(
            SkillSearchMatch(
                name=record.name,
                file_path=record.file_path,
                score=round(score, 6),
                lexical_score=round(lexical_score, 6),
                semantic_score=round(semantic_score, 6),
                matched_terms=matched_terms,
            )
        )

    ranked = sorted(
        matches,
        key=lambda item: (-item.score, -item.semantic_score, -item.lexical_score, item.name),
    )
    return tuple(ranked[: max(1, int(limit))])


def render_retrieval_record(record: SkillRetrievalRecord) -> dict[str, Any]:
    """Render one retrieval record for persistence or inspection."""

    return {
        "name": record.name,
        "file_path": record.file_path,
        "lexical_terms": list(record.lexical_terms),
        "source_fields": list(record.source_fields),
        "term_weights": dict(sorted(record.term_weights.items())),
    }


def _field_tokens(values: Any) -> tuple[str, ...]:
    """Normalize one metadata field into retrieval tokens."""

    if isinstance(values, str):
        return _tokenize(values)
    if isinstance(values, Mapping):
        tokens: list[str] = []
        for key, value in values.items():
            tokens.extend(_tokenize(str(key)))
            tokens.extend(_tokenize(str(value)))
        return tuple(tokens)
    if isinstance(values, Iterable):
        tokens: list[str] = []
        for value in values:
            tokens.extend(_tokenize(str(value)))
        return tuple(tokens)
    return ()


def _parameter_names(parameters: Any) -> tuple[str, ...]:
    """Return stable parameter names for retrieval weighting."""

    if not isinstance(parameters, Mapping):
        return ()
    return tuple(str(name).strip() for name in parameters.keys() if str(name).strip())


def _query_weights(query: str) -> dict[str, float]:
    """Build weighted query-term counts."""

    counter = Counter(_tokenize(query))
    return {token: float(weight) for token, weight in counter.items()}


def _tokenize(text: str) -> tuple[str, ...]:
    """Return normalized lowercase tokens for retrieval."""

    lowered = str(text or "").lower().replace("_", " ").replace("-", " ")
    return tuple(_TOKEN_RE.findall(lowered))


def _lexical_overlap(query_terms: set[str], lexical_terms: Sequence[str]) -> float:
    """Return normalized lexical overlap score."""

    candidates = set(str(term) for term in lexical_terms)
    if not query_terms or not candidates:
        return 0.0
    intersection = len(query_terms & candidates)
    union = len(query_terms | candidates)
    return intersection / union if union else 0.0


def _cosine_similarity(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    """Return cosine similarity between two weighted sparse vectors."""

    if not left or not right:
        return 0.0
    dot = sum(float(left.get(term, 0.0)) * float(right.get(term, 0.0)) for term in left)
    if dot <= 0:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = [
    "SkillRetrievalRecord",
    "SkillSearchMatch",
    "build_skill_retrieval_record",
    "render_retrieval_record",
    "search_skill_records",
]
