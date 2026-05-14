from __future__ import annotations

import shutil
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from typing import Any

from bio_harness.core.environment_bootstrap import build_tool_installation_plan
from bio_harness.core.reference_manager import (
    audit_reference_bundle,
    build_reference_materialization_plan,
)
from bio_harness.core.tool_env import requirement_available
from bio_harness.core.tool_launchers import load_tool_launchers, tool_launcher_available


def probe_llm_backend(**kwargs: Any) -> dict[str, Any]:
    probe_fn = getattr(import_module("bio_harness.core.llm_backend_probe"), "probe_llm_backend")
    return probe_fn(**kwargs)


def assess_resource_preflight(
    skill_names: list[str],
    *,
    selected_dir: str | Path | None = None,
    min_free_disk_gb: float = 20.0,
) -> dict[str, Any]:
    try:
        assess_fn = getattr(import_module("bio_harness.core.resource_preflight"), "assess_resource_preflight")
    except (ImportError, ModuleNotFoundError) as exc:
        missing_dependency = str(getattr(exc, "name", "") or "").strip()
        message = "resource preflight dependencies unavailable"
        if missing_dependency:
            message += f": missing dependency {missing_dependency}"
        target_dir = Path(selected_dir).expanduser().resolve() if selected_dir is not None else Path.cwd()
        return {
            "selected_dir": str(target_dir),
            "skill_names": [str(name).strip() for name in skill_names if str(name).strip()],
            "skills_found": [],
            "missing_skills": [],
            "requirements": {
                "min_ram_gb": 0.0,
                "min_cores": 0,
                "min_free_disk_gb": float(min_free_disk_gb),
                "estimated_free_disk_gb": float(min_free_disk_gb),
            },
            "system": {
                "available_mem_gb": 0.0,
                "available_cores": 0,
                "free_disk_gb": 0.0,
            },
            "disk_estimate": {
                "estimated_temp_disk_gb": 0.0,
                "estimated_reference_build_disk_gb": 0.0,
                "estimated_required_free_disk_gb": float(min_free_disk_gb),
                "drivers": [],
            },
            "warnings": [message],
            "ok": False,
            "exception_class": exc.__class__.__name__,
            "missing_dependency": missing_dependency,
        }
    return assess_fn(
        skill_names,
        selected_dir=selected_dir,
        min_free_disk_gb=min_free_disk_gb,
    )


def _check_command_version(argv: list[str]) -> dict[str, Any]:
    token = argv[0]
    resolved = shutil.which(token)
    if not resolved:
        return {"available": False, "path": None, "version": ""}
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return {"available": True, "path": resolved, "version": "", "error": str(exc)}
    version_text = (completed.stdout or completed.stderr or "").strip().splitlines()
    return {
        "available": True,
        "path": resolved,
        "version": version_text[0] if version_text else "",
        "returncode": completed.returncode,
    }


def assess_harness_doctor(
    *,
    skill_names: list[str] | None = None,
    tool_names: list[str] | None = None,
    selected_dir: str | Path | None = None,
    reference_root: str | Path | None = None,
    min_free_disk_gb: float = 20.0,
    probe_llm_backend_status: bool = False,
    llm_backend: str | None = None,
    model_name: str | None = None,
    host: str | None = None,
    llm_probe_text: bool = False,
    llm_probe_plan: bool = False,
) -> dict[str, Any]:
    requested_skills = [str(name).strip() for name in skill_names or [] if str(name).strip()]
    requested_tools = [str(name).strip() for name in tool_names or [] if str(name).strip()]

    install_plan = build_tool_installation_plan(
        tool_names=requested_tools,
        skill_names=requested_skills,
    )
    launchers = load_tool_launchers()
    launcher_status = {
        name: {
            "configured": True,
            "available": tool_launcher_available(name),
            "argv": list(spec.get("argv", [])) if isinstance(spec.get("argv", []), list) else [],
        }
        for name, spec in launchers.items()
    }

    tool_status = {
        tool_name: requirement_available(tool_name)
        for tool_name in install_plan["requested_tools"]
    }
    resource_report = assess_resource_preflight(
        requested_skills,
        selected_dir=selected_dir,
        min_free_disk_gb=min_free_disk_gb,
    )
    reference_audit = None
    reference_build_plan = None
    if reference_root is not None:
        reference_audit = audit_reference_bundle(reference_root)
        reference_build_plan = build_reference_materialization_plan(reference_root)
    llm_report = None
    if probe_llm_backend_status:
        llm_report = probe_llm_backend(
            llm_backend=llm_backend,
            model_name=model_name,
            host=host,
            probe_text=llm_probe_text,
            probe_plan=llm_probe_plan,
        )
    command_status = {
        "pixi": _check_command_version(["pixi", "--version"]),
        "docker": _check_command_version(["docker", "--version"]),
    }

    warnings: list[str] = []
    if not resource_report.get("ok", False):
        warnings.extend(resource_report.get("warnings", []))
    if install_plan["manual_install_required_tools"]:
        warnings.append(
            "manual install still required for: "
            + ", ".join(install_plan["manual_install_required_tools"])
        )
    if install_plan["pixi_installable_missing_tools"]:
        pixi_available = bool(command_status["pixi"].get("available", False))
        if not pixi_available:
            warnings.append(
                "pixi installable tools missing but pixi is unavailable on PATH: "
                + ", ".join(install_plan["pixi_installable_missing_tools"])
            )
        else:
            warnings.append(
                "pixi installable tools missing: "
                + ", ".join(install_plan["pixi_installable_missing_tools"])
            )
    if install_plan["isolated_recipe_missing_tools"]:
        warnings.append(
            "isolated launcher setup available for: "
            + ", ".join(install_plan["isolated_recipe_missing_tools"])
        )
    if reference_build_plan and reference_build_plan.get("pending_targets"):
        warnings.append(
            "reference assets can be materialized for: "
            + ", ".join(reference_build_plan["pending_targets"])
        )
    if llm_report is not None and not bool(llm_report.get("available", False)):
        warnings.append(
            "llm backend unavailable: "
            + str(llm_report.get("message", "") or "backend probe failed")
        )

    readiness = (
        not install_plan["manual_install_required_tools"]
        and resource_report.get("ok", False)
        and (llm_report is None or bool(llm_report.get("available", False)))
    )

    return {
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "commands": command_status,
        "install_plan": install_plan,
        "tool_status": tool_status,
        "tool_launchers": launcher_status,
        "resource_preflight": resource_report,
        "reference_audit": reference_audit,
        "reference_materialization_plan": reference_build_plan,
        "llm_backend": llm_report,
        "warnings": warnings,
        "ready": readiness and not bool(warnings),
    }
