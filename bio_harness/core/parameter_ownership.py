"""Helpers for classifying who owns one tool parameter value.

The planner should only author user-facing scientific inputs and tuning
parameters. Harness-managed implementation details, such as bundled
wrapper-script paths, must be injected deterministically by the runtime.
"""

from __future__ import annotations

from typing import Any, Mapping


DEFAULT_PARAMETER_OWNERSHIP = "user_input"
HARNESS_MANAGED_PARAMETER_OWNERSHIP = "harness_managed"
EXECUTION_OUTPUT_PARAMETER_OWNERSHIP = "execution_output"
TUNING_PARAMETER_OWNERSHIP = "tuning"

_OWNERSHIP_ALIASES = {
    "": DEFAULT_PARAMETER_OWNERSHIP,
    "user": DEFAULT_PARAMETER_OWNERSHIP,
    "user_input": DEFAULT_PARAMETER_OWNERSHIP,
    "input": DEFAULT_PARAMETER_OWNERSHIP,
    "planner": DEFAULT_PARAMETER_OWNERSHIP,
    "managed": HARNESS_MANAGED_PARAMETER_OWNERSHIP,
    "harness": HARNESS_MANAGED_PARAMETER_OWNERSHIP,
    "runtime": HARNESS_MANAGED_PARAMETER_OWNERSHIP,
    "runtime_managed": HARNESS_MANAGED_PARAMETER_OWNERSHIP,
    "harness_managed": HARNESS_MANAGED_PARAMETER_OWNERSHIP,
    "output": EXECUTION_OUTPUT_PARAMETER_OWNERSHIP,
    "execution": EXECUTION_OUTPUT_PARAMETER_OWNERSHIP,
    "execution_output": EXECUTION_OUTPUT_PARAMETER_OWNERSHIP,
    "result": EXECUTION_OUTPUT_PARAMETER_OWNERSHIP,
    "tune": TUNING_PARAMETER_OWNERSHIP,
    "tuning": TUNING_PARAMETER_OWNERSHIP,
    "parameter": TUNING_PARAMETER_OWNERSHIP,
}


def normalize_parameter_ownership(spec: Mapping[str, Any] | str | None) -> str:
    """Return the normalized ownership label for one parameter declaration."""

    if isinstance(spec, Mapping):
        raw = spec.get("ownership", DEFAULT_PARAMETER_OWNERSHIP)
    else:
        raw = spec or DEFAULT_PARAMETER_OWNERSHIP
    normalized = str(raw or DEFAULT_PARAMETER_OWNERSHIP).strip().lower()
    return _OWNERSHIP_ALIASES.get(normalized, DEFAULT_PARAMETER_OWNERSHIP)


def is_harness_managed_parameter(spec: Mapping[str, Any] | str | None) -> bool:
    """Return whether one parameter is runtime-managed by the harness."""

    return normalize_parameter_ownership(spec) == HARNESS_MANAGED_PARAMETER_OWNERSHIP


def is_execution_output_parameter(spec: Mapping[str, Any] | str | None) -> bool:
    """Return whether one parameter names an execution output location."""

    return normalize_parameter_ownership(spec) == EXECUTION_OUTPUT_PARAMETER_OWNERSHIP
