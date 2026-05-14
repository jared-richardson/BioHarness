"""Registry for calibrated fast-signal failure classes.

The scorecard uses registry-backed identifiers instead of free-form strings so
that calibration does not accidentally treat spelling variants as distinct
failure classes.
"""

from __future__ import annotations

from dataclasses import dataclass

FAILURE_CLASS_REGISTRY_VERSION = 1
UNCLASSIFIED_FAILURE_CLASS_ID = "unclassified"

KNOWN_FAILURE_CLASS_IDS = frozenset(
    {
        "branch_stage_progress",
        "annotated_vcf_handoff_binding",
        "duplicate_detector_granularity",
        "isec_path_branch_binding",
        "planner_completed_prefix_restart",
        "planner_input_role_confusion",
        "planner_missing_required_arguments_first_step",
        "planner_off_skeleton_required_step",
        "planner_output_input_alias_confusion",
        "prokka_gff_binding",
        "stepwise_prefix_normalization_state_serialization",
        "stepwise_prefix_scientific_repair_after_prokka",
    }
)

NO_FAILURE_CLASS_IDS = frozenset({"", "none", "no_failure", "pass"})


@dataclass(frozen=True)
class FailureClassResolution:
    """Resolved failure-class identity.

    Attributes:
        raw_value: Original scorecard value.
        failure_class_id: Registry ID used for calibration.
        known: Whether the value matched a known registry entry or no-failure
            marker.
        unclassified: Whether the value was non-empty but unknown.
    """

    raw_value: str
    failure_class_id: str
    known: bool
    unclassified: bool


def resolve_failure_class(value: str | None) -> FailureClassResolution:
    """Resolve a free-form failure class against the registry.

    Args:
        value: Raw failure-class value from a scorecard row.

    Returns:
        Registry resolution for calibration and reporting.
    """
    raw_value = str(value or "").strip()
    normalized = raw_value.lower().replace("-", "_").replace(" ", "_")
    if normalized in NO_FAILURE_CLASS_IDS:
        return FailureClassResolution(
            raw_value=raw_value,
            failure_class_id=normalized,
            known=True,
            unclassified=False,
        )
    if normalized in KNOWN_FAILURE_CLASS_IDS:
        return FailureClassResolution(
            raw_value=raw_value,
            failure_class_id=normalized,
            known=True,
            unclassified=False,
        )
    return FailureClassResolution(
        raw_value=raw_value,
        failure_class_id=UNCLASSIFIED_FAILURE_CLASS_ID,
        known=False,
        unclassified=True,
    )


__all__ = [
    "FAILURE_CLASS_REGISTRY_VERSION",
    "KNOWN_FAILURE_CLASS_IDS",
    "UNCLASSIFIED_FAILURE_CLASS_ID",
    "FailureClassResolution",
    "resolve_failure_class",
]
