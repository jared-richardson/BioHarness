"""Runtime feature flags for Meta-Harness-inspired ablations.

These helpers provide one narrow, testable source of truth for the ablation
toggles used by the Meta-Harness benchmark suite. The defaults reflect the
fully enabled harness; ablation scripts override them through environment
variables.
"""

from __future__ import annotations

import os


def diagnostic_traces_enabled() -> bool:
    """Return whether repair-context diagnostic traces should be included."""

    return _env_flag("BIO_HARNESS_DIAGNOSTIC_TRACES", default=True)


def nonmarkovian_repair_enabled() -> bool:
    """Return whether prior repair history should be injected into repair prompts."""

    return _env_flag("BIO_HARNESS_NONMARKOVIAN_REPAIR", default=True)


def environment_bootstrap_enabled() -> bool:
    """Return whether initial planning should include environment bootstrap context."""

    return _env_flag("BIO_HARNESS_ENV_BOOTSTRAP", default=True)


def trace_advisories_enabled() -> bool:
    """Return whether repair advisories should be surfaced in repair context."""

    return _env_flag("BIO_HARNESS_TRACE_ADVISORIES", default=True)


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
    "diagnostic_traces_enabled",
    "environment_bootstrap_enabled",
    "nonmarkovian_repair_enabled",
    "trace_advisories_enabled",
]
