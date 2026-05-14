"""Semantic validation helpers for differential-expression wrapper arguments.

This module validates metadata-aware tuning arguments for direct differential
expression wrappers before execution. The goal is to catch or deterministically
repair model-produced `design_formula` and `contrast` values when they do not
match the actual metadata schema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from bio_harness.core.de_wrapper_semantics_support import (
    _extract_design_variables,
    _levels_by_column,
    _load_metadata_table,
    _parse_contrast as _parse_contrast_spec,
    _resolve_binary_semantic_pair,
    _resolve_level_value,
    _sample_column_name,
)

_DIRECT_DE_WRAPPERS = frozenset({"deseq2_run", "edger_run", "limma_voom_run"})


@dataclass(frozen=True)
class _ContrastSpec:
    """One normalized contrast specification."""

    factor_name: str
    treatment: str
    control: str


def validate_and_repair_de_wrapper_arguments(
    tool_name: str,
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Validate metadata-aware DE wrapper arguments.

    Args:
        tool_name: Skill name for the current step.
        arguments: Planner-produced arguments for that skill.

    Returns:
        A tuple of `(arguments, issues, fixes)` where `arguments` is a repaired
        copy of the input mapping, `issues` contains blocking validation issues,
        and `fixes` describes any deterministic repairs that were applied.
    """

    repaired = {str(key): value for key, value in dict(arguments or {}).items()}
    if str(tool_name or "").strip().lower() not in _DIRECT_DE_WRAPPERS:
        return repaired, [], []

    metadata_path = Path(str(repaired.get("metadata_table", "") or "")).expanduser()
    if not metadata_path.exists() or not metadata_path.is_file():
        return repaired, [], []

    try:
        metadata_columns, metadata_rows = _load_metadata_table(metadata_path)
    except ValueError as exc:
        return repaired, [f"invalid_metadata_table:{exc}"], []
    if not metadata_columns:
        return repaired, ["invalid_metadata_table:missing_columns"], []

    sample_column = _sample_column_name(metadata_columns)
    levels_by_column = _levels_by_column(metadata_columns, metadata_rows)
    factor_candidate = _choose_factor_candidate(
        metadata_columns=metadata_columns,
        sample_column=sample_column,
        levels_by_column=levels_by_column,
        design_formula=str(repaired.get("design_formula", "") or ""),
        contrast_raw=repaired.get("contrast"),
    )

    fixes: list[str] = []
    issues: list[str] = []

    design_formula = str(repaired.get("design_formula", "") or "").strip()
    repaired_formula = _repair_design_formula(
        design_formula=design_formula,
        metadata_columns=metadata_columns,
        factor_candidate=factor_candidate,
        levels_by_column=levels_by_column,
    )
    if repaired_formula != design_formula:
        repaired["design_formula"] = repaired_formula
        fixes.append(f"semantic_repaired:design_formula:{design_formula}->{repaired_formula}")

    contrast_raw = repaired.get("contrast")
    repaired_contrast = _repair_contrast(
        contrast_raw=contrast_raw,
        factor_candidate=factor_candidate,
        levels_by_column=levels_by_column,
    )
    contrast_text = str(contrast_raw if contrast_raw is not None else "")
    if repaired_contrast and repaired_contrast != contrast_text:
        repaired["contrast"] = repaired_contrast
        fixes.append(f"semantic_repaired:contrast:{contrast_text}->{repaired_contrast}")

    final_formula = str(repaired.get("design_formula", "") or "").strip()
    final_design_vars = _extract_design_variables(final_formula)
    invalid_design_vars = [name for name in final_design_vars if name not in metadata_columns]
    if invalid_design_vars:
        issues.append(
            "invalid_design_formula_columns:" + ",".join(sorted(dict.fromkeys(invalid_design_vars)))
        )

    contrast_spec = _parse_contrast(repaired.get("contrast"))
    if contrast_spec is None:
        issues.append("invalid_contrast_spec")
        return repaired, issues, fixes

    factor_name = contrast_spec.factor_name
    if factor_name not in levels_by_column:
        issues.append(f"invalid_contrast_factor:{factor_name}")
        return repaired, issues, fixes

    valid_levels = levels_by_column.get(factor_name, {})
    resolved_treatment = _resolve_level_value(contrast_spec.treatment, valid_levels)
    resolved_control = _resolve_level_value(contrast_spec.control, valid_levels)
    if not resolved_treatment:
        issues.append(f"invalid_contrast_treatment:{factor_name}:{contrast_spec.treatment}")
    if not resolved_control:
        issues.append(f"invalid_contrast_control:{factor_name}:{contrast_spec.control}")
    if final_design_vars and factor_name not in final_design_vars:
        issues.append(f"contrast_factor_missing_from_design:{factor_name}")

    return repaired, issues, fixes


