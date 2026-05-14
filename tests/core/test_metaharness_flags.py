from __future__ import annotations

import pytest

from bio_harness.core.metaharness_flags import (
    diagnostic_traces_enabled,
    environment_bootstrap_enabled,
    nonmarkovian_repair_enabled,
    trace_advisories_enabled,
)


@pytest.mark.parametrize(
    ("env_name", "func"),
    [
        ("BIO_HARNESS_DIAGNOSTIC_TRACES", diagnostic_traces_enabled),
        ("BIO_HARNESS_NONMARKOVIAN_REPAIR", nonmarkovian_repair_enabled),
        ("BIO_HARNESS_ENV_BOOTSTRAP", environment_bootstrap_enabled),
        ("BIO_HARNESS_TRACE_ADVISORIES", trace_advisories_enabled),
    ],
)
def test_metaharness_flags_default_enabled(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    func,
) -> None:
    monkeypatch.delenv(env_name, raising=False)

    assert func() is True


@pytest.mark.parametrize(
    ("env_name", "func"),
    [
        ("BIO_HARNESS_DIAGNOSTIC_TRACES", diagnostic_traces_enabled),
        ("BIO_HARNESS_NONMARKOVIAN_REPAIR", nonmarkovian_repair_enabled),
        ("BIO_HARNESS_ENV_BOOTSTRAP", environment_bootstrap_enabled),
        ("BIO_HARNESS_TRACE_ADVISORIES", trace_advisories_enabled),
    ],
)
def test_metaharness_flags_disable_on_false_like_values(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    func,
) -> None:
    monkeypatch.setenv(env_name, "0")

    assert func() is False
