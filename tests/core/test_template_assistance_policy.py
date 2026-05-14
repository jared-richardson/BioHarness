from __future__ import annotations

from bio_harness.core.template_assistance_policy import (
    protocol_normalization_policy,
    protocol_template_assistance_enabled,
)


def test_protocol_template_assistance_defaults_on_for_scientific_harness(monkeypatch) -> None:
    monkeypatch.delenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", raising=False)

    assert protocol_template_assistance_enabled("scientific_harness") is True


def test_protocol_template_assistance_can_be_disabled_for_scientific_harness(monkeypatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")

    assert protocol_template_assistance_enabled("scientific_harness") is False


def test_protocol_template_assistance_flag_does_not_change_official_policy(monkeypatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")

    assert protocol_template_assistance_enabled("official_bioagentbench") is True
    assert protocol_template_assistance_enabled("bioagentbench_planning_strict") is True


def test_protocol_normalization_policy_respects_scientific_ablation_flag(monkeypatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")

    enabled, meta = protocol_normalization_policy(
        benchmark_policy="scientific_harness",
        has_compiler=True,
        planning_strict_benchmark_policy=False,
        protocol_source_files=[],
    )

    assert enabled is False
    assert meta == {
        "changed": False,
        "why": "disabled_by_scientific_template_ablation",
    }


def test_protocol_normalization_policy_keeps_official_generic_normalization_available(monkeypatch) -> None:
    monkeypatch.setenv("BIO_HARNESS_PROTOCOL_TEMPLATE_ASSISTANCE", "0")

    enabled, meta = protocol_normalization_policy(
        benchmark_policy="official_bioagentbench",
        has_compiler=True,
        planning_strict_benchmark_policy=False,
        protocol_source_files=[],
    )

    assert enabled is True
    assert meta == {}