def _parse_contrast(raw: Any) -> _ContrastSpec | None:
    """Parse a contrast value into `(factor, treatment, control)` form."""
    return _parse_contrast_spec(raw, _ContrastSpec)


def _choose_factor_candidate(
    *,
    metadata_columns: list[str],
    sample_column: str | None,
    levels_by_column: Mapping[str, Mapping[str, str]],
    design_formula: str,
    contrast_raw: Any,
) -> str | None:
    """Choose a deterministic comparison factor when one is uniquely implied."""

    design_vars = _extract_design_variables(design_formula)
    contrast_spec = _parse_contrast(contrast_raw)
    if contrast_spec is not None and contrast_spec.factor_name in metadata_columns:
        if len(levels_by_column.get(contrast_spec.factor_name, {})) >= 2:
            return contrast_spec.factor_name

    non_sample_columns = [
        name
        for name in metadata_columns
        if name != sample_column and len(levels_by_column.get(name, {})) >= 2
    ]
    if contrast_spec is not None:
        exact_matches = [
            name
            for name in non_sample_columns
            if contrast_spec.treatment.lower() in levels_by_column.get(name, {})
            and contrast_spec.control.lower() in levels_by_column.get(name, {})
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

    valid_design_vars = [name for name in design_vars if name in non_sample_columns]
    if len(valid_design_vars) == 1:
        return valid_design_vars[0]
    if len(non_sample_columns) == 1:
        return non_sample_columns[0]
    return None


def _repair_design_formula(
    *,
    design_formula: str,
    metadata_columns: list[str],
    factor_candidate: str | None,
    levels_by_column: Mapping[str, Mapping[str, str]],
) -> str:
    """Repair one formula when there is a unique metadata-backed factor."""

    text = str(design_formula or "").strip()
    if not factor_candidate:
        return text
    design_vars = _extract_design_variables(text)
    invalid_vars = [name for name in design_vars if name not in metadata_columns]
    if not text:
        return f"~ {factor_candidate}"
    if not design_vars:
        return f"~ {factor_candidate}"
    informative_design_vars = [
        name
        for name in design_vars
        if name in metadata_columns and len(levels_by_column.get(name, {})) >= 2
    ]
    if factor_candidate and factor_candidate not in informative_design_vars:
        if len(levels_by_column.get(factor_candidate, {})) >= 2:
            informative_design_vars.append(factor_candidate)
    if informative_design_vars and informative_design_vars != design_vars:
        return "~ " + " + ".join(dict.fromkeys(informative_design_vars))
    if factor_candidate not in design_vars:
        if len(design_vars) == 1 and invalid_vars:
            return f"~ {factor_candidate}"
        if len(invalid_vars) == 1:
            bad_name = invalid_vars[0]
            patched = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(bad_name)}(?![A-Za-z0-9_])",
                factor_candidate,
                text,
            )
            if patched != text:
                return patched
        if not invalid_vars:
            return f"{text} + {factor_candidate}"
    return text


def _repair_contrast(
    *,
    contrast_raw: Any,
    factor_candidate: str | None,
    levels_by_column: Mapping[str, Mapping[str, str]],
) -> str | None:
    """Repair one contrast when metadata yields a unique binding."""

    contrast_spec = _parse_contrast(contrast_raw)
    raw_text = str(contrast_raw or "").strip()
    if contrast_spec is None:
        factor_name = factor_candidate
        if raw_text and raw_text in levels_by_column:
            factor_name = raw_text
        valid_levels = list(levels_by_column.get(str(factor_name or ""), {}).values())
        if len(valid_levels) == 2 and factor_name:
            return f"{factor_name}_{valid_levels[1]}_vs_{valid_levels[0]}"
        return None

    factor_name = contrast_spec.factor_name
    valid_levels = dict(levels_by_column.get(factor_name, {}))
    if (factor_name not in levels_by_column or len(valid_levels) < 2) and factor_candidate:
        factor_name = factor_candidate
        valid_levels = dict(levels_by_column.get(factor_name, {}))
    if not valid_levels:
        return None

    treatment = _resolve_level_value(contrast_spec.treatment, valid_levels)
    control = _resolve_level_value(contrast_spec.control, valid_levels)
    if not treatment and not control:
        binary_pair = _resolve_binary_semantic_pair(valid_levels)
        if binary_pair is not None:
            treatment, control = binary_pair
    if not treatment and not control and factor_name in levels_by_column:
        return None

    if len(valid_levels) == 2:
        all_levels = list(valid_levels.values())
        if control and not treatment:
            treatment = next((value for value in all_levels if value != control), "")
        if treatment and not control:
            control = next((value for value in all_levels if value != treatment), "")

    if not treatment or not control or treatment == control:
        return None
    return f"{factor_name}_{treatment}_vs_{control}"
