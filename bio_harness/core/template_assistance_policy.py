"""Policy helpers for ablation-only scientific template assistance controls.

These helpers centralize the narrow environment flag used by the extended
scientific-suite ablation runner. The default harness keeps deterministic
protocol/template assistance enabled. The ablation flag only changes behavior
for `scientific_harness` runs so official benchmark semantics remain intact.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from bio_harness.core.benchmark_policy import (
    SCIENTIFIC_HARNESS_POLICY,
    is_blind_bioagentbench_policy,
    normalize_benchmark_policy,
)

_SCIENTIFIC_TEMPLATE_ASSISTANCE_ENV = "BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE"


def protocol_template_assistance_enabled(benchmark_policy: str | None) -> bool:
    """Return whether deterministic protocol/template assistance is enabled.

    Args:
        benchmark_policy: Active benchmark assistance policy.

    Returns:
        `True` unless the scientific-harness-only ablation flag explicitly
        disables deterministic template assistance.
    """

    if normalize_benchmark_policy(benchmark_policy) != SCIENTIFIC_HARNESS_POLICY:
        return True
    return _env_flag(_SCIENTIFIC_TEMPLATE_ASSISTANCE_ENV, default=True)


def protocol_normalization_policy(
    *,
    benchmark_policy: str | None,
    has_compiler: bool,
    planning_strict_benchmark_policy: bool,
    protocol_source_files: Iterable[str],
) -> tuple[bool, dict[str, Any]]:
    """Return whether deterministic protocol normalization is allowed.

    Args:
        benchmark_policy: Active benchmark assistance policy.
        has_compiler: Whether the active analysis type has a template compiler.
        planning_strict_benchmark_policy: Whether strict blind planning mode is
            active.
        protocol_source_files: Visible protocol grounding sources.

    Returns:
        A tuple of `(enabled, metadata)` following the existing harness
        convention for protocol-normalization policy decisions.
    """

    normalized_policy = normalize_benchmark_policy(benchmark_policy)
    if not protocol_template_assistance_enabled(normalized_policy):
        return False, {
            "changed": False,
            "why": "disabled_by_scientific_template_ablation",
        }

    blind_benchmark_policy = is_blind_bioagentbench_policy(normalized_policy)
    source_files = [str(path).strip() for path in protocol_source_files if str(path).strip()]
    allow_official_generic_normalization = (
        blind_benchmark_policy
        and has_compiler
        and not planning_strict_benchmark_policy
        and not source_files
    )
    enabled = bool((not blind_benchmark_policy) or allow_official_generic_normalization)
    if blind_benchmark_policy and not allow_official_generic_normalization:
        return False, {
            "changed": False,
            "why": f"disabled_for_{normalized_policy}_policy",
        }
    return enabled, {}


def _env_flag(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


__all__ = [
    "protocol_normalization_policy",
    "protocol_template_assistance_enabled",
]
