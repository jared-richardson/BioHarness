from __future__ import annotations

from bio_harness.core.model_catalog import (
    DEFAULT_PUBLIC_MODEL_ID,
    assess_model_resources,
    build_model_setup_options,
    estimate_required_free_disk_gb,
    load_model_catalog,
    recommend_model,
)


def test_model_catalog_loads_public_recommendations() -> None:
    catalog = load_model_catalog()

    model_ids = [entry.model_id for entry in catalog]
    assert DEFAULT_PUBLIC_MODEL_ID in model_ids
    assert "gemma4:26b" in model_ids
    assert "qwen3.6:35b-a3b" in model_ids
    assert catalog[0].model_id == DEFAULT_PUBLIC_MODEL_ID


def test_recommend_model_prefers_installed_qwen_coder_default() -> None:
    catalog = load_model_catalog()

    recommendation = recommend_model(
        catalog,
        installed_models=[
            {
                "name": DEFAULT_PUBLIC_MODEL_ID,
                "family": "qwen3next",
                "size_gb": 51.0,
            }
        ],
    )

    assert recommendation.model_id == DEFAULT_PUBLIC_MODEL_ID
    assert recommendation.installed is True
    assert recommendation.source == "installed_default"


def test_recommend_model_uses_gemma_as_tested_installed_alternative() -> None:
    catalog = load_model_catalog()

    recommendation = recommend_model(
        catalog,
        installed_models=[
            {
                "name": "gemma4:26b",
                "family": "gemma",
                "size_gb": 17.0,
            }
        ],
    )

    assert recommendation.model_id == "gemma4:26b"
    assert recommendation.source == "installed_tested_alternative"


def test_recommend_model_does_not_default_to_qwen36_research_stress_model() -> None:
    catalog = load_model_catalog()

    recommendation = recommend_model(
        catalog,
        installed_models=[
            {
                "name": "qwen3.6:35b-a3b",
                "family": "qwen",
                "size_gb": 23.0,
            }
        ],
    )

    assert recommendation.model_id == DEFAULT_PUBLIC_MODEL_ID
    assert recommendation.source == "catalog_default"
    assert recommendation.installed is False


def test_estimate_required_free_disk_includes_minimum_buffer() -> None:
    assert estimate_required_free_disk_gb(5.0) == 15.0
    assert estimate_required_free_disk_gb(100.0) == 120.0


def test_assess_model_resources_blocks_pull_when_disk_is_insufficient() -> None:
    entry = load_model_catalog()[0]

    assessment = assess_model_resources(
        entry,
        free_disk_gb=10.0,
        available_ram_gb=128.0,
        installed=False,
    )

    assert assessment["disk_ok"] is False
    assert assessment["can_pull"] is False


def test_build_model_setup_options_marks_installed_models_and_resources() -> None:
    payload = build_model_setup_options(
        installed_models=[
            {
                "name": DEFAULT_PUBLIC_MODEL_ID,
                "family": "qwen3next",
                "size_gb": 51.0,
            }
        ],
        free_disk_gb=100.0,
        available_ram_gb=32.0,
    )

    installed_rows = [row for row in payload["models"] if row["installed"]]
    assert installed_rows[0]["model_id"] == DEFAULT_PUBLIC_MODEL_ID
    assert payload["recommended"]["model_id"] == DEFAULT_PUBLIC_MODEL_ID
    assert installed_rows[0]["resource_assessment"]["required_free_disk_gb"] == 0.0
