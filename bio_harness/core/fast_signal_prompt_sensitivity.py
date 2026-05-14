"""Prompt-sensitivity measurement helpers for fast-signal studies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bio_harness.core.fast_signal import plan_idiom_summary


@dataclass(frozen=True)
class PromptSensitivityResult:
    """Prompt-feature effect size over paired planner emissions.

    Attributes:
        feature_name: Prompt feature being measured.
        pairs_compared: Number of paired emissions.
        changed_pairs: Number of pairs with different shape signatures.
        effect_size: Absolute changed-pair fraction.
        keep_feature: Whether effect size meets the default retention rule.
    """

    feature_name: str
    pairs_compared: int
    changed_pairs: int
    effect_size: float
    keep_feature: bool


def measure_prompt_sensitivity(
    *,
    feature_name: str,
    control_payloads: list[dict[str, Any]],
    treatment_payloads: list[dict[str, Any]],
    keep_threshold: float = 0.15,
) -> PromptSensitivityResult:
    """Measure whether a prompt feature changes emission shape.

    Args:
        feature_name: Prompt feature being measured.
        control_payloads: Planner payloads without the feature.
        treatment_payloads: Planner payloads with the feature.
        keep_threshold: Minimum absolute changed-pair fraction for keeping the
            prompt feature.

    Returns:
        Prompt-sensitivity result.
    """

    pair_count = min(len(control_payloads), len(treatment_payloads))
    changed = 0
    for index in range(pair_count):
        control_key = _shape_signature(plan_idiom_summary(control_payloads[index]))
        treatment_key = _shape_signature(plan_idiom_summary(treatment_payloads[index]))
        changed += int(control_key != treatment_key)
    effect_size = changed / max(pair_count, 1)
    return PromptSensitivityResult(
        feature_name=feature_name,
        pairs_compared=pair_count,
        changed_pairs=changed,
        effect_size=effect_size,
        keep_feature=effect_size >= keep_threshold,
    )


def _shape_signature(summary: dict[str, Any]) -> tuple[Any, ...]:
    path_styles = summary.get("path_styles", {})
    argument_forms = summary.get("argument_forms", {})
    branch_styles = summary.get("branch_styles", {})
    return (
        summary.get("top_level_step_key", ""),
        tuple(summary.get("tool_names", []) or []),
        path_styles.get("absolute", 0),
        path_styles.get("relative", 0),
        path_styles.get("bare", 0),
        argument_forms.get("arguments", 0),
        argument_forms.get("parameter_hints", 0),
        branch_styles.get("branch_id", 0),
        branch_styles.get("sample_name", 0),
    )
