"""Additive resource checks for planned Bio-Harness executions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import psutil


_GIB = 1024**3
_SKILL_INDEX_PATH = Path(__file__).resolve().parents[1] / "skills" / "definitions" / "index.json"
_TEMP_DISK_GB_BY_SKILL: dict[str, float] = {
    "cellranger_count": 60.0,
    "flye_assemble": 80.0,
    "sc_count_and_cluster": 40.0,
    "spades_assemble": 40.0,
    "star_2pass_align": 30.0,
    "star_align": 20.0,
    "star_solo_count": 40.0,
    "trinity_assemble": 60.0,
}
_REFERENCE_BUILD_DISK_GB_BY_SKILL: dict[str, float] = {
    "bowtie2_align": 12.0,
    "bwa_mem_align": 10.0,
    "cellranger_count": 20.0,
    "hisat2_align": 15.0,
    "kallisto_quant": 2.0,
    "minimap2_align": 5.0,
    "salmon_quant": 6.0,
    "sc_count_and_cluster": 25.0,
    "star_2pass_align": 25.0,
    "star_align": 25.0,
    "star_solo_count": 25.0,
    "subread_align": 10.0,
}


def _load_skill_index() -> dict[str, Any]:
    payload = json.loads(_SKILL_INDEX_PATH.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _estimate_disk_requirements(selected_rows: list[dict[str, Any]], min_free_disk_gb: float) -> dict[str, Any]:
    disk_drivers: list[dict[str, Any]] = []
    estimated_temp_disk_gb = 0.0
    estimated_reference_build_disk_gb = 0.0

    for row in selected_rows:
        skill_name = str(row.get("name", "")).strip()
        if not skill_name:
            continue
        temp_disk_gb = float(_TEMP_DISK_GB_BY_SKILL.get(skill_name, 0.0))
        reference_build_disk_gb = float(_REFERENCE_BUILD_DISK_GB_BY_SKILL.get(skill_name, 0.0))
        estimated_temp_disk_gb = max(estimated_temp_disk_gb, temp_disk_gb)
        estimated_reference_build_disk_gb = max(estimated_reference_build_disk_gb, reference_build_disk_gb)
        if temp_disk_gb <= 0.0 and reference_build_disk_gb <= 0.0:
            continue
        driver: dict[str, Any] = {"skill_name": skill_name}
        if temp_disk_gb > 0.0:
            driver["estimated_temp_disk_gb"] = temp_disk_gb
        if reference_build_disk_gb > 0.0:
            driver["estimated_reference_build_disk_gb"] = reference_build_disk_gb
        disk_drivers.append(driver)

    estimated_required_free_disk_gb = max(
        float(min_free_disk_gb),
        estimated_temp_disk_gb + estimated_reference_build_disk_gb,
    )
    return {
        "estimated_temp_disk_gb": round(estimated_temp_disk_gb, 3),
        "estimated_reference_build_disk_gb": round(estimated_reference_build_disk_gb, 3),
        "estimated_required_free_disk_gb": round(estimated_required_free_disk_gb, 3),
        "drivers": disk_drivers,
    }


def assess_resource_preflight(
    skill_names: list[str],
    *,
    selected_dir: str | Path | None = None,
    min_free_disk_gb: float = 20.0,
) -> dict[str, Any]:
    """Assess whether the current machine can comfortably run selected skills.

    Args:
        skill_names: Selected skill names for the planned run.
        selected_dir: Directory whose filesystem should be checked for free
            space. Defaults to the current working directory.
        min_free_disk_gb: Baseline minimum desired free disk capacity in GiB.

    Returns:
        A machine-readable resource report with RAM, CPU, and disk estimates
        plus advisory warnings when the selected machine looks undersized.
    """
    payload = _load_skill_index()
    rows = payload.get("skills", []) if isinstance(payload.get("skills", []), list) else []
    skill_map = {
        str(row.get("name", "")).strip(): row
        for row in rows
        if isinstance(row, dict) and str(row.get("name", "")).strip()
    }
    normalized_names = [str(name).strip() for name in skill_names if str(name).strip()]
    selected_rows = [skill_map[name] for name in normalized_names if name in skill_map]
    missing_skills = [name for name in normalized_names if name not in skill_map]

    required_ram = max(
        float(row.get("system_requirements", {}).get("min_ram_gb", 0) or 0)
        for row in selected_rows
    ) if selected_rows else 0.0
    required_cores = max(
        int(row.get("system_requirements", {}).get("min_cores", 0) or 0)
        for row in selected_rows
    ) if selected_rows else 0

    available_mem_gb = psutil.virtual_memory().available / _GIB
    available_cores = int(psutil.cpu_count(logical=True) or 0)

    target_dir = Path(selected_dir).expanduser().resolve() if selected_dir is not None else Path.cwd()
    disk_usage = shutil.disk_usage(target_dir)
    free_disk_gb = disk_usage.free / _GIB
    disk_estimate = _estimate_disk_requirements(selected_rows, float(min_free_disk_gb))

    warnings: list[str] = []
    if missing_skills:
        warnings.append(f"Unknown skill metadata: {', '.join(sorted(missing_skills))}")
    if available_mem_gb < required_ram:
        warnings.append(f"available memory {available_mem_gb:.2f} GiB is below required minimum {required_ram:.2f} GiB")
    if available_cores < required_cores:
        warnings.append(f"available cores {available_cores} are below required minimum {required_cores}")
    if free_disk_gb < float(disk_estimate["estimated_required_free_disk_gb"]):
        warnings.append(
            "free disk "
            f"{free_disk_gb:.2f} GiB is below estimated workflow requirement "
            f"{float(disk_estimate['estimated_required_free_disk_gb']):.2f} GiB "
            f"(baseline {float(min_free_disk_gb):.2f} GiB, temp {float(disk_estimate['estimated_temp_disk_gb']):.2f} GiB, "
            f"reference/index {float(disk_estimate['estimated_reference_build_disk_gb']):.2f} GiB)"
        )

    return {
        "selected_dir": str(target_dir),
        "skill_names": normalized_names,
        "skills_found": [str(row.get("name", "")).strip() for row in selected_rows],
        "missing_skills": missing_skills,
        "requirements": {
            "min_ram_gb": round(required_ram, 3),
            "min_cores": required_cores,
            "min_free_disk_gb": float(min_free_disk_gb),
            "estimated_free_disk_gb": float(disk_estimate["estimated_required_free_disk_gb"]),
        },
        "system": {
            "available_mem_gb": round(available_mem_gb, 3),
            "available_cores": available_cores,
            "free_disk_gb": round(free_disk_gb, 3),
        },
        "disk_estimate": disk_estimate,
        "warnings": warnings,
        "ok": not warnings,
    }
