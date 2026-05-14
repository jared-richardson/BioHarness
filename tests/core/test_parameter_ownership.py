"""Tests for parameter ownership normalization helpers."""

from __future__ import annotations

import pytest

from bio_harness.core.parameter_ownership import (
    DEFAULT_PARAMETER_OWNERSHIP,
    EXECUTION_OUTPUT_PARAMETER_OWNERSHIP,
    HARNESS_MANAGED_PARAMETER_OWNERSHIP,
    TUNING_PARAMETER_OWNERSHIP,
    is_execution_output_parameter,
    is_harness_managed_parameter,
    normalize_parameter_ownership,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, DEFAULT_PARAMETER_OWNERSHIP),
        ("", DEFAULT_PARAMETER_OWNERSHIP),
        ("user_input", DEFAULT_PARAMETER_OWNERSHIP),
        ("user", DEFAULT_PARAMETER_OWNERSHIP),
        ("input", DEFAULT_PARAMETER_OWNERSHIP),
        ({"ownership": "planner"}, DEFAULT_PARAMETER_OWNERSHIP),
        ("harness_managed", HARNESS_MANAGED_PARAMETER_OWNERSHIP),
        ("managed", HARNESS_MANAGED_PARAMETER_OWNERSHIP),
        ("runtime_managed", HARNESS_MANAGED_PARAMETER_OWNERSHIP),
        ({"ownership": "harness"}, HARNESS_MANAGED_PARAMETER_OWNERSHIP),
        ("execution_output", EXECUTION_OUTPUT_PARAMETER_OWNERSHIP),
        ("output", EXECUTION_OUTPUT_PARAMETER_OWNERSHIP),
        ("result", EXECUTION_OUTPUT_PARAMETER_OWNERSHIP),
        ({"ownership": "execution"}, EXECUTION_OUTPUT_PARAMETER_OWNERSHIP),
        ("tuning", TUNING_PARAMETER_OWNERSHIP),
        ("tune", TUNING_PARAMETER_OWNERSHIP),
        ({"ownership": "parameter"}, TUNING_PARAMETER_OWNERSHIP),
        ("unknown_value", DEFAULT_PARAMETER_OWNERSHIP),
    ],
)
def test_normalize_parameter_ownership_handles_aliases_and_unknown_values(spec, expected) -> None:
    assert normalize_parameter_ownership(spec) == expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"ownership": "harness_managed"}, True),
        ({"ownership": "runtime_managed"}, True),
        ({"ownership": "user_input"}, False),
        ({"ownership": "tuning"}, False),
        ({"ownership": "execution_output"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_is_harness_managed_parameter_matches_only_managed_values(spec, expected) -> None:
    assert is_harness_managed_parameter(spec) is expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"ownership": "execution_output"}, True),
        ({"ownership": "output"}, True),
        ({"ownership": "harness_managed"}, False),
        ({"ownership": "user_input"}, False),
        ({"ownership": "tuning"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_is_execution_output_parameter_matches_only_output_values(spec, expected) -> None:
    assert is_execution_output_parameter(spec) is expected
