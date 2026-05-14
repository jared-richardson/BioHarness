"""First-run setup state for Bio-Harness.

This module composes existing bootstrap, doctor, and LLM-backend reports into a
small deterministic state object that can drive both a CLI setup command and the
React first-run wizard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bio_harness.core.model_catalog import (
    ModelCatalogEntry,
    assess_model_resources,
    build_model_setup_options,
    find_catalog_entry,
    load_model_catalog,
    recommend_model,
)


def _bool_or_none(payload: dict[str, Any] | None, key: str) -> bool | None:
    """Read a boolean from an optional report."""
    if not isinstance(payload, dict) or key not in payload:
        return None
    return bool(payload.get(key))


def _available_models_from_llm_report(
    llm_setup_report: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Extract available model rows from an LLM setup report."""
    if not isinstance(llm_setup_report, dict):
        return []
    rows = llm_setup_report.get("available_models", [])
    return [row for row in rows if isinstance(row, dict)]


def _resource_row_for_recommendation(
    *,
    catalog: list[ModelCatalogEntry],
    model_id: str,
    installed: bool,
    free_disk_gb: float | None,
    available_ram_gb: float | None,
) -> dict[str, Any] | None:
    """Build resource metadata for the selected recommendation."""
    entry = find_catalog_entry(model_id, catalog)
    if entry is None:
        return None
    return assess_model_resources(
        entry,
        free_disk_gb=free_disk_gb,
        available_ram_gb=available_ram_gb,
        installed=installed,
    )


def _next_actions(
    *,
    environment_ready: bool | None,
    llm_setup_report: dict[str, Any] | None,
    recommended_model_id: str,
    resource_assessment: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return UI-ready next actions for first-run setup."""
    actions: list[dict[str, str]] = []
    if environment_ready is not True:
        actions.append(
            {
                "id": "run_environment_setup",
                "label": "Run environment setup",
                "reason": "Python/Pixi setup has not been confirmed yet.",
            }
        )

    report = llm_setup_report if isinstance(llm_setup_report, dict) else {}
    backend_name = str(report.get("backend_name", "ollama") or "ollama")
    ollama_available = report.get("ollama_command_available")
    backend_reachable = bool(report.get("backend_reachable", False))
    model_present = bool(report.get("model_present", False))
    ready = bool(report.get("ready", False))

    if backend_name in {"ollama", "ollama_openai"} and ollama_available is False:
        actions.append(
            {
                "id": "install_ollama",
                "label": "Install Ollama",
                "reason": "The Ollama CLI is not available on PATH.",
            }
        )
        return actions

    if backend_name in {"ollama", "ollama_openai"} and ollama_available and not backend_reachable:
        actions.append(
            {
                "id": "start_ollama",
                "label": "Start Ollama",
                "reason": "The local Ollama server is not reachable.",
            }
        )
        return actions

    if backend_reachable and not model_present:
        if resource_assessment and resource_assessment.get("disk_ok") is False:
            actions.append(
                {
                    "id": "free_disk_for_model",
                    "label": "Free disk before model pull",
                    "reason": f"Not enough free disk for {recommended_model_id}.",
                }
            )
            return actions
        actions.append(
            {
                "id": "pull_model",
                "label": "Pull selected model",
                "reason": f"{recommended_model_id} is not installed yet.",
            }
        )
        return actions

    if backend_reachable and model_present and not ready:
        actions.append(
            {
                "id": "verify_model",
                "label": "Verify model",
                "reason": "The model is installed but the backend probe did not pass.",
            }
        )
        return actions

    if environment_ready is True and ready:
        actions.append(
            {
                "id": "run_mini_preflight",
                "label": "Run mini preflight",
                "reason": "The environment and model are ready for a tiny real-tool check.",
            }
        )
    return actions


def build_first_run_setup_status(
    *,
    bootstrap_report: dict[str, Any] | None = None,
    doctor_report: dict[str, Any] | None = None,
    llm_setup_report: dict[str, Any] | None = None,
    free_disk_gb: float | None = None,
    available_ram_gb: float | None = None,
    catalog_path: str | Path | None = None,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic first-run setup status payload.

    Args:
        bootstrap_report: Optional output from ``bootstrap_bioharness.py``.
        doctor_report: Optional output from ``doctor_bioharness.py``.
        llm_setup_report: Optional output from ``setup_llm_backend.py``.
        free_disk_gb: Current free disk in GiB.
        available_ram_gb: Current available RAM in GiB.
        catalog_path: Optional model catalog path for tests or overrides.
        requested_model: Optional user-selected model id.

    Returns:
        JSON-serializable setup status for CLI or UI surfaces.
    """
    catalog = load_model_catalog(catalog_path)
    installed_models = _available_models_from_llm_report(llm_setup_report)
    recommendation = recommend_model(
        catalog,
        installed_models=installed_models,
        requested_model=requested_model,
    )
    resource_assessment = _resource_row_for_recommendation(
        catalog=catalog,
        model_id=recommendation.model_id,
        installed=recommendation.installed,
        free_disk_gb=free_disk_gb,
        available_ram_gb=available_ram_gb,
    )

    bootstrap_ready = _bool_or_none(bootstrap_report, "success")
    doctor_ready = _bool_or_none(doctor_report, "ready")
    environment_ready = doctor_ready if doctor_ready is not None else bootstrap_ready
    model_ready = _bool_or_none(llm_setup_report, "ready")
    next_actions = _next_actions(
        environment_ready=environment_ready,
        llm_setup_report=llm_setup_report,
        recommended_model_id=recommendation.model_id,
        resource_assessment=resource_assessment,
    )
    setup_complete = environment_ready is True and model_ready is True

    return {
        "schema_version": 1,
        "setup_complete": setup_complete,
        "environment_ready": environment_ready,
        "model_ready": model_ready,
        "recommended_model": recommendation.to_public_dict(),
        "recommended_model_resource_assessment": resource_assessment,
        "model_options": build_model_setup_options(
            catalog=catalog,
            installed_models=installed_models,
            free_disk_gb=free_disk_gb,
            available_ram_gb=available_ram_gb,
            requested_model=requested_model,
        ),
        "next_actions": next_actions,
    }
