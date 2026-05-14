"""Model catalog and recommendation helpers for first-run setup.

The catalog keeps public setup recommendations deterministic. It records the
models we have actually tested, their approximate local resource needs, and
whether they are suitable for the default public path or only for advanced
research/stress setups.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CATALOG_PATH = PROJECT_ROOT / "bio_harness" / "config" / "model_catalog.json"
DEFAULT_PUBLIC_MODEL_ID = "qwen3-coder-next:latest"
MIN_DISK_BUFFER_GB = 10.0
DISK_BUFFER_FRACTION = 0.2


@dataclass(frozen=True)
class ModelCatalogEntry:
    """One tested or recommended local model option."""

    model_id: str
    backend: str
    display_name: str
    recommended: bool
    recommendation_priority: int
    release_role: str
    tested_status: str
    evidence_doc: str
    estimated_download_gb: float
    estimated_disk_required_gb: float
    min_ram_gb: float
    recommended_ram_gb: float
    notes: str
    tags: tuple[str, ...]

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> ModelCatalogEntry:
        """Build an entry from a JSON mapping.

        Args:
            payload: Raw model mapping from ``model_catalog.json``.

        Returns:
            Parsed catalog entry.

        Raises:
            ValueError: If required fields are missing.
        """
        model_id = str(payload.get("model_id", "")).strip()
        backend = str(payload.get("backend", "")).strip()
        if not model_id or not backend:
            raise ValueError("model catalog entries require model_id and backend")
        return cls(
            model_id=model_id,
            backend=backend,
            display_name=str(payload.get("display_name", model_id)).strip() or model_id,
            recommended=bool(payload.get("recommended", False)),
            recommendation_priority=int(payload.get("recommendation_priority", 100)),
            release_role=str(payload.get("release_role", "")).strip(),
            tested_status=str(payload.get("tested_status", "")).strip(),
            evidence_doc=str(payload.get("evidence_doc", "")).strip(),
            estimated_download_gb=float(payload.get("estimated_download_gb", 0.0) or 0.0),
            estimated_disk_required_gb=float(payload.get("estimated_disk_required_gb", 0.0) or 0.0),
            min_ram_gb=float(payload.get("min_ram_gb", 0.0) or 0.0),
            recommended_ram_gb=float(payload.get("recommended_ram_gb", 0.0) or 0.0),
            notes=str(payload.get("notes", "")).strip(),
            tags=tuple(str(tag).strip() for tag in payload.get("tags", []) if str(tag).strip()),
        )

    def to_public_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable catalog entry."""
        return {
            "model_id": self.model_id,
            "backend": self.backend,
            "display_name": self.display_name,
            "recommended": self.recommended,
            "recommendation_priority": self.recommendation_priority,
            "release_role": self.release_role,
            "tested_status": self.tested_status,
            "evidence_doc": self.evidence_doc,
            "estimated_download_gb": self.estimated_download_gb,
            "estimated_disk_required_gb": self.estimated_disk_required_gb,
            "min_ram_gb": self.min_ram_gb,
            "recommended_ram_gb": self.recommended_ram_gb,
            "notes": self.notes,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class ModelRecommendation:
    """Recommended model selection for first-run setup."""

    model_id: str
    source: str
    reason: str
    installed: bool
    catalog_entry: ModelCatalogEntry | None

    def to_public_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable recommendation."""
        return {
            "model_id": self.model_id,
            "source": self.source,
            "reason": self.reason,
            "installed": self.installed,
            "catalog_entry": (
                self.catalog_entry.to_public_dict() if self.catalog_entry is not None else None
            ),
        }


def load_model_catalog(path: str | Path | None = None) -> list[ModelCatalogEntry]:
    """Load checked-in model recommendations.

    Args:
        path: Optional catalog path. Defaults to the repo catalog.

    Returns:
        Parsed catalog entries sorted by recommendation priority.

    Raises:
        ValueError: If the catalog schema is unsupported or invalid.
    """
    catalog_path = Path(path or DEFAULT_MODEL_CATALOG_PATH).expanduser().resolve()
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0) or 0) != 1:
        raise ValueError("unsupported model catalog schema_version")
    entries = [
        ModelCatalogEntry.from_mapping(row)
        for row in payload.get("models", [])
        if isinstance(row, dict)
    ]
    if not entries:
        raise ValueError("model catalog contains no models")
    return sorted(entries, key=lambda entry: (entry.recommendation_priority, entry.model_id))


def installed_model_names(
    installed_models: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> set[str]:
    """Return installed model names from backend discovery rows.

    Args:
        installed_models: Rows from ``llm_setup_support`` or Ollama metadata.

    Returns:
        Set of normalized model names.
    """
    names: set[str] = set()
    for row in installed_models:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("model") or "").strip()
        if name:
            names.add(name)
    return names


def find_catalog_entry(
    model_id: str,
    catalog: list[ModelCatalogEntry] | tuple[ModelCatalogEntry, ...],
) -> ModelCatalogEntry | None:
    """Find a catalog entry by exact model id."""
    normalized = str(model_id or "").strip()
    for entry in catalog:
        if entry.model_id == normalized:
            return entry
    return None


def estimate_required_free_disk_gb(
    estimated_download_gb: float,
    *,
    min_buffer_gb: float = MIN_DISK_BUFFER_GB,
    buffer_fraction: float = DISK_BUFFER_FRACTION,
) -> float:
    """Estimate free disk needed before pulling a model.

    Args:
        estimated_download_gb: Approximate model download size.
        min_buffer_gb: Minimum extra disk buffer.
        buffer_fraction: Fractional extra disk buffer.

    Returns:
        Required free disk in GiB, rounded to two decimals.
    """
    download = max(float(estimated_download_gb or 0.0), 0.0)
    buffer = max(float(min_buffer_gb), download * float(buffer_fraction))
    return round(download + buffer, 2)


def assess_model_resources(
    entry: ModelCatalogEntry,
    *,
    free_disk_gb: float | None,
    available_ram_gb: float | None = None,
    installed: bool = False,
) -> dict[str, Any]:
    """Assess whether local resources are sufficient for a model setup step.

    Args:
        entry: Catalog model entry.
        free_disk_gb: Current free disk in GiB. ``None`` means unknown.
        available_ram_gb: Current available RAM in GiB. ``None`` means unknown.
        installed: Whether the model is already present locally.

    Returns:
        JSON-serializable resource assessment.
    """
    required_disk = 0.0 if installed else entry.estimated_disk_required_gb
    if required_disk <= 0 and not installed:
        required_disk = estimate_required_free_disk_gb(entry.estimated_download_gb)
    disk_ok = None if free_disk_gb is None else float(free_disk_gb) >= required_disk
    ram_ok = None
    ram_warning = False
    if available_ram_gb is not None and entry.min_ram_gb > 0:
        ram_ok = float(available_ram_gb) >= entry.min_ram_gb
        ram_warning = not ram_ok
    return {
        "model_id": entry.model_id,
        "installed": installed,
        "estimated_download_gb": entry.estimated_download_gb,
        "required_free_disk_gb": round(required_disk, 2),
        "free_disk_gb": None if free_disk_gb is None else round(float(free_disk_gb), 2),
        "disk_ok": disk_ok,
        "min_ram_gb": entry.min_ram_gb,
        "recommended_ram_gb": entry.recommended_ram_gb,
        "available_ram_gb": (
            None if available_ram_gb is None else round(float(available_ram_gb), 2)
        ),
        "ram_ok": ram_ok,
        "ram_warning": ram_warning,
        "can_pull": installed or disk_ok is not False,
    }


def recommend_model(
    catalog: list[ModelCatalogEntry] | tuple[ModelCatalogEntry, ...],
    installed_models: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    *,
    requested_model: str | None = None,
) -> ModelRecommendation:
    """Choose the model Bio-Harness should suggest during first-run setup.

    Args:
        catalog: Parsed model catalog entries.
        installed_models: Installed backend model rows.
        requested_model: Optional user-selected model id.

    Returns:
        Deterministic recommendation.
    """
    installed = installed_model_names(tuple(installed_models or ()))
    requested = str(requested_model or "").strip()
    if requested:
        entry = find_catalog_entry(requested, catalog)
        return ModelRecommendation(
            model_id=requested,
            source="requested",
            reason="User-selected model.",
            installed=requested in installed,
            catalog_entry=entry,
        )

    default_entry = find_catalog_entry(DEFAULT_PUBLIC_MODEL_ID, catalog)
    if default_entry is not None and default_entry.model_id in installed:
        return ModelRecommendation(
            model_id=default_entry.model_id,
            source="installed_default",
            reason="Recommended public model is already installed.",
            installed=True,
            catalog_entry=default_entry,
        )

    qwen_coder_installed = sorted(name for name in installed if "qwen3-coder" in name.lower())
    if qwen_coder_installed:
        chosen = qwen_coder_installed[0]
        return ModelRecommendation(
            model_id=chosen,
            source="installed_compatible_qwen_coder",
            reason="A compatible Qwen Coder model is already installed.",
            installed=True,
            catalog_entry=find_catalog_entry(chosen, catalog),
        )

    gemma_entry = find_catalog_entry("gemma4:26b", catalog)
    if gemma_entry is not None and gemma_entry.model_id in installed:
        return ModelRecommendation(
            model_id=gemma_entry.model_id,
            source="installed_tested_alternative",
            reason="Tested Gemma alternative is already installed.",
            installed=True,
            catalog_entry=gemma_entry,
        )

    recommended_entries = [entry for entry in catalog if entry.recommended]
    chosen_entry = recommended_entries[0] if recommended_entries else catalog[0]
    return ModelRecommendation(
        model_id=chosen_entry.model_id,
        source="catalog_default",
        reason="Recommended public default for first-run setup.",
        installed=chosen_entry.model_id in installed,
        catalog_entry=chosen_entry,
    )


def build_model_setup_options(
    *,
    catalog: list[ModelCatalogEntry] | tuple[ModelCatalogEntry, ...] | None = None,
    installed_models: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    free_disk_gb: float | None = None,
    available_ram_gb: float | None = None,
    requested_model: str | None = None,
) -> dict[str, Any]:
    """Build UI-ready model choices and recommendation metadata.

    Args:
        catalog: Optional parsed catalog. Loaded from disk when omitted.
        installed_models: Installed backend model rows.
        free_disk_gb: Current free disk in GiB.
        available_ram_gb: Current available RAM in GiB.
        requested_model: Optional user-selected model id.

    Returns:
        JSON-serializable model setup options.
    """
    entries = list(catalog or load_model_catalog())
    installed_names = installed_model_names(tuple(installed_models or ()))
    recommendation = recommend_model(
        entries,
        installed_models=tuple(installed_models or ()),
        requested_model=requested_model,
    )
    options = []
    for entry in entries:
        is_installed = entry.model_id in installed_names
        row = entry.to_public_dict()
        row["installed"] = is_installed
        row["resource_assessment"] = assess_model_resources(
            entry,
            free_disk_gb=free_disk_gb,
            available_ram_gb=available_ram_gb,
            installed=is_installed,
        )
        options.append(row)
    return {
        "schema_version": 1,
        "recommended": recommendation.to_public_dict(),
        "models": options,
        "installed_model_names": sorted(installed_names),
    }
