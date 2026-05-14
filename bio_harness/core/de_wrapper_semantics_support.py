"""Support helpers for differential-expression wrapper semantics."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from bio_harness.core.tabular_io import load_delimited_dict_rows

_SAMPLE_COLUMN_ALIASES = frozenset({"sample", "sample_id", "samplename", "sample_name"})
_FORMULA_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FUNCTION_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_TREATMENT_LEVEL_ALIASES = frozenset(
    {
        "treated",
        "treatment",
        "treat",
        "trt",
        "case",
        "experimental",
        "exposed",
        "stimulated",
        "biofilm",
        "disease",
        "tumor",
        "infected",
        "mutant",
        "knockout",
        "ko",
    }
)
_CONTROL_LEVEL_ALIASES = frozenset(
    {
        "untreated",
        "control",
        "ctrl",
        "untrt",
        "vehicle",
        "baseline",
        "reference",
        "planktonic",
        "plankton",
        "wildtype",
        "wt",
        "normal",
        "healthy",
        "mock",
    }
)


def _load_metadata_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Load a TSV/CSV metadata table into normalized rows."""

    columns, rows, _ = load_delimited_dict_rows(path)
    return columns, rows


def _sample_column_name(columns: Iterable[str]) -> str | None:
    """Return the most likely sample identifier column."""

    for name in columns:
        lowered = str(name).strip().lower()
        if lowered in _SAMPLE_COLUMN_ALIASES:
            return str(name).strip()
    column_list = [str(name).strip() for name in columns if str(name).strip()]
    return column_list[0] if column_list else None


def _levels_by_column(
    columns: Iterable[str],
    rows: Iterable[Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """Return case-folded categorical values per metadata column."""

    levels: dict[str, dict[str, str]] = {}
    for column in columns:
        values: dict[str, str] = {}
        for row in rows:
            raw_value = str(row.get(column, "") or "").strip()
            if not raw_value:
                continue
            values.setdefault(raw_value.lower(), raw_value)
        levels[str(column)] = values
    return levels


def _extract_design_variables(design_formula: str) -> list[str]:
    """Extract metadata column names from a formula-like string."""

    text = str(design_formula or "").strip()
    if not text:
        return []
    function_names = {
        match.group(1)
        for match in _FUNCTION_NAME_RE.finditer(text)
        if str(match.group(1) or "").strip()
    }
    variables: list[str] = []
    for match in _FORMULA_TOKEN_RE.finditer(text):
        token = str(match.group(0) or "").strip()
        if not token or token in function_names:
            continue
        if token not in variables:
            variables.append(token)
    return variables


def _normalize_level_token(value: str) -> str:
    """Normalize one categorical label for semantic matching."""

    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _semantic_level_group(value: str) -> str:
    """Return the semantic alias group for one contrast level label."""

    normalized = _normalize_level_token(value)
    if normalized in _TREATMENT_LEVEL_ALIASES:
        return "treatment"
    if normalized in _CONTROL_LEVEL_ALIASES:
        return "control"
    return ""


def _resolve_level_value(raw_value: str, valid_levels: Mapping[str, str]) -> str:
    """Resolve one requested contrast level against available metadata levels."""

    value = str(raw_value or "").strip()
    if not value:
        return ""
    exact = valid_levels.get(value.lower(), "")
    if exact:
        return exact

    normalized = _normalize_level_token(value)
    if not normalized:
        return ""
    normalized_matches = [
        candidate for candidate in valid_levels.values()
        if _normalize_level_token(candidate) == normalized
    ]
    if len(normalized_matches) == 1:
        return normalized_matches[0]

    semantic_group = _semantic_level_group(value)
    if semantic_group:
        group_matches = [
            candidate for candidate in valid_levels.values()
            if _semantic_level_group(candidate) == semantic_group
        ]
        if len(group_matches) == 1:
            return group_matches[0]

    substring_matches = [
        candidate for candidate in valid_levels.values()
        if normalized in _normalize_level_token(candidate)
        or _normalize_level_token(candidate) in normalized
    ]
    if len(substring_matches) == 1:
        return substring_matches[0]
    return ""


def _resolve_binary_semantic_pair(valid_levels: Mapping[str, str]) -> tuple[str, str] | None:
    """Return a deterministic `(treatment, control)` pair for binary factors."""

    if len(valid_levels) != 2:
        return None
    treatment_level = ""
    control_level = ""
    for candidate in valid_levels.values():
        semantic_group = _semantic_level_group(candidate)
        if semantic_group == "treatment":
            if treatment_level:
                return None
            treatment_level = candidate
        elif semantic_group == "control":
            if control_level:
                return None
            control_level = candidate
    if treatment_level and control_level and treatment_level != control_level:
        return treatment_level, control_level
    return None


def _parse_contrast(raw: Any, contrast_type: type[Any]) -> Any | None:
    """Parse a contrast value into a contrast spec instance."""

    if isinstance(raw, (list, tuple)) and len(raw) >= 3:
        values = [str(item or "").strip() for item in list(raw)[:3]]
        if all(values):
            return contrast_type(values[0], values[1], values[2])
        return None
    if isinstance(raw, Mapping):
        factor = str(raw.get("factor_name", "") or "").strip()
        treatment = str(raw.get("treatment", "") or "").strip()
        control = str(raw.get("control", "") or "").strip()
        if factor and treatment and control:
            return contrast_type(factor, treatment, control)
        return None

    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and len(parsed) >= 3:
            values = [str(item or "").strip() for item in parsed[:3]]
            if all(values):
                return contrast_type(values[0], values[1], values[2])
    if "," in text:
        parts = [part.strip() for part in text.split(",")]
        if len(parts) >= 3 and all(parts[:3]):
            return contrast_type(parts[0], parts[1], parts[2])
    parts = [part.strip() for part in text.split("_") if part.strip()]
    if len(parts) >= 4 and parts[2].lower() == "vs":
        return contrast_type(parts[0], parts[1], parts[3])
    return None


__all__ = [
    "_extract_design_variables",
    "_levels_by_column",
    "_load_metadata_table",
    "_normalize_level_token",
    "_parse_contrast",
    "_resolve_binary_semantic_pair",
    "_resolve_level_value",
    "_sample_column_name",
    "_semantic_level_group",
]
